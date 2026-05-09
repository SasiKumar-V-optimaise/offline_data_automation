from core.config_loader import ConfigLoader
from core.logging import get_logger
from infrastructure.postgresql_client import PostgreSQLClient
import pandas as pd
import glob
import os

logger = get_logger(__name__)

class RMHMPostgreSQLService:
    def __init__(self, config):
        self.config = config
        self.postgresql_client = PostgreSQLClient(config['postgresql']['connection_string'])

    def process(self, run_dates):
        for run_date in run_dates:
            excel_files = glob.glob(os.path.join(self.config['download']['directory'], '**', f'*{self.config["portal"]["file_mappings"]["rm_hm"]}*'), recursive=True)
            if not excel_files:
                logger.warning(f"No RM-HM Excel files found for {run_date}")
                continue
            latest_file = max(excel_files, key=os.path.getmtime)
            df = pd.read_excel(latest_file, sheet_name=self.config['rm_hm'].get('sheet_name', 'SP-02'))
            df.columns = df.columns.str.lower().str.replace('[^a-z0-9]', '', regex=True)
            required_cols = ['ai', 'ti', 'rdi', 'ri']
            for col in required_cols:
                if col not in df.columns:
                    df[col] = df[col].fillna(method='ffill')
            df = df[df['date'].dt.date == run_date.date()]
            df.rename(columns=self.config['rm_hm']['rm_hm_fields'], inplace=True)
            self.postgresql_client.insert_dataframe(df, 'rm_hm_data')
        self.postgresql_client.close()