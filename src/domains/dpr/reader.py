# src/domains/dpr/reader.py

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any, Optional

import pandas as pd
from openpyxl import load_workbook
from openpyxl.utils import column_index_from_string

from domains.dpr.config_updater import DPRConfigUpdater


@dataclass
class DPRReader:
    logger: any

    def _normalize(self, text: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip() if text else ""

    def _build_reverse_map(self, rename_map) -> dict:
        reverse = {}
        for new_col, labels in (rename_map or {}).items():
            if isinstance(labels, list):
                for label in labels:
                    reverse[self._normalize(label)] = new_col
        return reverse

    def read_for_date(self, file_path: str, dpr_cfg: Dict[str, Any], run_date: str) -> Optional[pd.DataFrame]:
        sheets = dpr_cfg["config"]["sheets"]
        run_dt = datetime.strptime(run_date, "%d-%b-%Y").date()

        wb = load_workbook(file_path, data_only=True)
        updater = DPRConfigUpdater(self.logger)
        target_sheet = updater.select_sheet_for_run_date(wb, run_date)

        self.logger.info(f"Reading DPR Excel: {file_path} / sheet: {target_sheet}")

        for cfg in sheets.values():
            if cfg["sheet_name"] != target_sheet:
                continue

            if target_sheet not in wb.sheetnames:
                self.logger.warning(f"Sheet '{target_sheet}' not found — skipping")
                return None

            ws = wb[target_sheet]
            date_row = int(cfg["date_row"])
            col_start, col_end = cfg["date_cols"]

            col_range = range(
                column_index_from_string(col_start),
                column_index_from_string(col_end) + 1,
            )

            parsed_dates, valid_cols = [], []
            for col in col_range:
                val = ws.cell(row=date_row, column=col).value
                parsed = pd.to_datetime(val, errors="coerce")
                if pd.notna(parsed):
                    parsed_dates.append(parsed.date())
                    valid_cols.append(col)

            raw_data = {
                label: [ws.cell(row=row_idx, column=col).value for col in valid_cols]
                for label, row_idx in cfg["rows"].items()
                if row_idx
            }

            non_empty_mask = [
                any(raw_data[lbl][i] is not None for lbl in raw_data)
                for i in range(len(valid_cols))
            ]

            filtered_dates = [d for d, keep in zip(parsed_dates, non_empty_mask) if keep]
            df = pd.DataFrame({"Date": filtered_dates})

            reverse_map = self._build_reverse_map(cfg.get("rename_map", {}))

            for label, values in raw_data.items():
                vals = [v for v, keep in zip(values, non_empty_mask) if keep]
                norm = self._normalize(label)

                if norm in reverse_map:
                    col = reverse_map[norm]
                else:
                    label_tokens = set(norm.split())
                    best_col, best_score = label, 0
                    for key, mapped in reverse_map.items():
                        score = len(label_tokens & set(key.split()))
                        if score > best_score:
                            best_score, best_col = score, mapped
                    col = best_col if best_score > 0 else label

                df[col] = vals

            df = df[df["Date"] == run_dt].reset_index(drop=True)
            return df if not df.empty else None

        self.logger.warning(f"No matching sheet config found for '{target_sheet}'")
        return None
