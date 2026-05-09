import os
import glob
from datetime import datetime
from core.logging import get_logger
from infrastructure.postgresql_client import PostgreSQLClient
from domains.rm_stock.reader import RMStockReader
from domains.rm_stock.processor import RMStockProcessor
import pandas as pd

logger = get_logger(__name__)


class RMStockPostgreSQLService:
    def __init__(self, config):
        self.config = config
        self.postgresql_client = PostgreSQLClient(config['postgresql']['connection_string'])
        self.reader = RMStockReader(logger)
        self.processor = RMStockProcessor()

    def process(self, run_dates):
        for run_date in run_dates:
            excel_files = glob.glob(
                os.path.join(
                    self.config['download']['download_dir'],
                    '**',
                    f'*{self.config["portal"]["file_mappings"]["rm_stock"]}*',
                ),
                recursive=True,
            )
            if not excel_files:
                logger.warning(f"No RM Stock Excel files found for {run_date}")
                continue
            latest_file = max(excel_files, key=os.path.getmtime)
            df, ts = self.reader.read(latest_file, run_date)
            df = self.processor.process(df, ts)

            material_map = self.config['rm_stock']
            df['material_key'] = df['material'].astype(str).str.lower().map(
                lambda x: next(
                    (k for k in material_map if k.lower() in x),
                    'unknown',
                )
            )
            df = df.groupby('material_key', dropna=False)['physical_stock'].sum().reset_index()
            df['date_time'] = pd.to_datetime(df['time'], errors='coerce')
            self.postgresql_client.insert_dataframe(df, 'rm_stock')

        self.postgresql_client.close()