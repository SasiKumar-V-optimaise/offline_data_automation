from core.logging import get_logger
from infrastructure.postgresql_client import PostgreSQLClient
from domains.hot_metal.reader import HotMetalReader
from domains.hot_metal.config_updater import HotMetalConfigUpdater
import pandas as pd
import glob
import os

logger = get_logger(__name__)


class HotMetalPostgreSQLService:
    def __init__(self, config):
        self.config = config
        self.postgresql_client = PostgreSQLClient(config['postgresql']['connection_string'])

    def process(self, run_dates):
        hm_cfg = self.config['hot_metal']
        config_updater = HotMetalConfigUpdater(logger)

        for run_date in run_dates:
            config_updater.update_from_excel(
                excel_path=self._find_file(run_date),
                hm_cfg=hm_cfg,
                run_date=run_date,
            )
            excel_files = glob.glob(
                os.path.join(
                    self.config['download']['download_dir'],
                    '**',
                    f'*{self.config['portal']['file_mappings']['hot_metal']}*',
                ),
                recursive=True,
            )
            if not excel_files:
                logger.warning(f"No Hot Metal Excel files found for {run_date}")
                continue
            latest_file = max(excel_files, key=os.path.getmtime)
            reader = HotMetalReader(logger)
            df = reader.read_for_dates(latest_file, [run_date], hm_cfg)
            if df is not None and not df.empty:
                df.rename(columns=self.config['hot_metal']['hot_metal_fields'], inplace=True)
                df['lab_sample_id'] = df['lab_sample_id'].astype(str)
                df['cast_no_ladle_spec'] = df['cast_no_ladle_spec'].astype(str)
                self.postgresql_client.insert_dataframe(df, 'hotmetal_slag_updated_data')
        self.postgresql_client.close()

    def _find_file(self, run_date: str) -> str:
        excel_files = glob.glob(
            os.path.join(
                self.config['download']['download_dir'],
                '**',
                f'*{self.config['portal']['file_mappings']['hot_metal']}*',
            ),
            recursive=True,
        )
        if not excel_files:
            raise FileNotFoundError(f"No Hot Metal files found for {run_date}")
        return max(excel_files, key=os.path.getmtime)
