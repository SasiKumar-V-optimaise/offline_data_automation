import os
import re
from datetime import datetime, timedelta
import pandas as pd

from core.logging import log_file_read


class ChargeExcelReader:
    def __init__(self, sheet_hint: str = "SH", logger=None):
        self.sheet_hint = sheet_hint
        self.logger = logger

    def read_target_day_raw(self, file_path: str, target_date: datetime) -> pd.DataFrame:
        if not file_path or not os.path.exists(file_path):
            raise FileNotFoundError(f"Charge file not found: {file_path}")

        if self.logger:
            log_file_read(self.logger, file_path, domain="CHARGE")

        sheet = self._detect_sheet(file_path)
        header_row = self._detect_header_row(file_path, sheet)

        df = pd.read_excel(file_path, sheet_name=sheet, header=header_row)
        df.columns = [self._clean_col(c) for c in df.columns]

        if "DATETIME" not in df.columns:
            raise ValueError("DATETIME column not found")

        df["SOURCE_ROW_NUMBER"] = df.index + header_row + 2
        keep_cols = ["DATETIME", "SOURCE_ROW_NUMBER"]

        if "CHARGE_NO" in df.columns:
            keep_cols.append("CHARGE_NO")

        hopper_act_cols = [
            c for c in df.columns
            if re.fullmatch(r"HOPPER_\d+_ACT", c)
        ]

        keep_cols.extend(hopper_act_cols)

        df = df[keep_cols].copy()
        df["DATETIME"] = pd.to_datetime(df["DATETIME"], errors="coerce")
        df = df.dropna(subset=["DATETIME"])

        start = target_date
        end = target_date + timedelta(days=1)

        df = df[(df["DATETIME"] >= start) & (df["DATETIME"] < end)]

        return df.reset_index(drop=True)

    def _detect_sheet(self, file_path: str) -> str:
        xl = pd.ExcelFile(file_path)
        return next(
            (s for s in xl.sheet_names if self.sheet_hint.upper() in s.upper()),
            xl.sheet_names[0],
        )

    def _detect_header_row(self, file_path: str, sheet: str) -> int:
        preview = pd.read_excel(file_path, sheet_name=sheet, header=None, nrows=50)

        for i in range(len(preview)):
            row = preview.iloc[i].astype(str).str.upper().tolist()

            if "DATETIME" in row and any("HOPPER" in x for x in row):
                return i

        raise ValueError("Could not detect header row")

    @staticmethod
    def _clean_col(value) -> str:
        if pd.isna(value):
            return ""

        s = re.sub(r"\s+", "_", str(value).strip()).upper()

        m = re.fullmatch(r"HOPPER_0*(\d+)_(SP|ACT)", s)
        if m:
            return f"HOPPER_{int(m.group(1))}_{m.group(2)}"

        return s
