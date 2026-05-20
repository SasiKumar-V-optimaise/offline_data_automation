from __future__ import annotations

import re
from typing import Any

import pandas as pd


class FinesAnalysisTransformer:
    VALID_SHIFTS = {"A", "B", "C"}
    SIZE_COLUMN_SPECS = (
        ("plus", 100, "plus_100mm"),
        ("plus", 90, "plus_90mm"),
        ("plus", 80, "plus_80mm"),
        ("plus", 70, "plus_70mm"),
        ("plus", 60, "plus_60mm"),
        ("plus", 50, "plus_50mm"),
        ("plus", 40, "plus_40mm"),
        ("plus", 30, "plus_30mm"),
        ("plus", 25, "plus_25mm"),
        ("plus", 20, "plus_20mm"),
        ("plus", 16, "plus_16mm"),
        ("plus", 12, "plus_12mm"),
        ("plus", 10, "plus_10mm"),
        ("plus", 8, "plus_8mm"),
        ("plus", 6, "plus_6mm"),
        ("plus", 5, "plus_5mm"),
        ("minus", 10, "minus_10mm"),
        ("minus", 6, "minus_6mm"),
        ("minus", 5, "minus_5mm"),
    )
    SIZE_COLUMNS = {
        (direction, size): column
        for direction, size, column in SIZE_COLUMN_SPECS
    }
    OUTPUT_SIZE_COLUMNS = [
        column for _, _, column in SIZE_COLUMN_SPECS
    ]

    def __init__(self, logger):
        self.logger = logger

    def transform(
        self,
        df: pd.DataFrame,
        cfg: dict[str, Any],
        date_list: list,
        shift_time: dict[str, str],
    ) -> pd.DataFrame:
        material_code = cfg["material_code"]
        date_col = self._find_column(df, "DATE")
        if not date_col:
            self.logger.warning(f"No DATE column found for {cfg['sheet_name']}")
            return pd.DataFrame()

        size_column_map = self._size_column_map(df.columns)
        if not size_column_map:
            self.logger.warning(f"No supported size-analysis columns found for {cfg['sheet_name']}")
            return pd.DataFrame()

        out = pd.DataFrame()
        out["recorded_date"] = pd.to_datetime(
            df[date_col].ffill(),
            errors="coerce",
            dayfirst=True,
            format="mixed",
        ).dt.date

        shift_col = self._find_column(df, "SHIFT")
        if shift_col:
            out["shift"] = (
                df[shift_col]
                .astype(str)
                .str.strip()
                .str.upper()
                .str.extract(r"^([ABC])", expand=False)
            )
        else:
            out["shift"] = pd.NA

        for source_col, target_col in size_column_map.items():
            values = self._replace_invalid_markers(df[source_col], cfg)
            values = pd.to_numeric(values, errors="coerce")
            if target_col in out:
                out[target_col] = out[target_col].combine_first(values)
            else:
                out[target_col] = values

        out = out[out["recorded_date"].isin(date_list)]
        if shift_col:
            out = out[out["shift"].isin(self.VALID_SHIFTS)]

        size_cols = self.output_size_columns(out)
        out = out.dropna(subset=size_cols, how="all")
        if out.empty:
            return pd.DataFrame()

        out["date_time"] = self._build_date_time(
            out["recorded_date"],
            out["shift"],
            shift_time,
            cfg.get("default_time", "00:00"),
        )
        out["material_code"] = material_code

        result_cols = ["date_time", "material_code"] + size_cols
        out = out[result_cols].dropna(subset=["date_time"])

        for col in size_cols:
            out[col] = out[col].round(3)

        return (
            out.groupby(["date_time", "material_code"], as_index=False, dropna=False)
            .mean(numeric_only=True)
            .reindex(columns=result_cols)
        )

    def _build_date_time(
        self,
        dates: pd.Series,
        shifts: pd.Series,
        shift_time: dict[str, str],
        default_time: str,
    ) -> pd.Series:
        timestamps = []
        for recorded_date, shift in zip(dates, shifts):
            if pd.isna(recorded_date):
                timestamps.append(pd.NaT)
                continue

            shift_value = None if pd.isna(shift) else str(shift).strip().upper()
            time_text = shift_time.get(shift_value, default_time)
            timestamp = pd.to_datetime(f"{recorded_date} {time_text}", errors="coerce")

            if shift_value == "C" and pd.notna(timestamp):
                timestamp -= pd.Timedelta(days=1)

            timestamps.append(timestamp)

        return pd.Series(timestamps, index=dates.index)

    @classmethod
    def _size_column_map(cls, columns) -> dict[str, str]:
        out: dict[str, str] = {}
        for col in columns:
            parsed = cls._parse_size_header(str(col))
            if parsed is None:
                continue
            target_col = cls.SIZE_COLUMNS.get(parsed)
            if target_col:
                out[str(col)] = target_col
        return out

    @classmethod
    def output_size_columns(cls, df: pd.DataFrame) -> list[str]:
        return [col for col in cls.OUTPUT_SIZE_COLUMNS if col in df.columns]

    @classmethod
    def _parse_size_header(cls, header: str) -> tuple[str, int] | None:
        normalized = cls._normalize_size_header(header)
        if "MESH" in normalized:
            return None

        patterns = (
            ("plus", r"(?:\+|PLUS)\s*0*(\d+)\s*(?:MM)?\b"),
            ("minus", r"(?:-|MINUS|BELOW|<)\s*0*(\d+)\s*(?:MM)?\b"),
        )
        for direction, pattern in patterns:
            match = re.search(pattern, normalized)
            if match:
                return direction, int(match.group(1))

        return None

    @staticmethod
    def _normalize_size_header(header: str) -> str:
        return (
            str(header)
            .upper()
            .replace("\u00a0", " ")
            .replace("\u2212", "-")
            .replace("\u2010", "-")
            .replace("\u2011", "-")
            .replace("\u2012", "-")
            .replace("\u2013", "-")
            .replace("\u2014", "-")
            .replace("%", " ")
            .replace("(", " ")
            .replace(")", " ")
            .replace("_", " ")
        )

    @staticmethod
    def _normalize_column_name(value: str) -> str:
        return re.sub(r"[^A-Z0-9]+", "", str(value).upper())

    def _find_column(self, df: pd.DataFrame, name: str) -> str | None:
        target = self._normalize_column_name(name)
        for col in df.columns:
            if self._normalize_column_name(col) == target:
                return col
        return None

    @staticmethod
    def _replace_invalid_markers(values: pd.Series, cfg: dict[str, Any]) -> pd.Series:
        markers = {str(value).strip().upper() for value in cfg.get("invalid_markers", [])}
        return values.mask(values.astype(str).str.strip().str.upper().isin(markers), pd.NA)
