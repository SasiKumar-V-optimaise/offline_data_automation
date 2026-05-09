import pandas as pd


class RawChargePostgreSQLProcessor:
    def process_wide_with_time(self, raw_df, snapshots):
        raw_df = raw_df.sort_values("DATETIME")
        snapshots = sorted(snapshots, key=lambda x: x["ts"])

        out = raw_df.copy()

        # initialize material columns
        for hopper_no in range(1, 20):
            out[f"hopper_{hopper_no}_material"] = None

        snap_idx = 0

        for i, row in out.iterrows():
            dt = row["DATETIME"]

            while (
                snap_idx + 1 < len(snapshots)
                and snapshots[snap_idx + 1]["ts"] <= dt
            ):
                snap_idx += 1

            mapping = snapshots[snap_idx]["mapping"]

            for hopper_no in range(1, 20):
                out.at[i, f"hopper_{hopper_no}_material"] = mapping.get(hopper_no)

        final = pd.DataFrame()
        final["Date"] = out["DATETIME"]

        if "CHARGE_NO" in out.columns:
            final["charge_no"] = out["CHARGE_NO"]

        for hopper_no in range(1, 20):
            act_col = f"HOPPER_{hopper_no}_ACT"
            mat_col = f"hopper_{hopper_no}_material"
            val_col = f"hopper_{hopper_no}_value"

            final[mat_col] = out.get(mat_col)
            final[val_col] = pd.to_numeric(out.get(act_col), errors="coerce")

        return final

    def to_charge_data_table(
        self,
        wide_df: pd.DataFrame,
        material_column_map: dict,
        table_columns: list,
    ) -> pd.DataFrame:

        out = pd.DataFrame()
        out["date_time"] = pd.to_datetime(wide_df["Date"], errors="coerce")

        for col in table_columns:
            if col not in ("date_time", "charge_no"):
                out[col] = 0.0

        out["charge_no"] = wide_df.get("charge_no")

        normalized_map = {
            str(k).strip().lower(): v
            for k, v in (material_column_map or {}).items()
        }

        unmapped = set()

        for hopper_no in range(1, 20):
            mat_col = f"hopper_{hopper_no}_material"
            val_col = f"hopper_{hopper_no}_value"

            if mat_col not in wide_df.columns or val_col not in wide_df.columns:
                continue

            materials = wide_df[mat_col].astype(str).str.strip().str.lower()
            values = pd.to_numeric(wide_df[val_col], errors="coerce")

            for idx in wide_df.index:
                material = materials.iloc[idx]
                value_kg = values.iloc[idx]

                if not material or pd.isna(value_kg) or value_kg == 0:
                    continue

                db_col = normalized_map.get(material)

                if not db_col:
                    unmapped.add(material)
                    continue

                if db_col not in out.columns:
                    unmapped.add(f"{material} -> {db_col} (missing column)")
                    continue

                out.at[idx, db_col] += float(value_kg) / 1000.0  # kg → MT

        if unmapped:
            print("\n Unmapped charge materials:")
            for x in sorted(unmapped):
                print(" -", x)

        return out[table_columns]