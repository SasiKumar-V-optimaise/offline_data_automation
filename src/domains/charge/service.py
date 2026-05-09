# src/domains/charge/service.py

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import pandas as pd

from .reader import ChargeExcelReader
from .processor import RawChargeProcessor
from .hopper_repository import HopperSnapshotRepository
from infrastructure.neon_client import NeonClient
from core.logging import get_logger, LogTemplates

logger = get_logger(__name__)


# -------------------------------------------------
# CONFIG
# -------------------------------------------------
@dataclass
class ChargeServiceConfig:
    output_dir: str
    neon_dev_cfg: dict      # snapshot DB
    neondb_cfg: dict        # charge_data DB
    charge_cfg: dict       
    write_to_neon: bool = False


# -------------------------------------------------
# SERVICE
# -------------------------------------------------
class ChargeService:
    def __init__(self, cfg: ChargeServiceConfig, logger):
        self.cfg = cfg
        self.logger = logger
        self.reader = ChargeExcelReader()
        self.processor = RawChargeProcessor()
        self.snapshot_repo = HopperSnapshotRepository(cfg.neon_dev_cfg)

    def run(self, charge_file: str, run_date_str: str) -> pd.DataFrame:
        target_date = datetime.strptime(run_date_str, "%d-%b-%Y")

        logger.info(f"READ | charge_file={Path(charge_file).name} date={run_date_str}")

        # -------------------------------------------------
        # READ RAW DATA
        # -------------------------------------------------
        raw_df = self.reader.read_target_day_raw(
            file_path=charge_file,
            target_date=target_date,
        )

        logger.info(LogTemplates.process(len(raw_df)))

        # -------------------------------------------------
        # FETCH SNAPSHOTS
        # -------------------------------------------------
        snapshots = self.snapshot_repo.fetch_for_day(target_date)

        if not snapshots:
            raise ValueError("No hopper snapshots found for the given date")

        logger.info(
            f"SNAPSHOTS | count={len(snapshots)} "
            f"first={snapshots[0]['ts']} last={snapshots[-1]['ts']}"
        )

        # -------------------------------------------------
        # PROCESS WIDE FORMAT
        # -------------------------------------------------
        final_df = self.processor.process_wide_with_time(
            raw_df=raw_df,
            snapshots=snapshots,
        )

        # -------------------------------------------------
        # SAVE RAW OUTPUT
        # -------------------------------------------------
        Path(self.cfg.output_dir).mkdir(parents=True, exist_ok=True)

        out_path = Path(self.cfg.output_dir) / f"raw_charge_data_{target_date:%Y_%m_%d}.xlsx"
        final_df.to_excel(out_path, index=False)

        logger.info(f"OUTPUT | file={out_path.name}")

        # -------------------------------------------------
        # LOAD CONFIG 
        # -------------------------------------------------
        charge_cfg = self.cfg.charge_cfg

        material_column_map = charge_cfg.get("material_column_map", {})
        table_columns = charge_cfg.get("table_columns", [])

        if not table_columns:
            raise ValueError("charge.table_columns is missing in config")

        # -------------------------------------------------
        # CONVERT TO DB FORMAT
        # -------------------------------------------------
        db_df = self.processor.to_charge_data_table(
            wide_df=final_df,
            material_column_map=material_column_map,
            table_columns=table_columns,   # explicit
        )

        db_out_path = Path(self.cfg.output_dir) / f"charge_data_table_{target_date:%Y_%m_%d}.xlsx"
        db_df.to_excel(db_out_path, index=False)

        logger.info(f"OUTPUT | db_format={db_out_path.name}")

        # -------------------------------------------------
        # WRITE TO NEON DB
        # -------------------------------------------------
        if self.cfg.write_to_neon:
            client = NeonClient(self.cfg.neondb_cfg)

            try:
                inserted = client.insert_dataframe(
                    df=db_df,
                    table_name="public.charge_data",
                    conflict_cols=["date_time"],
                    upsert_mode="on_conflict",
                )

                logger.info(LogTemplates.db_inserted(inserted))

            finally:
                client.close()

        return final_df