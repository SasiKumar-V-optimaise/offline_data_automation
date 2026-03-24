# src/domains/dpr/reader.py

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any, List, Optional

import pandas as pd
from openpyxl import load_workbook
from openpyxl.utils import column_index_from_string


@dataclass
class DPRReader:
    logger: any

    def _build_reverse_map(self, rename_map: Dict[str, List[str]]) -> Dict[str, str]:
        # reverse_map: "Coke Online - EML" -> "COKE_ONLINE_MT"
        reverse = {}
        for new_col, labels in (rename_map or {}).items():
            if isinstance(labels, list) and labels:
                for label in labels:
                    reverse[label] = new_col
        return reverse

    def read_for_date(self, file_path, dpr_cfg, run_date):

        sheets = dpr_cfg["dpr_config"]["sheets"]
        run_dt = datetime.strptime(run_date, "%d-%b-%Y").date()

        wb = load_workbook(file_path, data_only=True)
        all_parts = []

        self.logger.info(f"Reading DPR Excel: {file_path}")

        # ✅ get ONLY correct sheet
        from domains.dpr.config_updater import DPRConfigUpdater
        updater = DPRConfigUpdater(self.logger)
        target_sheet = updater.select_sheet_for_run_date(wb, run_date)

        processed = False

        for sheet_key, cfg in sheets.items():
            sheet_name = cfg["sheet_name"]

            # ✅ ONLY process matching sheet
            if sheet_name != target_sheet:
                continue

            self.logger.info(f"Processing sheet: {sheet_name}")

            # ✅ safety check
            if sheet_name not in wb.sheetnames:
                self.logger.warning(f"Sheet '{sheet_name}' not found — skipping")
                continue

            ws = wb[sheet_name]

            # ---- SAME LOGIC (unchanged) ----
            date_row = int(cfg["date_row"]) - 1
            col_start, col_end = cfg["date_cols"]

            from openpyxl.utils import column_index_from_string
            col_range = range(
                column_index_from_string(col_start),
                column_index_from_string(col_end) + 1,
            )

            raw_dates = [ws.cell(row=date_row + 1, column=col).value for col in col_range]

            import pandas as pd
            parsed_dates, valid_cols = [], []

            for i, val in enumerate(raw_dates):
                parsed = pd.to_datetime(val, errors="coerce")
                if pd.notna(parsed):
                    parsed_dates.append(parsed.date())
                    valid_cols.append(col_range[i])

            raw_data = {}
            for label, row_idx in cfg["rows"].items():
                if not row_idx:
                    continue
                raw_data[label] = [
                    ws.cell(row=row_idx, column=col).value for col in valid_cols
                ]

            non_empty_mask = [
                any(raw_data[label][i] is not None for label in raw_data)
                for i in range(len(valid_cols))
            ]

            filtered_dates = [
                parsed_dates[i] for i, keep in enumerate(non_empty_mask) if keep
            ]

            df = pd.DataFrame({"Date": filtered_dates})

            reverse_map = self._build_reverse_map(cfg.get("rename_map", {}))

            for label, values in raw_data.items():
                vals = [values[i] for i, keep in enumerate(non_empty_mask) if keep]
                col = reverse_map.get(label, label)
                df[col] = vals

            df = df[df["Date"] == run_dt].reset_index(drop=True)

            if not df.empty:
                all_parts.append(df)
                processed = True

        if not processed:
            self.logger.warning("No DPR data found")
            return None

        return pd.concat(all_parts, ignore_index=True)

