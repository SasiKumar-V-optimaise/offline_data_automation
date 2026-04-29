import re
import pandas as pd


class RawChargeProcessor:
    def process_wide_with_time(self, raw_df, snapshots):
        raw_df = raw_df.sort_values("DATETIME")

        snapshots = sorted(snapshots, key=lambda x: x["ts"])

        out = raw_df.copy()

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

        # now attach values
        final = pd.DataFrame()
        final["Date"] = out["DATETIME"]

        if "CHARGE_NO" in out.columns:
            final["charge_no"] = out["CHARGE_NO"]

        for hopper_no in range(1, 20):
            act_col = f"HOPPER_{hopper_no}_ACT"
            mat_col = f"hopper_{hopper_no}_material"
            val_col = f"hopper_{hopper_no}_value"

            # material name
            final[mat_col] = out[mat_col]

            # numeric value
            final[val_col] = out[act_col]

        return final
    def to_charge_data_table(self, wide_df: pd.DataFrame, material_column_map: dict) -> pd.DataFrame:
        table_cols = [
            "date_time", "charge_no",
            "ore_1_mt", "ore_2_mt", "ore_3_mt", "ore_4_mt", "ore_5_mt", "ore_6_mt",
            "ore_7_mt", "ore_8_mt", "ore_9_mt", "ore_10_mt", "ore_11_mt", "ore_12_mt",
            "flux_1_mt", "flux_2_mt", "flux_3_mt",
            "coke_2_mt", "coke_1_mt", "nut_coke_1_mt",
            "pci_mt",
             "sinter_3_mt",
            "pellet_1_mt", "pellet_2_mt",
        ]

        out = pd.DataFrame()
        out["date_time"] = pd.to_datetime(wide_df["Date"])

        # initialize all numeric columns FIRST
        for col in table_cols:
            if col not in ("date_time", "charge_no"):
                out[col] = 0.0

        # THEN assign charge_no (after init)
        if "charge_no" in wide_df.columns:
            out["charge_no"] = wide_df["charge_no"]
        else:
            out["charge_no"] = None

        normalized_map = {
            str(k).strip().lower(): v
            for k, v in material_column_map.items()
        }

        unmapped = set()

        for hopper_no in range(1, 20):
            mat_col = f"hopper_{hopper_no}_material"
            val_col = f"hopper_{hopper_no}_value"

            if mat_col not in wide_df.columns or val_col not in wide_df.columns:
                continue

            for idx, row in wide_df.iterrows():
                material = str(row[mat_col]).strip()
                value_kg = pd.to_numeric(row[val_col], errors="coerce")

                if not material or pd.isna(value_kg) or value_kg == 0:
                    continue

                db_col = normalized_map.get(material.lower())

                if not db_col:
                    unmapped.add(material)
                    continue

                if db_col not in out.columns:
                    unmapped.add(f"{material} -> {db_col} column missing")
                    continue

                out.at[idx, db_col] += float(value_kg) / 1000.0

        if unmapped:
            print("Unmapped charge materials:")
            for x in sorted(unmapped):
                print(" -", x)

        return out[table_cols]
    






















    