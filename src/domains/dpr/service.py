# src/domains/dpr/service.py

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Any, Tuple

import pandas as pd

from domains.dpr.reader import DPRReader
from domains.dpr.config_updater import DPRConfigUpdater
from infrastructure.influx_client import InfluxClient
from core.logging import get_logger, LogTemplates

logger = get_logger(__name__)


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
            logger.info(f"START | mode=dpr date={run_date}")
            run_dt = datetime.strptime(run_date, "%d-%b-%Y")
            key = (run_dt.month, run_dt.year)

            # update rows only once per month
            if key not in config_cache:
                logger.info(f"CONFIG | updating_dpr={run_date}")
                dpr_cfg = self.updater.update_rows_in_config(dpr_file, dpr_cfg, run_date)
                config_cache[key] = dpr_cfg

            df = self.reader.read_for_date(dpr_file, dpr_cfg, run_date)

            if df is None or df.empty:
                logger.warning(LogTemplates.skipped(f"no_data={run_date}"))
                continue

            # rename fields BEFORE writing/pushing
            if field_mapping:
                df = df.rename(columns=field_mapping)

            if "date" not in df.columns:
                raise ValueError("Missing 'date' column after renaming DPR fields.")

            # write excel
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            out_path = os.path.join(OUTPUT_DIR, "combined_dpr_data.xlsx")
            df.to_excel(out_path, index=False)
            logger.info(f"OUTPUT | file={out_path}")

            # push to influx
            if not influx_cfg:
                logger.warning(LogTemplates.skipped("no_influx_config"))
                continue

            influx = InfluxClient(influx_cfg)
            try:
                influx.write_dataframe(
                    df=df,
                    measurement="dpr_data",
                    field_mapping=field_mapping,
                    tag_keys=[],
                )
                logger.info(LogTemplates.db_inserted(len(df)))
            finally:
                influx.close()
