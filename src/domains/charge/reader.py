# src/domains/charge/reader.py
import os
import re
import logging
from dataclasses import dataclass
from typing import Dict, List, Tuple

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class ChargeFileData:
    file_path: str
    df: pd.DataFrame
    material_to_hoppers: Dict[str, List[str]]
    hopper_to_material_raw: Dict[str, str]


class ChargeExcelReader:
    """
    Simplified reader:
    - NO materials.yaml / NO canonicalization
    - Reads only DATETIME + HOPPER_<n>_ACT
    - Builds material_to_hoppers from the header-material row
    """

    # ✅ accepts materials_cfg but ignores it (backward compatible with old service.py)
    def __init__(self, sheet_hint: str = "SH", materials_cfg=None, **_ignored):
        self.sheet_hint = sheet_hint

    def read(self, file_path: str) -> ChargeFileData:
        if not file_path or not os.path.exists(file_path):
            raise FileNotFoundError(f"Charge file not found: {file_path}")

        sheet = self._detect_sheet(file_path)
        header_row, material_row = self._detect_header_and_material_rows(file_path, sheet)

        # header-only read to find cols fast
        header_df = pd.read_excel(file_path, sheet_name=sheet, header=header_row, nrows=0)
        cols = [self._clean_col_name(c) for c in header_df.columns]
        if "DATETIME" not in cols:
            raise ValueError(f"'DATETIME' column not found in {file_path} ({sheet=})")

        hopper_act_cols = [c for c in cols if re.fullmatch(r"HOPPER_\d+_ACT", c)]
        usecols = ["DATETIME"] + hopper_act_cols

        df = pd.read_excel(file_path, sheet_name=sheet, header=header_row, usecols=usecols)
        df.columns = [self._clean_col_name(c) for c in df.columns]
        df["DATETIME"] = pd.to_datetime(df["DATETIME"], errors="coerce")
        df = df.dropna(subset=["DATETIME"]).copy()

        raw_hdr = pd.read_excel(file_path, sheet_name=sheet, header=None, nrows=header_row + 1)

        material_to_hoppers, hopper_to_material_raw = self._extract_mapping_from_header(
            raw_hdr=raw_hdr,
            header_row_idx=header_row,
            material_row_idx=material_row,
        )

        return ChargeFileData(
            file_path=file_path,
            df=df,
            material_to_hoppers=material_to_hoppers,
            hopper_to_material_raw=hopper_to_material_raw,
        )

    def _detect_sheet(self, file_path: str) -> str:
        xl = pd.ExcelFile(file_path)
        hint = self.sheet_hint.upper()
        return next((s for s in xl.sheet_names if hint in s.upper()), xl.sheet_names[0])

    def _detect_header_and_material_rows(self, file_path: str, sheet: str) -> Tuple[int, int]:
        preview = pd.read_excel(file_path, sheet_name=sheet, header=None, nrows=40)
        for i in range(len(preview)):
            row = preview.iloc[i].astype(str).str.upper().tolist()
            if "DATETIME" in row and any("HOPPER" in cell for cell in row):
                if i == 0:
                    break
                return i, i - 1
        raise ValueError(f"Could not detect header row in {file_path} ({sheet=})")

    def _extract_mapping_from_header(
        self,
        raw_hdr: pd.DataFrame,
        header_row_idx: int,
        material_row_idx: int,
    ):
        header_row = raw_hdr.iloc[header_row_idx].tolist()
        material_row = raw_hdr.iloc[material_row_idx].tolist()

        idx_to_col: Dict[int, str] = {}
        for idx, v in enumerate(header_row):
            c = self._clean_col_name(v)
            if c and c != "NAN":
                idx_to_col[idx] = c

        material_to_hoppers: Dict[str, List[str]] = {}
        hopper_to_material_raw: Dict[str, str] = {}

        for idx, col in idx_to_col.items():
            m = re.fullmatch(r"HOPPER_(\d+)_ACT", col)
            if not m:
                continue

            n = int(m.group(1))
            hopper_act = f"HOPPER_{n}_ACT"

            sp_idx = idx - 1
            if sp_idx in idx_to_col and idx_to_col[sp_idx] == f"HOPPER_{n}_SP":
                raw_material = material_row[sp_idx]
            else:
                raw_material = material_row[idx]

            raw_str = self._clean_material_raw(raw_material)
            if not raw_str:
                continue

            mat_key = self._normalize_material_key(raw_str)
            material_to_hoppers.setdefault(mat_key, []).append(hopper_act)
            hopper_to_material_raw[hopper_act] = raw_str

        for k in list(material_to_hoppers.keys()):
            material_to_hoppers[k] = sorted(set(material_to_hoppers[k]))

        return material_to_hoppers, hopper_to_material_raw

    @staticmethod
    def _clean_col_name(v) -> str:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return ""
        s = re.sub(r"\s+", "_", str(v).strip()).upper()
        m = re.fullmatch(r"HOPPER_0*(\d+)_(SP|ACT)", s)
        if m:
            return f"HOPPER_{int(m.group(1))}_{m.group(2)}"
        return s

    @staticmethod
    def _clean_material_raw(v) -> str:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return ""
        return re.sub(r"\s+", " ", str(v).strip())

    @staticmethod
    def _normalize_material_key(raw_material: str) -> str:
        s = raw_material.lower().replace("-", "_").replace("/", "_")
        s = re.sub(r"\s+", "_", s)
        s = re.sub(r"[^a-z0-9_]", "", s)
        return f"{s}_mt"
