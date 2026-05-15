# src/domains/hot_metal/reader.py

from datetime import datetime, timedelta, time
import pandas as pd
import re
from openpyxl import load_workbook
from openpyxl.utils import column_index_from_string

from core.logging import log_file_read


class HotMetalReader:
    def __init__(self, logger):
        self.logger = logger

    def read_for_dates(self, file_path: str, run_dates, hm_cfg: dict) -> pd.DataFrame:
        log_file_read(self.logger, file_path, domain="HOT_METAL")
        hm = hm_cfg["hot_metal_config"]
        sheet_key = hm["sheet_name"]
        block = hm["sheets"][sheet_key]

        usecols = block.get("columns", "A:Z")
        header_rows = sorted(block.get("header_row", [3, 4]))
        top = max(header_rows)

        xls = pd.ExcelFile(file_path)
        sheet = sheet_key if sheet_key in xls.sheet_names else None
        if not sheet:
            raise ValueError(f"HOT METAL sheet '{sheet_key}' not found")

        # Build merged headers
        H = xls.parse(sheet, header=None, usecols=usecols, nrows=top + 1).fillna("")
        h1 = H.iloc[header_rows[0]].astype(str).str.strip()
        h2 = H.iloc[header_rows[1]].astype(str).str.strip()

        cols = [
            a if a and not b else b if b and not a else f"{a} | {b}" if (a or b) else ""
            for a, b in zip(h1, h2)
        ]
        cols = [c or f"COL_{i+1}" for i, c in enumerate(cols)]

        df = xls.parse(sheet, header=None, usecols=usecols, skiprows=top + 1)
        df.columns = cols
        df = df.dropna(how="all")

        date_col = next(c for c in df.columns if "DATE" in c.upper())
        time_col = next((c for c in df.columns if "RECD TIME" in c.upper()), None)

        df[date_col] = pd.to_datetime(df[date_col], errors="coerce", dayfirst=True)
        df = df[df[date_col].notna()].copy()

        

        def parse_time(v):
            if pd.isna(v):
                return None

            try:
                v = str(v).strip()
                v = re.sub(r"\.+$", "", v)

                if "." in v:
                    parts = v.split(".")
                    if len(parts) >= 2:
                        h = int(parts[0])
                        m = int(parts[1])

                elif ":" in v:
                    parts = v.split(":")
                    h = int(parts[0])
                    m = int(parts[1])

                else:
                    val = float(v)
                    h = int(val)
                    m = int(round((val - h) * 100))

                if m >= 60:
                    h += m // 60
                    m = m % 60

                return time(h % 24, m)

            except Exception:
                return None

        if time_col:
            df[time_col] = df[time_col].apply(parse_time)
            df["date"] = df.apply(
                lambda r: datetime.combine(r[date_col].date(), r[time_col]) - timedelta(minutes=16)
                if r[time_col] else r[date_col],
                axis=1,
            )
        else:
            df["date"] = df[date_col]

        fdates = {datetime.strptime(d, "%d-%b-%Y").date() for d in run_dates}
        df = df[df["date"].dt.date.isin(fdates)].copy()

        return df
