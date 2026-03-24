# src/domains/charge/service.py
import os
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass, field

import pandas as pd
from influxdb_client import InfluxDBClient

from .reader import ChargeExcelReader
from .processor import ChargeProcessor, ChargeProcessConfig
from .config_updater import ChargeConfigUpdater
from infrastructure.influx_client import InfluxClient


logger = logging.getLogger(__name__)


@dataclass
class ChargeServiceConfig:
    charge_yaml_path: str
    aggregates: Dict[str, List[str]] = field(default_factory=dict)
    rename_dict: Dict[str, str] = field(default_factory=dict)
    influx_cfg: dict = field(default_factory=dict)


class ChargeService:
    def __init__(self, cfg: ChargeServiceConfig):
        self.cfg = cfg

        # ✅ no materials.yaml / no aliasing
        self.reader = ChargeExcelReader()

        self.processor = ChargeProcessor(
            ChargeProcessConfig(
                aggregates=cfg.aggregates,
                rename_dict=cfg.rename_dict,
            )
        )

        self.config_updater = ChargeConfigUpdater(charge_yaml_path=cfg.charge_yaml_path)

    # def run(self, file_today: str, file_yesterday: Optional[str], run_date_str: str) -> pd.DataFrame:
    #     target_date = datetime.strptime(run_date_str, "%d-%b-%Y")
    #     file_paths = [p for p in (file_yesterday, file_today) if p]

    #     frames: List[pd.DataFrame] = []
    #     mappings: List[Dict[str, List[str]]] = []
    #     yaml_updates: List[Dict] = []

    #     for path in file_paths:
    #         parsed = self.reader.read(path)
    #         frames.append(parsed.df)
    #         mappings.append(parsed.material_to_hoppers)
    #         yaml_updates.append({
    #             "file": os.path.basename(path),
    #             "hopper_to_material_raw": parsed.hopper_to_material_raw,
    #             "material_to_hoppers": parsed.material_to_hoppers,
    #         })

    #     if yaml_updates:
    #         self.config_updater.update_target_and_previous(
    #             target_date=target_date.date(),
    #             target_mapping=yaml_updates[-1],
    #             previous_mapping=yaml_updates[0] if len(yaml_updates) > 1 else None,
    #         )

    #     agg = self.processor.process_files(frames, mappings, target_date)
    #     if agg.empty:
    #         return agg

    #     pci_df = self._fetch_pci_mt(target_date)
    #     if not pci_df.empty:
    #         agg = agg.merge(pci_df, on="DATETIME", how="left")
    #     else:
    #         agg["pci_mt"] = 0.0

    #     cols = ["DATETIME"] + [c for c in agg.columns if c not in ("DATETIME", "pci_mt")] + ["pci_mt"]
    #     return agg[cols]


    def run(self, file_today: str, file_yesterday: Optional[str], run_date_str: str) -> pd.DataFrame:
        target_date = datetime.strptime(run_date_str, "%d-%b-%Y")
        file_paths = [p for p in (file_yesterday, file_today) if p]

        frames: List[pd.DataFrame] = []
        mappings: List[Dict[str, List[str]]] = []
        yaml_updates: List[Dict] = []

        for path in file_paths:
            parsed = self.reader.read(path)
            frames.append(parsed.df)
            mappings.append(parsed.material_to_hoppers)
            yaml_updates.append({
                "file": os.path.basename(path),
                "hopper_to_material_raw": parsed.hopper_to_material_raw,
                "material_to_hoppers": parsed.material_to_hoppers,
            })

        if yaml_updates:
            self.config_updater.update_target_and_previous(
                target_date=target_date.date(),
                target_mapping=yaml_updates[-1],
                previous_mapping=yaml_updates[0] if len(yaml_updates) > 1 else None,
            )

        agg = self.processor.process_files(frames, mappings, target_date)
        if agg.empty:
            logger.warning("Charge aggregation produced no rows")
            return agg

        pci_df = self._fetch_pci_mt(target_date)
        if not pci_df.empty:
            agg = agg.merge(pci_df, on="DATETIME", how="left")
        else:
            agg["pci_mt"] = 0.0

        cols = ["DATETIME"] + [c for c in agg.columns if c not in ("DATETIME", "pci_mt")] + ["pci_mt"]
        agg = agg[cols]

        # ✅ WRITE TO INFLUX (FIX)
        influx_df = agg.rename(columns={"DATETIME": "date"})
        self._write_to_influx(influx_df)

        return agg

    def _fetch_pci_mt(self, target_date: datetime) -> pd.DataFrame:
        influx_cfg = self.cfg.influx_cfg
        if not influx_cfg:
            return pd.DataFrame()

        bucket = influx_cfg["online_bucket"]
        token = influx_cfg["online_token"]

        start_utc_dt = target_date - timedelta(hours=6, minutes=30)
        stop_utc_dt = target_date + timedelta(days=1) - timedelta(hours=5, minutes=30)

        start_str = start_utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        stop_str = stop_utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        query = f"""
import "math"
from(bucket: "{bucket}")
|> range(start: time(v: "{start_str}"), stop: time(v: "{stop_str}"))
|> filter(fn: (r) =>
    r._measurement == "process_params" and
    (r._field == "coal_rate_actual_value" or r._field == "production_per_hour")
)
|> map(fn: (r) => ({{ r with _value: math.abs(x: r._value) }}))
|> aggregateWindow(every: 1h, fn: mean, createEmpty: false, offset: 30m)
|> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
"""

        try:
            with InfluxDBClient(url=influx_cfg["url"], token=token, org=influx_cfg["org"]) as client:
                df = client.query_api().query_data_frame(query)
            if df.empty:
                return pd.DataFrame()

            df = df.rename(columns={"_time": "DATETIME"})
            df["DATETIME"] = (
                pd.to_datetime(df["DATETIME"], utc=True)
                .dt.tz_convert("Asia/Kolkata")
                .dt.floor("h")
                .dt.tz_localize(None)
            )
            df["pci_mt"] = (df["coal_rate_actual_value"] * df["production_per_hour"]) / 1000.0
            df = df[["DATETIME", "pci_mt"]]

            idx = pd.date_range(target_date, target_date + timedelta(days=1), freq="1h", inclusive="left")
            return df.set_index("DATETIME").reindex(idx).reset_index().rename(columns={"index": "DATETIME"})
        except Exception:
            logger.exception("Failed to fetch pci_mt")
            return pd.DataFrame()
        
    def _write_to_influx(self, df: pd.DataFrame):
        influx_cfg = self.cfg.influx_cfg
        if not influx_cfg:
            logger.warning("Influx config missing. Skipping InfluxDB write.")
            return

        if "date" not in df.columns:
            raise ValueError("Influx write requires 'date' column")

        client = InfluxClient(influx_cfg)

        try:
            client.write_dataframe(
                df=df,
                measurement="latest_charge_data",
                field_mapping=None,   # write all numeric fields
                tag_keys=None,
            )
            logger.info("Charge data written to InfluxDB successfully.")
        finally:
            client.close()

