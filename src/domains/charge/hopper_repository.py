from datetime import datetime
import pandas as pd
import psycopg2
from psycopg2 import sql


HOPPER_COLUMNS = [f"hopper_{i:02d}" for i in range(1, 20)]


class HopperSnapshotRepository:
    def __init__(
        self,
        neon_cfg: dict,
        hopper_cfg: dict | None = None,
        material_cfg: dict | None = None,
    ):
        self.neon_cfg = neon_cfg
        self.hopper_cfg = {
            "schema": "ops_config",
            "table": "hopper_raw_material_history",
            "timestamp_column": "date_time",
            **(hopper_cfg or {}),
        }
        self.material_cfg = {
            "schema": "plant_master",
            "table": "materials",
            "code_column": "material_code",
            "name_column": "material_name",
            "active_column": "is_active",
            **(material_cfg or {}),
        }

    def fetch_for_day(self, target_date: datetime):
        start_ist = pd.Timestamp(target_date).tz_localize("Asia/Kolkata")
        end_ist = start_ist + pd.Timedelta(days=1)

        start_utc = start_ist.tz_convert("UTC").to_pydatetime()
        end_utc = end_ist.tz_convert("UTC").to_pydatetime()

        with psycopg2.connect(dsn=self.neon_cfg["url"]) as conn:
            df = self._fetch_hopper_history(conn, start_utc, end_utc)
            material_names = self._fetch_material_names(conn)

        snapshots = []
        ts_col = self.hopper_cfg["timestamp_column"]

        for _, row in df.iterrows():
            codes = {
                hopper_no: code
                for hopper_no, col in enumerate(HOPPER_COLUMNS, start=1)
                if (code := self._normalize_code(row.get(col)))
            }
            names = {
                hopper_no: material_names.get(code, code)
                for hopper_no, code in codes.items()
            }
            ts = (
                pd.to_datetime(row[ts_col], utc=True)
                .tz_convert("Asia/Kolkata")
                .tz_localize(None)
            )

            snapshots.append({
                "ts": ts,
                "codes": codes,
                "names": names,
            })

        return snapshots

    def _fetch_hopper_history(
        self,
        conn,
        start_utc: datetime,
        end_utc: datetime,
    ) -> pd.DataFrame:
        ts_col = self.hopper_cfg["timestamp_column"]
        selected_cols = sql.SQL(", ").join([
            sql.Identifier(ts_col),
            *map(sql.Identifier, HOPPER_COLUMNS),
        ])
        query = sql.SQL(
            """
            WITH previous AS (
                SELECT {selected_cols}
                FROM {table}
                WHERE {ts_col} < %s
                ORDER BY {ts_col} DESC
                LIMIT 1
            ),
            daily AS (
                SELECT {selected_cols}
                FROM {table}
                WHERE {ts_col} >= %s AND {ts_col} < %s
            )
            SELECT * FROM previous
            UNION ALL
            SELECT * FROM daily
            ORDER BY {ts_col}
            """
        ).format(
            ts_col=sql.Identifier(ts_col),
            selected_cols=selected_cols,
            table=sql.Identifier(self.hopper_cfg["schema"], self.hopper_cfg["table"]),
        )
        return self._read_frame(conn, query, (start_utc, start_utc, end_utc))

    def _fetch_material_names(self, conn) -> dict[str, str]:
        code_col = self.material_cfg["code_column"]
        name_col = self.material_cfg["name_column"]
        active_col = self.material_cfg.get("active_column")
        active_filter = sql.SQL("")
        if active_col:
            active_filter = sql.SQL(" AND COALESCE({}, TRUE)").format(sql.Identifier(active_col))

        query = sql.SQL(
            """
            SELECT {code_col}, {name_col}
            FROM {table}
            WHERE {code_col} IS NOT NULL{active_filter}
            """
        ).format(
            code_col=sql.Identifier(code_col),
            name_col=sql.Identifier(name_col),
            table=sql.Identifier(self.material_cfg["schema"], self.material_cfg["table"]),
            active_filter=active_filter,
        )
        df = self._read_frame(conn, query)
        material_names = {}
        for _, row in df.iterrows():
            code = self._normalize_code(row[code_col])
            if not code:
                continue
            name = None if pd.isna(row[name_col]) else str(row[name_col]).strip()
            material_names[code] = name or code
        return material_names

    @staticmethod
    def _normalize_code(value) -> str | None:
        if pd.isna(value):
            return None
        code = str(value).strip().lower()
        return code or None

    @staticmethod
    def _read_frame(conn, query, params=None) -> pd.DataFrame:
        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
        return pd.DataFrame(rows, columns=columns)


