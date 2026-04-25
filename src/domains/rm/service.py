# src/domains/rm/service.py

import os
import pandas as pd
from datetime import datetime
from typing import List
from infrastructure.influx_client import InfluxClient

from domains.rm.reader import RMReader
from domains.rm.transformer import RMTransformer
from infrastructure.neon_client import NeonClient
from domains.rm.neon_mapper import RMNeonMapper
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

            # Filter out rows with invalid markers like 'STOP'
            df = self.transformer.filter_invalid_markers(df)

            if df is None or df.empty:
                self.logger.warning(f"   SKIPPED: {sheet} (all rows filtered as invalid)")
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
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        out_path = os.path.join(OUTPUT_DIR, "rm_processed_data.xlsx")
        combined.to_excel(out_path, index=False)

        self.logger.info(f"RM output written → {out_path}")
        self.logger.info("RM processing completed successfully")
        # ------------------------------------------------
        # PUSH TO NEON DB (CORRECT WAY)
        # ------------------------------------------------
        neon_cfg = setting_cfg.get("neondb")

        if neon_cfg:
            self.logger.info("Pushing RM data to Neon DB...")

            neon_client = NeonClient(neon_cfg)

            try:
                # 1. Fetch material lookup from DB
                material_lookup = neon_client.fetch_material_lookup()

                # 2. Initialize mapper with lookup
                mapper = RMNeonMapper(material_lookup, logger=self.logger)

                # 3. Convert combined DF → per-table DFs
                table_dfs = mapper.iter_table_dfs(combined)

                # 4. Insert each table separately
                for table_name, df in table_dfs:
                    try:
                        rows = neon_client.insert_dataframe(
                            df=df,
                            table_name=table_name,
                            conflict_cols=["material_id", "date_time"],
                        )
                        self.logger.info(f"    {table_name}: {rows} rows inserted")

                    except Exception as e:
                        self.logger.error(f"    Failed for {table_name}: {e}")

            finally:
                neon_client.close()
