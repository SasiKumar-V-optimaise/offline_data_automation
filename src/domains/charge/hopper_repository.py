from dataclasses import dataclass
from datetime import datetime
import re
import pandas as pd
import psycopg2


@dataclass
class HopperSnapshot:
    ts: datetime
    mapping: dict[int, str]  


class HopperSnapshotRepository:
    def __init__(self, neon_cfg: dict):
        self.neon_cfg = neon_cfg

    def fetch_for_day(self, target_date: datetime):
        start_ist = pd.Timestamp(target_date).tz_localize("Asia/Kolkata")
        end_ist = start_ist + pd.Timedelta(days=1)

        start_utc = start_ist.tz_convert("UTC").to_pydatetime()
        end_utc = end_ist.tz_convert("UTC").to_pydatetime()

        query = """
        SELECT *
        FROM hopper_raw_material_history
        WHERE ts < %s
        ORDER BY ts;
        """

        db_url = self.neon_cfg["url"]

        with psycopg2.connect(dsn=db_url) as conn:
            df = pd.read_sql(query, conn, params=(end_utc,))

        snapshots = []

        for _, row in df.iterrows():
            mapping = {}

            for col in df.columns:
                m = re.fullmatch(r"hopper_0?(\d+)", col)
                if not m:
                    continue

                hopper_no = int(m.group(1))
                mapping[hopper_no] = str(row[col]).strip()

            ts = pd.to_datetime(row["ts"], utc=True)\
                .tz_convert("Asia/Kolkata")\
                .tz_localize(None)

            snapshots.append({
                "ts": ts,
                "mapping": mapping
            })

        return snapshots


