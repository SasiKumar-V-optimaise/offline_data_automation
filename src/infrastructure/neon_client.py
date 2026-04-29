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
        upsert_mode: str = "on_conflict",  # "on_conflict" | "delete_insert"
    ) -> int:
        """
        Upsert `df` into `table_name`.

        upsert_mode="on_conflict"   — requires a UNIQUE/PK constraint on conflict_cols.
        upsert_mode="delete_insert" — deletes existing rows matching conflict_cols values,
                                      then inserts; works without any DB constraint.
        Returns the number of rows processed.
        """
        if df.empty:
            return 0

        df = df.copy()

        if "date_time" in df.columns:
            df["date_time"] = pd.to_datetime(df["date_time"], errors="coerce")
            if df["date_time"].dt.tz is None:
                df["date_time"] = df["date_time"].dt.tz_localize("Asia/Kolkata")
            df["date_time"] = df["date_time"].dt.tz_convert("UTC")
        
        # 🔥 ADD THIS BLOCK
        for col in df.columns:
            if col not in ["date_time", "material_id"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.where(df.notnull(), None)

        cols = list(df.columns)
        values = [tuple(row) for row in df.itertuples(index=False, name=None)]
        conflict_cols = conflict_cols or ["material_id", "date_time"]

        if upsert_mode == "delete_insert":
            match_col = conflict_cols[0]
            match_vals = df[match_col].dropna().tolist()
            if match_vals:
                with self.conn.cursor() as cur:
                    cur.execute(
                        f"DELETE FROM {table_name} WHERE {match_col} = ANY(%s)",
                        (match_vals,),
                    )
            with self.conn.cursor() as cur:
                execute_values(
                    cur,
                    f"INSERT INTO {table_name} ({', '.join(cols)}) VALUES %s",
                    values,
                )
            return len(values)

        # on_conflict mode (requires UNIQUE/PK constraint on conflict_cols)
        update_cols = [c for c in cols if c not in conflict_cols]
        if not update_cols:
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
    

    def write_hotmetal_dataframe(self, df: pd.DataFrame, source_file: str):
        df = df.copy()

        # Align column name with DB logic
        if "date" in df.columns:
            df = df.rename(columns={"date": "date_time"})

        return self.insert_dataframe(
            df=df,
            table_name="hot_metal_chemistry",
            conflict_cols=["lab_sample_id", "date_time"],
            upsert_mode="on_conflict"
        )

    def fetch_material_lookup(self) -> dict[str, int]:
        """Returns {MATERIAL_NAME_UPPER: id} from raw_materials."""
        with self.conn.cursor() as cur:
            cur.execute("SELECT id, material_name FROM raw_materials")
            return {name.strip().upper(): mid for mid, name in cur.fetchall()}


    def close(self):
        self.conn.close()
