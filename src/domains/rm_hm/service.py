# src/domains/rm_hm/service.py

import os
import pandas as pd
from datetime import datetime
from infrastructure.influx_client import InfluxClient

OUTPUT_DIR = r"C:\dev\offline_data_automation\output"


class RMHMService:
    """
    Handles RM & HM combined sheet processing (e.g. SP-02).
    """

    def __init__(self, logger):
        self.logger = logger

    def process(
        self,
        rm_hm_file: str,
        setting_cfg: dict,
        run_dates: list[str],
    ) -> pd.DataFrame | None:

        rm_hm_cfg = setting_cfg.get("rm_hm", {})
        field_map = setting_cfg.get("rm_hm_fields", {})
        influx_cfg = setting_cfg.get("influxdb")

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
            self.logger.error(f"'date' column not found in RM & HM sheet. Columns: {list(df.columns)}")
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
        # FORWARD FILL LOGIC (LEGACY)
        # ----------------------------
        df[target_cols] = df[target_cols].ffill()
        df[target_cols] = df[target_cols].infer_objects(copy=False)


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

        # ----------------------------
        # FORMAT DATE (KEEP DATETIME)
        # ----------------------------
        filtered["date"] = pd.to_datetime(filtered["date"])

        # ----------------------------
        # RENAME FIELDS (BUSINESS NAMES)
        # ----------------------------
        if field_map:
            filtered = filtered.rename(columns=field_map)

        # ----------------------------
        # WRITE EXCEL (OPTIONAL)
        # ----------------------------
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        out_path = os.path.join(OUTPUT_DIR, "combined_rm_hm_data.xlsx")
        filtered.to_excel(out_path, index=False)

        self.logger.info(f"RM & HM output written → {out_path}")

        # ----------------------------
        # PUSH TO INFLUXDB
        # ----------------------------
        if not influx_cfg:
            self.logger.warning("Influx config missing — skipping RM & HM Influx push")
            return filtered

        influx = InfluxClient(influx_cfg)
        try:
            influx.write_dataframe(
                df=filtered,
                measurement="rm_hm_data",
                field_mapping=field_map,
                tag_keys=[],  # no tags for RM & HM
            )
            self.logger.info("RM & HM data pushed to InfluxDB successfully")
        finally:
            influx.close()

        return filtered
