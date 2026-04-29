# src/domains/charge/service.py
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import pandas as pd

from .reader import ChargeExcelReader
from .processor import RawChargeProcessor
from .snapshot_repository import HopperSnapshotRepository
import yaml
from infrastructure.neon_client import NeonClient




@dataclass
class ChargeServiceConfig:
    output_dir: str
    neon_dev_cfg: dict   # snapshot DB
    neondb_cfg: dict     # charge_data DB
    charge_yaml_path: str = "src/config/charge.yaml"
    write_to_neon: bool = False


class ChargeService:
    def __init__(self, cfg: ChargeServiceConfig, logger):
        self.cfg = cfg
        self.logger = logger
        self.reader = ChargeExcelReader()
        self.processor = RawChargeProcessor()
        self.snapshot_repo = HopperSnapshotRepository(cfg.neon_dev_cfg)

    def run(self, charge_file: str, run_date_str: str) -> pd.DataFrame:
        target_date = datetime.strptime(run_date_str, "%d-%b-%Y")

        self.logger.info(f"Reading raw charge data for {run_date_str}")

        raw_df = self.reader.read_target_day_raw(
            file_path=charge_file,
            target_date=target_date,
        )

        self.logger.info(f"Raw rows found: {len(raw_df)}")

        snapshots = self.snapshot_repo.fetch_for_day(target_date)
        self.logger.info(f"Snapshots loaded: {len(snapshots)}")
        self.logger.info(f"First snapshot: {snapshots[0]['ts']}")
        self.logger.info(f"Last snapshot: {snapshots[-1]['ts']}")

        final_df = self.processor.process_wide_with_time(
            raw_df=raw_df,
            snapshots=snapshots
        )
        Path(self.cfg.output_dir).mkdir(parents=True, exist_ok=True)

        out_path = Path(self.cfg.output_dir) / f"raw_charge_data_{target_date:%Y_%m_%d}.xlsx"
        final_df.to_excel(out_path, index=False)

        self.logger.info(f"Raw charge Excel written: {out_path}")

        # ------------------------------
        # LOAD CHARGE YAML MAPPING
        # ------------------------------
        with open(self.cfg.charge_yaml_path, "r", encoding="utf-8") as f:
            charge_cfg = yaml.safe_load(f) or {}

        material_column_map = charge_cfg.get("material_column_map", {})

        # ------------------------------
        # CONVERT TO DB FORMAT
        # ------------------------------
        db_df = self.processor.to_charge_data_table(
            wide_df=final_df,
            material_column_map=material_column_map,
        )

        db_out_path = Path(self.cfg.output_dir) / f"charge_data_table_{target_date:%Y_%m_%d}.xlsx"
        db_df.to_excel(db_out_path, index=False)

        self.logger.info(f"Charge DB-format Excel written: {db_out_path}")



        # ------------------------------
        # WRITE TO NEON DB
        # ------------------------------
        if self.cfg.write_to_neon:
            client = NeonClient(self.cfg.neondb_cfg)

            try:
                inserted = client.insert_dataframe(
                    df=db_df,
                    table_name="public.charge_data",
                    conflict_cols=["date_time"],
                    upsert_mode="on_conflict",
                )

                self.logger.info(f"Inserted/updated rows in charge_data: {inserted}")

            finally:
                client.close()

        return final_df