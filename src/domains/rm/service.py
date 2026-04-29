# src/domains/rm/service.py

import os
import pandas as pd
from datetime import datetime
from typing import List

from domains.rm.reader import RMReader
from domains.rm.transformer import RMTransformer
from infrastructure.neon_client import NeonClient
from domains.rm.neon_mapper import RMNeonMapper


class RMService:
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

        # ── Config from YAML ────────────────────────────────────────────
        shifts_cfg = rm_cfg.get("shifts", {})
        shift_priority = shifts_cfg.get("priority", {"C": 0, "A": 1, "B": 2})
        shift_time = shifts_cfg.get("time", {"A": "07:00", "B": "15:00", "C": "23:00"})
        invalid_markers = rm_cfg.get("invalid_markers")

        output_cfg = rm_cfg.get("output", {})
        output_dir = output_cfg.get("dir", r"C:\dev\offline_data_automation\output")
        output_filename = output_cfg.get("filename", "rm_processed_data.xlsx")

        run_date_fmt = rm_cfg.get("run_date_format", "%d-%b-%Y")
        date_list = [datetime.strptime(d, run_date_fmt).date() for d in run_dates]

        self.logger.info("RM processing started")

        frames = self.reader.read(rm_file, rm_cfg["sheet_config"])
        parts = []

        # ── Per-sheet processing ────────────────────────────────────────
        for df, prefix, sheet in frames:
            self.logger.info(f"→ {sheet}")

            df = self.transformer.normalize_columns(df)
            df = self.transformer.filter_by_date_and_shift(df, date_list, sheet)

            if df is None or df.empty:
                self.logger.warning(f"   SKIPPED: {sheet} (no valid data)")
                continue

            df = self.transformer.filter_invalid_markers(df, invalid_markers=invalid_markers)

            if df is None or df.empty:
                self.logger.warning(f"   SKIPPED: {sheet} (all rows filtered as invalid)")
                continue

            if "ONLINE/OFFLINE" in df.columns:
                df = self.transformer.split_online_offline_and_merge(df)

            if {"DATE", "SHIFT"}.issubset(df.columns):
                counts = df.groupby(["DATE", "SHIFT"]).size()
                if any(counts > 1):
                    df = self.transformer.average_shift_blocks(df)

            df = df.copy()
            df["MERGE_KEY"] = df["DATE"].astype(str) + "_" + df["SHIFT"]
            df = df.rename(
                columns={c: f"{prefix}{c}" for c in df.columns if c != "MERGE_KEY"}
            )
            parts.append(df)
            self.logger.info(f"   OK: {sheet}")

        if not parts:
            self.logger.error("No RM data produced — exiting")
            return pd.DataFrame()

        # ── Align all sheets by MERGE_KEY ───────────────────────────────
        combined = parts[0]
        for df in parts[1:]:
            combined = combined.merge(
                df, on="MERGE_KEY", how="outer", suffixes=("", "_dup"),
            )
        combined = combined.loc[:, ~combined.columns.str.endswith("_dup")]

        # ── Rename fields ────────────────────────────────────────────────
        if rename_map:
            combined = combined.rename(columns=rename_map)
            self.logger.info("RM fields renamed using rm.yaml mapping")

        combined.to_excel(os.path.join(output_dir, "rm_combined_raw_1.xlsx"), index=False)
        # ── Fix shift order (C → A → B) ──────────────────────────────────
        combined["SHIFT"] = combined["MERGE_KEY"].str.split("_").str[-1]
        combined["SHIFT_ORDER"] = combined["SHIFT"].map(shift_priority)
        combined = combined.sort_values("SHIFT_ORDER").reset_index(drop=True)

        # ── Build final datetime ─────────────────────────────────────────
        date_col = next(
            (c for c in combined.columns if c.upper().endswith("_DATE")), None
        )
        if date_col is None:
            self.logger.error("No *_DATE column found after merge — cannot build datetime")
            return pd.DataFrame()

        combined["Date"] = pd.to_datetime(combined[date_col], errors="coerce")
        combined.drop(
            columns=[c for c in combined.columns if c.upper().endswith("_DATE")],
            inplace=True,
            errors="ignore",
        )
        combined["Date"] = pd.to_datetime(
            combined["Date"].dt.strftime("%Y-%m-%d")
            + " "
            + combined["SHIFT"].map(shift_time)
        )
        # C-shift belongs to the previous calendar day
        combined.loc[combined["SHIFT"] == "C", "Date"] -= pd.Timedelta(days=1)

        combined.drop(columns=["SHIFT", "SHIFT_ORDER", "MERGE_KEY"], inplace=True)
        combined = combined.rename(columns={"Date": "date"})

        # ── Write output ─────────────────────────────────────────────────
        if rename_map:
            allowed = [c for c in rename_map.values() if c in combined.columns]
            combined = combined[allowed]

        os.makedirs(output_dir, exist_ok=True)
        out_path = os.path.join(output_dir, output_filename)
        combined.to_excel(out_path, index=False)
        self.logger.info(f"RM output written → {out_path}")
        self.logger.info("RM processing completed successfully")

        # ── Push to Neon DB ──────────────────────────────────────────────
        neon_cfg = setting_cfg.get("neondb")
        if neon_cfg:
            self.logger.info("Pushing RM data to Neon DB...")
            neon_client = NeonClient(neon_cfg)
            try:
                material_lookup = neon_client.fetch_material_lookup()

                rm_neon_cfg = rm_cfg.get("neon", {})
                category_map = rm_neon_cfg.get("category_map", {})
                conflict_cols = rm_neon_cfg.get("conflict_cols", ["material_id", "date_time"])

                mapper = RMNeonMapper(
                    material_lookup, category_map=category_map, logger=self.logger
                )

                for table_name, df in mapper.iter_table_dfs(combined):
                    try:
                        rows = neon_client.insert_dataframe(
                            df=df,
                            table_name=table_name,
                            conflict_cols=conflict_cols,
                        )
                        self.logger.info(f"    {table_name}: {rows} rows inserted")
                    except Exception as e:
                        self.logger.error(f"    Failed for {table_name}: {e}")
            finally:
                neon_client.close()

        return combined
