# src/domains/rm_hm/service.py

import os
import pandas as pd
from datetime import datetime
from infrastructure.neon_client import NeonClient

OUTPUT_DIR = "output/rm_hm"


class RMHMService:
    """
    Handles RM & HM combined sheet processing (e.g. SP-02).
    """

    def __init__(self, logger, neon_cfg: dict | None = None, write_to_neon: bool = False):
        self.logger = logger
        self.neon_cfg = neon_cfg
        self.write_to_neon = write_to_neon

    def process(
        self,
        rm_hm_file: str,
        setting_cfg: dict,
        run_dates: list[str],
    ) -> pd.DataFrame | None:

        rm_hm_cfg = setting_cfg.get("rm_hm", {})
        field_map = setting_cfg.get("rm_hm_fields", {})

        sheet_name = rm_hm_cfg.get("sheet_name", "SP-02")

        self.logger.info(f"Using RM & HM sheet: {sheet_name}")

        # ----------------------------
        # READ SHEET
        # ----------------------------
        df = pd.read_excel(
            rm_hm_file,
            sheet_name=sheet_name,
            header=0,
        )

        if df.empty:
            self.logger.warning("RM & HM sheet is empty")
            return None

        # ----------------------------
        # NORMALIZE COLUMNS
        # ----------------------------
        df.columns = (
            df.columns.astype(str)
            .str.strip()
            .str.replace(r"\s+", "_", regex=True)
            .str.replace(r"[^0-9a-zA-Z_]", "", regex=True)
            .str.lower()
        )

        df = df.dropna(how="all")

        # ----------------------------
        # STANDARDIZE DATE COLUMN
        # ----------------------------
        date_col = None
        for col in df.columns:
            if col.lower() in {"date", "dates", "dt"}:
                date_col = col
                break

        if not date_col:
            self.logger.error(f"'date' column not found. Columns: {list(df.columns)}")
            return None

        df = df.rename(columns={date_col: "date"})
        df["date"] = pd.to_datetime(df["date"], errors="coerce")

        df = df[df["date"].notna()]
        df = df.sort_values("date").reset_index(drop=True)

        # ----------------------------
        # ENSURE KEY COLUMNS EXIST
        # ----------------------------
        target_cols = ["ai", "ti", "rdi", "ri"]
        for col in target_cols:
            if col not in df.columns:
                self.logger.warning(f"Column '{col}' missing — creating empty column")
                df[col] = pd.NA

        # ----------------------------
        # FORWARD FILL
        # ----------------------------
        df[target_cols] = df[target_cols].ffill().infer_objects(copy=False)

        # ----------------------------
        # FILTER BY RUN DATES
        # ----------------------------
        run_dt_list = [
            datetime.strptime(d, "%d-%b-%Y").date()
            for d in run_dates
        ]

        filtered = df[df["date"].dt.date.isin(run_dt_list)].copy()

        if filtered.empty:
            self.logger.warning("No RM & HM data found for requested dates")
            return None

        filtered["date"] = pd.to_datetime(filtered["date"])

        # ----------------------------
        # RENAME + SELECT COLUMNS
        # ----------------------------
        cols_to_keep = []  # ✅ FIX: always initialize

        if field_map:
            filtered = filtered.rename(columns=field_map)

            cols_to_keep = list(field_map.values())

        # always include date
        if "date" in filtered.columns:
            cols_to_keep.append("date")

        # remove duplicates
        cols_to_keep = list(set(cols_to_keep))

        # keep only existing columns
        cols_to_keep = [c for c in cols_to_keep if c in filtered.columns]

        filtered = filtered[cols_to_keep]

        # ----------------------------
        # REQUIRED FOR NEON (date_time)
        # ----------------------------
        if "date" in filtered.columns:
            filtered = filtered.rename(columns={"date": "date_time"})

        # ----------------------------
        # WRITE EXCEL
        # ----------------------------
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        out_path = os.path.join(OUTPUT_DIR, "combined_rm_hm_data.xlsx")
        filtered.to_excel(out_path, index=False)

        self.logger.info(f"RM & HM output written → {out_path}")

        # ----------------------------
        # WRITE TO NEON DB
        # ----------------------------
        if self.write_to_neon and self.neon_cfg:
            try:
                client = NeonClient(self.neon_cfg)

                rows = client.insert_dataframe(
                    df=filtered,
                    table_name="offline_feed.raw_material_strength_analysis",
                    conflict_cols=["date_time"],
                    upsert_mode="delete_insert",
                )

                self.logger.info(f"Inserted {rows} rows → offline_feed.raw_material_strength_analysis")

                client.close()

            except Exception as e:
                self.logger.error(f"Failed to write RM_HM data to Neon: {e}")

        return filtered