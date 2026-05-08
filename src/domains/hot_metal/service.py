# src/domains/hot_metal/service.py

import os
import pandas as pd
from infrastructure.neon_client import NeonClient

from domains.hot_metal.reader import HotMetalReader
from domains.hot_metal.config_updater import HotMetalConfigUpdater
import pytz

ist = pytz.timezone("Asia/Kolkata")

OUTPUT_DIR = "output/hot_metal"


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

            # Update config
            hm_cfg = self.updater.update_from_excel(hm_file, hm_cfg, run_date)

            # Read data
            df = self.reader.read_for_dates(hm_file, [run_date], hm_cfg)

            if df is None or df.empty:
                self.logger.warning(f"No HOT_METAL data for {run_date}")
                continue

            # Drop raw DATE column BEFORE renaming to avoid duplicate `date`
            if "DATE" in df.columns:
                df = df.drop(columns=["DATE"])

            # Rename fields (DATE -> date happens here safely)
            df = df.rename(columns=field_map)
            df = df.loc[:, ~df.columns.duplicated()]
            allowed_cols = list(field_map.values())
            df = df[[col for col in allowed_cols if col in df.columns]]

            # df["date"] = pd.to_datetime(df["date"])  

            # Convert tag columns to string
            for col in ["lab_sample_id", "cast_no_ladle_spec"]:
                if col in df.columns:
                    df[col] = df[col].astype(str).fillna("")

            # Write Excel
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            out_path = os.path.join(OUTPUT_DIR, "combined_hot_data.xlsx")
            df.to_excel(out_path, index=False)
            self.logger.info(f"HOT_METAL output written → {out_path}")
            df["date"] = pd.to_datetime(df["date"])
            df["date"] = df["date"].dt.tz_localize(ist)
            # --- CLEAN NUMERIC COLUMNS ---
            exclude_cols = ["lab_sample_id", "cast_no_ladle_spec", "date"]

            for col in df.columns:
                if col in exclude_cols:
                    continue

                # Replace junk values
                df[col] = df[col].replace(
                    ["*", "NA", "na", "--", ""],
                    None
                )

                # Convert to numeric safely
                df[col] = pd.to_numeric(df[col], errors="coerce")

            neon_cfg = setting_cfg.get("neon_developer")

            if not neon_cfg:
                self.logger.warning("Neon developer config missing — skipping DB insert")
                continue

            # New target: offline_feed.hot_metal_slag_analysis (date column → date_time)
            db_df = df.rename(columns={"date": "date_time"})

            neon = NeonClient(neon_cfg)

            try:
                rows = neon.insert_dataframe(
                    df=db_df,
                    table_name="offline_feed.hot_metal_slag_analysis",
                    conflict_cols=["lab_sample_id", "date_time"],
                    upsert_mode="delete_insert",
                )
                self.logger.info(f"HOT_METAL {run_date}: {rows} rows synced → offline_feed.hot_metal_slag_analysis")
            finally:
                neon.close()
