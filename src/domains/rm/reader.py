# src/domains/rm/reader.py

import pandas as pd
from typing import Dict, Any, List, Tuple


class RMReader:
    def __init__(self, logger):
        self.logger = logger

    def read(
        self,
        file_path: str,
        sheet_config: Dict[str, Any],
    ) -> List[Tuple[pd.DataFrame, str, str]]:
        """
        Returns list of (df, prefix, sheet_name)
        """
        xls = pd.ExcelFile(file_path)
        frames = []

        for key, cfg in sheet_config.items():
            sheet = cfg["sheet_name"]
            cols = cfg["columns"]
            header = cfg["header_row"] - 1
            prefix = cfg.get("col_prefix", "")

            if sheet not in xls.sheet_names:
                self.logger.warning(f"RM sheet missing: {sheet}")
                continue

            df = pd.read_excel(
                xls,
                sheet_name=sheet,
                usecols=cols,
                header=header,
            ).dropna(how="all")

            df.columns = [str(c).strip().upper() for c in df.columns]
            frames.append((df.reset_index(drop=True), prefix, sheet))

        return frames
