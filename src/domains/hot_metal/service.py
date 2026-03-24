# src/domains/hot_metal/service.py

import os
import pandas as pd
from infrastructure.influx_client import InfluxClient

from domains.hot_metal.reader import HotMetalReader
from domains.hot_metal.config_updater import HotMetalConfigUpdater

OUTPUT_DIR = r"C:\dev\offline_data_automation\output"


class HotMetalService:
    def __init__(self, logger):
        self.logger = logger
        self.reader = HotMetalReader(logger)
        self.updater = HotMetalConfigUpdater(logger)

    def process(self, hm_file: str, setting_cfg: dict, run_dates):
        hm_cfg = setting_cfg["hot_metal"]
        influx_cfg = setting_cfg.get("influxdb")
        field_map = hm_cfg.get("hot_metal_fields", {})

        for run_date in run_dates:
            self.logger.info(f"Processing HOT_METAL for {run_date}")

            # Update config (sheet selection)
            hm_cfg = self.updater.update_from_excel(hm_file, hm_cfg, run_date)

            # Read data
            df = self.reader.read_for_dates(hm_file, [run_date], hm_cfg)

            if df is None or df.empty:
                self.logger.warning(f"No HOT_METAL data for {run_date}")
                continue

            # 🔥 IMPORTANT FIX:
            # Drop raw DATE column BEFORE renaming to avoid duplicate `date`
            if "DATE" in df.columns:
                df = df.drop(columns=["DATE"])

            # Rename fields (DATE -> date happens here safely)
            df = df.rename(columns=field_map)
            df = df.loc[:, ~df.columns.duplicated()]

            # 🔥 DO NOT re-parse date — already datetime from reader
            # df["date"] = pd.to_datetime(df["date"])  ❌ REMOVED

            # Convert tag columns to string
            for col in ["lab_sample_id", "cast_no_ladle_spec"]:
                if col in df.columns:
                    df[col] = df[col].astype(str).fillna("")

            # Write Excel
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            out_path = os.path.join(OUTPUT_DIR, "combined_hot_data.xlsx")
            df.to_excel(out_path, index=False)
            self.logger.info(f"HOT_METAL output written → {out_path}")

            # Push to InfluxDB
            if not influx_cfg:
                self.logger.warning("Influx config missing — skipping Influx push")
                continue

            influx = InfluxClient(influx_cfg)
            try:
                influx.write_dataframe(
                    df=df,
                    measurement="hotmetal_slag_updated_data",
                    field_mapping=field_map,
                    tag_keys=["lab_sample_id", "cast_no_ladle_spec"],
                )
                self.logger.info(f"HOT_METAL {run_date} pushed to InfluxDB")
            finally:
                influx.close()
