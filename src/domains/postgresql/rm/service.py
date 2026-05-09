import os
import glob
from datetime import datetime
import pandas as pd
from core.logging import get_logger, LogTemplates
from infrastructure.postgresql_client import PostgreSQLClient
from domains.rm.reader import RMReader
from domains.rm.transformer import RMTransformer

logger = get_logger(__name__)


class RMPostgreSQLService:
    def __init__(self, config):
        self.config = config
        self.postgresql_client = PostgreSQLClient(config['postgresql']['connection_string'])
        self.reader = RMReader(logger)
        self.transformer = RMTransformer(logger)

    def process(self, run_dates):
        rm_cfg = self.config['rm']
        rename_map = rm_cfg.get('rename_fields', {})
        date_list = [datetime.strptime(d, '%d-%b-%Y').date() for d in run_dates]

        logger.info(f"START | mode=rm_postgresql dates={len(date_list)}")

        excel_files = glob.glob(
            os.path.join(
                self.config['download']['download_dir'],
                '**',
                f'*{self.config["portal_files"]["rm"]}*',
            ),
            recursive=True,
        )

        if not excel_files:
            logger.warning(LogTemplates.skipped('no_rm_file'))
            return

        latest_file = max(excel_files, key=os.path.getmtime)
        frames = self.reader.read(latest_file, rm_cfg['sheet_config'])
        parts = []

        for df, prefix, sheet in frames:
            logger.info(f"SHEET | name={sheet}")
            df = self.transformer.normalize_columns(df)
            df = self.transformer.filter_by_date_and_shift(df, date_list, sheet)

            if df is None or df.empty:
                logger.warning(LogTemplates.skipped(f"sheet={sheet}"))
                continue

            if 'ONLINE/OFFLINE' in df.columns:
                df = self.transformer.split_online_offline_and_merge(df)

            if {'DATE', 'SHIFT'}.issubset(df.columns):
                counts = df.groupby(['DATE', 'SHIFT']).size()
                if any(counts > 1):
                    df = self.transformer.average_shift_blocks(df)

            df = df.copy()
            df['MERGE_KEY'] = df['DATE'].astype(str) + '_' + df['SHIFT']
            df = df.rename(
                columns={c: f"{prefix}{c}" for c in df.columns if c != 'MERGE_KEY'}
            )
            parts.append(df)
            logger.info(f"OK | sheet={sheet}")

        if not parts:
            logger.error(LogTemplates.failed('no_data'))
            return

        combined = parts[0]
        for part in parts[1:]:
            combined = combined.merge(
                part,
                on='MERGE_KEY',
                how='outer',
                suffixes=('', '_dup'),
            )
        combined = combined.loc[:, ~combined.columns.str.endswith('_dup')]

        if rename_map:
            combined = combined.rename(columns=rename_map)
            logger.info('CONFIG | fields_renamed')

        if 'MERGE_KEY' in combined.columns:
            combined.drop(columns=['MERGE_KEY'], inplace=True)

        if 'SHIFT' in combined.columns:
            combined['SHIFT'] = combined['SHIFT'].astype(str)
            combined['SHIFT_ORDER'] = combined['SHIFT'].map({'C': 0, 'A': 1, 'B': 2})
            combined = combined.sort_values('SHIFT_ORDER').reset_index(drop=True)
            combined.drop(columns=['SHIFT_ORDER'], inplace=True)

        date_col = next((c for c in combined.columns if c.upper().endswith('_DATE')), None)
        if date_col:
            combined['Date'] = pd.to_datetime(combined[date_col], errors='coerce')
            combined.drop(columns=[c for c in combined.columns if c.upper().endswith('_DATE')], inplace=True, errors='ignore')
            combined['Date'] = pd.to_datetime(
                combined['Date'].dt.strftime('%Y-%m-%d')
                + ' '
                + combined['SHIFT'].map({'A': '07:00', 'B': '15:00', 'C': '23:00'})
            )
            combined.loc[combined['SHIFT'] == 'C', 'Date'] -= pd.Timedelta(days=1)
            combined = combined.drop(columns=['SHIFT'], errors='ignore')
            combined = combined.rename(columns={'Date': 'date'})

        self.postgresql_client.insert_dataframe(combined, 'rm_data')
        self.postgresql_client.close()