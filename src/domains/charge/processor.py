import pandas as pd


HOPPERS = range(1, 20)


class RawChargeProcessor:
    DATA_COLUMNS = [
        "sinter_1_mt", "sinter_2_mt", "sinter_3_mt", "sinter_4_mt",
        "pellet_1_mt", "pellet_2_mt",
        "ore_1_mt", "ore_2_mt", "ore_3_mt", "ore_4_mt", "ore_5_mt", "ore_6_mt",
        "ore_7_mt", "ore_8_mt", "ore_9_mt", "ore_10_mt", "ore_11_mt", "ore_12_mt",
        "flux_1_mt", "flux_2_mt", "flux_3_mt",
        "coke_1_mt", "coke_2_mt", "nut_coke_1_mt", "nut_coke_2_mt", "pci_mt",
    ]
    DB_COLUMNS = ["date_time", "charge_no", *DATA_COLUMNS, "import_batch_id", "source_row_number"]

    def process_wide_with_time(self, raw_df, snapshots):
        raw = raw_df.sort_values("DATETIME").reset_index(drop=True)
        snapshot_df = self._snapshot_frame(snapshots)
        out = pd.merge_asof(
            raw,
            snapshot_df,
            left_on="DATETIME",
            right_on="snapshot_ts",
            direction="backward",
        )

        source_rows = out["SOURCE_ROW_NUMBER"] if "SOURCE_ROW_NUMBER" in out.columns else out.index + 1
        final = pd.DataFrame({"Date": out["DATETIME"], "source_row_number": source_rows})
        final["charge_no"] = out["CHARGE_NO"] if "CHARGE_NO" in out.columns else None

        for hopper_no in HOPPERS:
            final[f"hopper_{hopper_no}_material_code"] = out.get(f"hopper_{hopper_no}_material_code")
            final[f"hopper_{hopper_no}_material"] = out.get(f"hopper_{hopper_no}_material")
            final[f"hopper_{hopper_no}_value"] = out.get(f"HOPPER_{hopper_no}_ACT")
        return final

    def to_charge_data_table(
        self,
        wide_df: pd.DataFrame,
        material_column_overrides: dict | None = None,
        import_batch_id: str | None = None,
    ) -> pd.DataFrame:
        out = pd.DataFrame({"date_time": pd.to_datetime(wide_df["Date"], errors="coerce")})
        out["charge_no"] = wide_df["charge_no"] if "charge_no" in wide_df.columns else None
        out[self.DATA_COLUMNS] = 0.0

        overrides = {
            self._normalize_code(code): str(column).strip()
            for code, column in (material_column_overrides or {}).items()
            if self._normalize_code(code)
        }
        unmapped = set()

        for hopper_no in HOPPERS:
            code_col = f"hopper_{hopper_no}_material_code"
            val_col = f"hopper_{hopper_no}_value"
            if code_col not in wide_df.columns or val_col not in wide_df.columns:
                continue

            codes = wide_df[code_col].map(self._normalize_code)
            values_mt = pd.to_numeric(wide_df[val_col], errors="coerce").fillna(0.0) / 1000.0
            used_codes = sorted(codes[values_mt.ne(0) & codes.notna()].unique())

            for code in used_codes:
                db_col = self._db_column_for_code(code, overrides)
                if db_col not in self.DATA_COLUMNS:
                    unmapped.add(code)
                    continue
                mask = codes.eq(code) & values_mt.ne(0)
                out.loc[mask, db_col] = out.loc[mask, db_col].add(values_mt[mask], fill_value=0)

        out["import_batch_id"] = import_batch_id
        out["source_row_number"] = wide_df.get("source_row_number")

        if unmapped:
            print("Unmapped charge materials:")
            for x in sorted(unmapped):
                print(" -", x)

        return out[self.DB_COLUMNS]

    @staticmethod
    def _snapshot_frame(snapshots) -> pd.DataFrame:
        rows = []
        for snapshot in sorted(snapshots, key=lambda x: x["ts"]):
            row = {"snapshot_ts": snapshot["ts"]}
            for hopper_no in HOPPERS:
                row[f"hopper_{hopper_no}_material_code"] = snapshot["codes"].get(hopper_no)
                row[f"hopper_{hopper_no}_material"] = snapshot["names"].get(hopper_no)
            rows.append(row)
        return pd.DataFrame(rows).sort_values("snapshot_ts")

    @staticmethod
    def _normalize_code(value) -> str | None:
        if pd.isna(value):
            return None
        code = str(value).strip().lower()
        return code or None

    @staticmethod
    def _db_column_for_code(code: str, overrides: dict[str, str]) -> str:
        return overrides.get(code) or f"{code}_mt"
