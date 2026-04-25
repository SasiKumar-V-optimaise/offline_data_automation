# src/infrastructure/neon_client.py

import psycopg2
from psycopg2.extras import execute_values
import pandas as pd


class NeonClient:
    def __init__(self, db_config: dict):
        self.conn = psycopg2.connect(db_config["url"])
        self.conn.autocommit = True

    # ------------------------------------------------------------------
    def insert_dataframe(
        self,
        df: pd.DataFrame,
        table_name: str,
        conflict_cols: list[str] | None = None,
    ) -> int:
        """
        Upsert `df` into `table_name`.

        Returns the number of rows processed.
        Raises on DB error so the caller can log/handle it.
        """
        if df.empty:
            return 0

        df = df.copy()

        # ── normalise the datetime column ──────────────────────────────
        if "date_time" in df.columns:
            df["date_time"] = pd.to_datetime(df["date_time"], errors="coerce")
            if df["date_time"].dt.tz is None:
                df["date_time"] = df["date_time"].dt.tz_localize("Asia/Kolkata")
            df["date_time"] = df["date_time"].dt.tz_convert("UTC")

        # ── replace NaN with None (psycopg2 writes NULL) ──────────────
        df = df.where(df.notnull(), None)

        cols = list(df.columns)
        values = [tuple(row) for row in df.itertuples(index=False, name=None)]

        conflict_cols = conflict_cols or ["material_id", "date_time"]
        update_cols = [c for c in cols if c not in conflict_cols]

        if not update_cols:
            # nothing to update — just INSERT IGNORE
            query = f"""
                INSERT INTO {table_name} ({", ".join(cols)})
                VALUES %s
                ON CONFLICT ({", ".join(conflict_cols)}) DO NOTHING
            """
        else:
            query = f"""
                INSERT INTO {table_name} ({", ".join(cols)})
                VALUES %s
                ON CONFLICT ({", ".join(conflict_cols)})
                DO UPDATE SET
                {", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)}
            """

        with self.conn.cursor() as cur:
            execute_values(cur, query, values)

        return len(values)

    # ------------------------------------------------------------------
    def fetch_material_lookup(self) -> dict[str, int]:
        """Returns {MATERIAL_NAME_UPPER: id} from raw_materials."""
        with self.conn.cursor() as cur:
            cur.execute("SELECT id, material_name FROM raw_materials")
            return {name.strip().upper(): mid for mid, name in cur.fetchall()}

    # ------------------------------------------------------------------
    def close(self):
        self.conn.close()