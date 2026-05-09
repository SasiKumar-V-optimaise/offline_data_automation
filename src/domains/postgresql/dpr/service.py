from core.logging import get_logger
from infrastructure.postgresql_client import PostgreSQLClient
from domains.dpr.reader import DPRReader
from domains.dpr.config_updater import DPRConfigUpdater
import pandas as pd
import glob
import os

logger = get_logger(__name__)


class DPRPostgreSQLService:
    def __init__(self, config):
        self.config = config
        self.postgresql_client = PostgreSQLClient(config['postgresql']['connection_string'])

    def process(self, run_dates):
        dpr_cfg = self.config['dpr']
        field_mapping = dpr_cfg.get('dpr_fields', {})
        config_updater = DPRConfigUpdater(logger)

        for run_date in run_dates:
            excel_files = glob.glob(
                os.path.join(
                    self.config['download']['download_dir'],
                    '**',
                    f'*{self.config['portal']['file_mappings']['dpr']}*',
                ),
                recursive=True,
            )
            if not excel_files:
                logger.warning(f"No DPR Excel files found for {run_date}")
                continue

            latest_file = max(excel_files, key=os.path.getmtime)
            config_updater.update_rows_in_config(latest_file, dpr_cfg, run_date)
            reader = DPRReader(logger)
            df = reader.read_for_date(latest_file, dpr_cfg, run_date)
            if df is not None and 'date' in df.columns:
                df = df.rename(columns=field_mapping)
                self.postgresql_client.insert_dataframe(df, 'dpr_data')

        self.postgresql_client.close()