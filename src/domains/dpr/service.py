# src/domains/dpr/service.py

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Any, Tuple

import pandas as pd

from domains.dpr.reader import DPRReader
from domains.dpr.config_updater import DPRConfigUpdater
from infrastructure.neon_client import NeonClient


OUTPUT_DIR = r"C:\dev\offline_data_automation\output"


@dataclass
class DPRService:
    logger: any

    def __post_init__(self):
        self.reader = DPRReader(self.logger)
        self.updater = DPRConfigUpdater(self.logger)

    def process(
        self,
        dpr_file: str,
        setting_cfg: Dict[str, Any],
        run_dates: List[str],
    ) -> None:

        dpr_cfg = setting_cfg["dpr"]
        field_mapping = dpr_cfg.get("dpr_fields", {})
        influx_cfg = setting_cfg.get("influxdb")

        config_cache: Dict[Tuple[int, int], Dict[str, Any]] = {}

        for run_date in run_dates:
            self.logger.info(f"Processing DPR for {run_date}")
            run_dt = datetime.strptime(run_date, "%d-%b-%Y")
            key = (run_dt.month, run_dt.year)

            # update rows only once per month
            if key not in config_cache:
                self.logger.info(f"Updating DPR config for {run_date}")
                dpr_cfg = self.updater.update_rows_in_config(dpr_file, dpr_cfg, run_date)
                config_cache[key] = dpr_cfg

            df = self.reader.read_for_date(dpr_file, dpr_cfg, run_date)

            if df is None or df.empty:
                self.logger.warning(f"No DPR data for {run_date}")
                continue

            # rename fields BEFORE writing/pushing
            if field_mapping:
                df = df.rename(columns=field_mapping)
            if df.columns.duplicated().any():
                self.logger.warning("Duplicate columns found → removing duplicates")
                df = df.loc[:, ~df.columns.duplicated()]
            required_cols = list(field_mapping.values())
            if "date" not in df.columns:
                raise ValueError("Missing 'date' column after renaming DPR fields.")
            for col in required_cols:
                if col not in df.columns:
                    df[col] = None
                    self.logger.warning(f"Column '{col}' missing → filled with NULL")

            # now safe selection
            df = df[["date"] + required_cols]

            # write excel
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            out_path = os.path.join(OUTPUT_DIR, "combined_dpr_data.xlsx")
            df.to_excel(out_path, index=False)
            self.logger.info(f"DPR output written → {out_path}")

            # push to influx
            

            neon = NeonClient(setting_cfg["neondb"])

            try:
                neon.insert_dataframe(
                    df=df,
                    table_name="dpr_data",
                    conflict_cols=["date"],   # ✅ important
                )
                self.logger.info(f"DPR data pushed to NeonDB for {run_date}")
            finally:
                neon.close()
