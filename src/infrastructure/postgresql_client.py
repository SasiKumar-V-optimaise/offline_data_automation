import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
import logging

logger = logging.getLogger(__name__)


class PostgreSQLClient:
    def __init__(self, connection_string: str):
        self.connection_string = connection_string
        self.conn = None

    def connect(self):
        if not self.conn:
            self.conn = psycopg2.connect(self.connection_string)
            self.conn.autocommit = False
            logger.info("Connected to PostgreSQL database.")

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None
            logger.info("PostgreSQL connection closed.")

    def insert_dataframe(
        self,
        df: pd.DataFrame,
        table_name: str,
        conflict_cols: list[str] | None = None,
        upsert_mode: str = "on_conflict",
    ) -> int:
        if df.empty:
            return 0

        self.connect()
        df = df.copy()

        if "date_time" in df.columns:
            df["date_time"] = pd.to_datetime(df["date_time"], errors="coerce")
            if df["date_time"].dt.tz is None:
                df["date_time"] = df["date_time"].dt.tz_localize("Asia/Kolkata")
            df["date_time"] = df["date_time"].dt.tz_convert("UTC")

        df = df.where(pd.notnull(df), None)

        cols = list(df.columns)
        values = [tuple(row) for row in df.itertuples(index=False, name=None)]
        conflict_cols = conflict_cols or ["date_time"]

        if upsert_mode == "delete_insert":
            match_col = conflict_cols[0]
            match_vals = df[match_col].dropna().tolist()
            with self.conn.cursor() as cur:
                if match_vals:
                    cur.execute(
                        f"DELETE FROM {table_name} WHERE {match_col} = ANY(%s)",
                        (match_vals,),
                    )
                execute_values(
                    cur,
                    f"INSERT INTO {table_name} ({', '.join(cols)}) VALUES %s",
                    values,
                )
            self.conn.commit()
            return len(values)

        update_cols = [c for c in cols if c not in conflict_cols]
        if not update_cols:
            query = f"INSERT INTO {table_name} ({', '.join(cols)}) VALUES %s ON CONFLICT ({', '.join(conflict_cols)}) DO NOTHING"
        else:
            query = f"INSERT INTO {table_name} ({', '.join(cols)}) VALUES %s ON CONFLICT ({', '.join(conflict_cols)}) DO UPDATE SET {', '.join(f'{c} = EXCLUDED.{c}' for c in update_cols)}"

        with self.conn.cursor() as cur:
            execute_values(cur, query, values)
        self.conn.commit()
        return len(values)

    def fetch_material_lookup(self):
        return {}

    def fetch_dataframe(self, query: str, params=None) -> pd.DataFrame:
        self.connect()
        try:
            return pd.read_sql(query, self.conn, params=params)
        except Exception as e:
            logger.error(f"Error fetching data: {e}")
            return pd.DataFrame()
