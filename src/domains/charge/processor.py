import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class ChargeProcessConfig:
    aggregates: Dict[str, List[str]]     # e.g. {"ore_mt": [...], "flux_mt": [...]}
    rename_dict: Dict[str, str]          # e.g. {"online_mt": "coke_online_mt"}


class ChargeProcessor:
    def __init__(self, cfg: ChargeProcessConfig):
        self.cfg = cfg

    def process_files(
        self,
        file_frames: List[pd.DataFrame],
        file_mappings: List[Dict[str, List[str]]],
        target_date: datetime,
    ) -> pd.DataFrame:
        start = target_date
        end = target_date + timedelta(days=1)

        parts: List[pd.DataFrame] = []

        for df, mapping in zip(file_frames, file_mappings):
            if df is None or df.empty:
                continue

            df = df.copy()
            df["DATETIME"] = df["DATETIME"].dt.floor("h") + pd.Timedelta(hours=1)

            act_cols = [c for c in df.columns if c.startswith("HOPPER_") and c.endswith("_ACT")]
            if not act_cols:
                continue

            hourly = df.groupby("DATETIME")[act_cols].sum().reset_index()
            hourly = hourly[(hourly["DATETIME"] >= start) & (hourly["DATETIME"] < end)]
            if hourly.empty:
                continue

            # hopper -> material MT
            rows = []
            for _, r in hourly.iterrows():
                out = {"DATETIME": r["DATETIME"]}
                for mat_key, hopper_list in mapping.items():
                    present = [h for h in hopper_list if h in hourly.columns]
                    out[mat_key] = (r[present].sum() / 1000.0) if present else 0.0
                rows.append(out)

            parts.append(pd.DataFrame(rows))

        if not parts:
            return pd.DataFrame()

        agg = (
            pd.concat(parts, ignore_index=True)
            .groupby("DATETIME", as_index=False)
            .sum(numeric_only=True)
        )

        # Ensure 24 hours
        idx = pd.date_range(start, end, freq="1h", inclusive="left")
        agg = agg.set_index("DATETIME").reindex(idx).fillna(0.0)
        agg.index.name = "DATETIME"
        agg = agg.reset_index()

        # ✅ STEP A: aggregates FIRST (use raw/original keys)
        for group_name, children in (self.cfg.aggregates or {}).items():
            cols_present = [c for c in children if c in agg.columns]
            agg[group_name] = agg[cols_present].sum(axis=1) if cols_present else 0.0
            print(f"Aggregate '{group_name}' from columns: {cols_present}")

        # ✅ STEP B: rename LAST (final Excel headings)
        if self.cfg.rename_dict:
            agg = agg.rename(columns={k: v for k, v in self.cfg.rename_dict.items() if k in agg.columns})

        return agg
