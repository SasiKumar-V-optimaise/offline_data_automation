# src/domains/rm/service.py

import os
import pandas as pd
from datetime import datetime
from typing import List
from infrastructure.influx_client import InfluxClient

from domains.rm.reader import RMReader
from domains.rm.transformer import RMTransformer

OUTPUT_DIR = r"C:\dev\offline_data_automation\output"


class RMService:
    # Explicit shift priority (legacy behavior)
    SHIFT_PRIORITY = {"C": 0, "A": 1, "B": 2}
    SHIFT_TIME = {"A": "07:00", "B": "15:00", "C": "23:00"}

    def __init__(self, logger):
        self.logger = logger
        self.reader = RMReader(logger)
        self.transformer = RMTransformer(logger)

    def process(
        self,
        rm_file: str,
        setting_cfg: dict,
        run_dates: List[str],
    ) -> pd.DataFrame:

        rm_cfg = setting_cfg["rm"]
        rename_map = rm_cfg.get("rename_fields", {})

        date_list = [datetime.strptime(d, "%d-%b-%Y").date() for d in run_dates]

        self.logger.info("RM processing started")

        frames = self.reader.read(rm_file, rm_cfg["sheet_config"])
        parts = []

        # ------------------------------------------------
        # PER-SHEET PROCESSING
        # ------------------------------------------------
        for df, prefix, sheet in frames:
            self.logger.info(f"→ {sheet}")

            df = self.transformer.normalize_columns(df)
            df = self.transformer.filter_by_date_and_shift(df, date_list, sheet)

            if df is None or df.empty:
                self.logger.warning(f"   SKIPPED: {sheet} (no valid data)")
                continue

            if "ONLINE/OFFLINE" in df.columns:
                df = self.transformer.split_online_offline_and_merge(df)

            if {"DATE", "SHIFT"}.issubset(df.columns):
                counts = df.groupby(["DATE", "SHIFT"]).size()
                if any(counts > 1):
                    df = self.transformer.average_shift_blocks(df)

            # Create MERGE_KEY before prefixing
            df = df.copy()
            df["MERGE_KEY"] = df["DATE"].astype(str) + "_" + df["SHIFT"]

            # Prefix everything except MERGE_KEY
            df = df.rename(
                columns={c: f"{prefix}{c}" for c in df.columns if c != "MERGE_KEY"}
            )

            parts.append(df)
            self.logger.info(f"   OK: {sheet}")

        if not parts:
            self.logger.error("No RM data produced — exiting")
            return pd.DataFrame()

        # ------------------------------------------------
        # ALIGN ALL SHEETS BY MERGE_KEY
        # ------------------------------------------------
        combined = parts[0]
        for df in parts[1:]:
            combined = combined.merge(
                df,
                on="MERGE_KEY",
                how="outer",
                suffixes=("", "_dup"),
            )

        combined = combined.loc[:, ~combined.columns.str.endswith("_dup")]

        # ------------------------------------------------
        # RENAME FIELDS (BEFORE EXCEL WRITE)
        # ------------------------------------------------
        if rename_map:
            combined = combined.rename(columns=rename_map)
            self.logger.info("RM fields renamed using rm.yaml mapping")
        
        combined.to_excel(os.path.join(OUTPUT_DIR, "rm_combined_raw.xlsx"), index=False)  # Debug output

        # ------------------------------------------------
        # FIX SHIFT ORDER (C → A → B)
        # ------------------------------------------------
        combined["SHIFT"] = combined["MERGE_KEY"].str.split("_").str[-1]
        combined["SHIFT_ORDER"] = combined["SHIFT"].map(self.SHIFT_PRIORITY)

        combined = combined.sort_values("SHIFT_ORDER").reset_index(drop=True)

        # ------------------------------------------------
        # BUILD FINAL DATETIME (LEGACY LOGIC)
        # ------------------------------------------------
        date_col = next(c for c in combined.columns if c.upper().endswith("_DATE"))

        combined["Date"] = pd.to_datetime(combined[date_col], errors="coerce")

        combined.drop(
            columns=[c for c in combined.columns if c.upper().endswith("_DATE")],
            inplace=True,
            errors="ignore"
        )

        combined["Date"] = pd.to_datetime(
            combined["Date"].dt.strftime("%Y-%m-%d")
            + " "
            + combined["SHIFT"].map(self.SHIFT_TIME)
        )

        # C-shift belongs to previous day
        combined.loc[combined["SHIFT"] == "C", "Date"] -= pd.Timedelta(days=1)

        combined.drop(columns=["SHIFT", "SHIFT_ORDER", "MERGE_KEY"], inplace=True)
        combined = combined.rename(columns={"Date": "date"})
        # ------------------------------------------------
        # WRITE OUTPUT
        # ------------------------------------------------
        if rename_map:
            allowed_columns = list(rename_map.values())

            # keep only existing columns
            allowed_columns = [c for c in allowed_columns if c in combined.columns]

            combined = combined[allowed_columns]

            self.logger.info(
                f"Final RM fields filtered: keeping only {len(allowed_columns)} columns"
            )
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        out_path = os.path.join(OUTPUT_DIR, "rm_processed_data.xlsx")
        combined.to_excel(out_path, index=False)

        self.logger.info(f"RM output written → {out_path}")
        self.logger.info("RM processing completed successfully")
        # ------------------------------------------------
        # PUSH TO INFLUXDB
        # ------------------------------------------------
        influx_cfg = setting_cfg.get("influxdb")

        if not influx_cfg:
            self.logger.warning("InfluxDB config missing — skipping Influx push")
        else:
            try:
                influx = InfluxClient(influx_cfg)

                influx.write_dataframe(
                    df=combined,
                    measurement="rm_updated_data",
                    field_mapping=rm_cfg.get("rename_fields", {}),
                    tag_keys=[],   # add tags later if needed
                )

                influx.close()
                self.logger.info("RM data pushed to InfluxDB successfully")

            except Exception as exc:
                self.logger.error(f"Failed to push RM data to InfluxDB: {exc}")


        return combined
