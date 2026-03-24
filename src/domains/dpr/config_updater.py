from dataclasses import dataclass
from datetime import datetime
from calendar import month_abbr
import re
from typing import Dict, Any, Tuple, Optional

from openpyxl import load_workbook


_SHEET_RE = re.compile(r"^([A-Za-z]+)\s*'\s*(\d{2})\s*$", re.IGNORECASE)


def parse_sheet_date(name: str) -> Optional[Tuple[int, int]]:
    if not name:
        return None
    name = name.strip()
    m = _SHEET_RE.match(name)
    if not m:
        return None

    try:
        month_str = m.group(1)[:3].title()
        year = 2000 + int(m.group(2))
        month = list(month_abbr).index(month_str)
        return (year, month)
    except Exception:
        return None


@dataclass
class DPRConfigUpdater:
    logger: any

    def select_sheet_for_run_date(self, wb, run_date: str) -> str:
        run_dt = datetime.strptime(run_date, "%d-%b-%Y").date()
        target = (run_dt.year, run_dt.month)

        dated = [(s, parse_sheet_date(s)) for s in wb.sheetnames]
        dated = [(s, d) for s, d in dated if d]

        if not dated:
            raise RuntimeError("No month-named sheets found in DPR workbook.")

        for sname, parsed in dated:
            if parsed == target:
                return sname

        sname, _ = max(dated, key=lambda x: x[1])
        self.logger.warning(f"No matching sheet for {run_date}; using latest: {sname}")
        return sname

    def update_rows_in_config(self, excel_path: str, dpr_cfg: Dict[str, Any], run_date: str) -> Dict[str, Any]:

        wb = load_workbook(excel_path, data_only=True)
        target_sheet = self.select_sheet_for_run_date(wb, run_date)
        ws = wb[target_sheet]

        sheets = dpr_cfg.get("dpr_config", {}).get("sheets", {})

        sheet_key = target_sheet.replace("'", "").replace(" ", "")

        if sheet_key not in sheets:
            old_key = next(iter(sheets))
            sheets[sheet_key] = sheets[old_key]
            self.logger.warning(f"DPR month key '{sheet_key}' missing; copied from '{old_key}'")

        block = sheets[sheet_key]
        block["sheet_name"] = target_sheet

        old_rows = block.get("rows", {})
        found_rows: Dict[str, int] = {}

        for r in range(1, ws.max_row + 1):
            texts = [
                str(ws.cell(r, c).value).strip()
                for c in range(1, 8)
                if ws.cell(r, c).value
            ]
            for label in old_rows:
                if label in texts and label not in found_rows:
                    found_rows[label] = r

        new_rows = {}
        for label in old_rows:
            if label in found_rows:
                new_rows[label] = found_rows[label]
            else:
                new_rows[label] = 0
                self.logger.warning(f"'{label}' not found in '{target_sheet}'")

        block["rows"] = new_rows
        sheets[sheet_key] = block

        dpr_cfg["dpr_config"]["sheets"] = sheets

        self.logger.info(f"DPR config updated in-memory for '{target_sheet}'")
        return dpr_cfg