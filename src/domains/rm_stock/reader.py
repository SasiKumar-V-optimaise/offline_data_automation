import pandas as pd

from core.logging import log_file_read


class RMStockReader:
    def __init__(self, logger):
        self.logger = logger

    def _get_sheet_name(self, run_date: str) -> str:
        dt = pd.to_datetime(run_date, format="%d-%b-%Y")
        return dt.strftime("%d.%m")

    def read(self, file_path: str, run_date: str) -> tuple[pd.DataFrame, pd.Timestamp]:
        sheet_name = self._get_sheet_name(run_date)

        log_file_read(self.logger, file_path, domain="RM_STOCK", sheet=sheet_name)
        self.logger.info(f"Reading sheet: {sheet_name}")

        try:
            df = pd.read_excel(
                file_path,
                sheet_name=sheet_name,
                usecols="B,T",
                header=0,
            )
        except ValueError:
            raise ValueError(f"Sheet '{sheet_name}' not found in file")

        df.columns = ["material", "physical_stock"]

        # Clean
        df["material"] = df["material"].astype(str).str.strip()
        df["physical_stock"] = pd.to_numeric(df["physical_stock"], errors="coerce")
        df = df.dropna(subset=["material"])

        # Read timestamp FROM SAME SHEET
        ts = pd.read_excel(
            file_path,
            sheet_name=sheet_name,
            usecols="S",
            nrows=2,
            header=None,
        ).iloc[1, 0]

        return df, ts
