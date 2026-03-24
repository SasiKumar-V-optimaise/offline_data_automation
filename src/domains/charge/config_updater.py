import os
import yaml
import logging
from datetime import datetime, date, timedelta
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class ChargeConfigUpdater:
    """
    Stores only two dynamic mappings in charge.yaml:
    - target_day
    - previous_day

    BUT preserves user/static keys like:
    - rename_dict
    - aggregates
    """

    PRESERVE_KEYS = ("rename_dict", "aggregates", "material_groups")  # keep backward compat

    def __init__(self, charge_yaml_path: str):
        self.charge_yaml_path = charge_yaml_path

    def update_target_and_previous(
        self,
        target_date: date,
        target_mapping: Dict,
        previous_mapping: Optional[Dict] = None,
        previous_date: Optional[date] = None,
    ) -> None:
        path = self.charge_yaml_path
        os.makedirs(os.path.dirname(path), exist_ok=True)

        current = {}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                current = yaml.safe_load(f) or {}

        # ✅ keep user/static keys from existing yaml
        preserved = {k: current.get(k) for k in self.PRESERVE_KEYS if k in current}

        if previous_date is None:
            previous_date = target_date - timedelta(days=1)

        new_yaml = {
            **preserved,  # ✅ keep rename_dict / aggregates
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "target_day": {
                "date": target_date.isoformat(),
                **target_mapping,
            },
            "previous_day": None,
        }

        if previous_mapping:
            new_yaml["previous_day"] = {
                "date": previous_date.isoformat(),
                **previous_mapping,
            }
        else:
            existing_prev = current.get("previous_day")
            if existing_prev and existing_prev.get("date") == previous_date.isoformat():
                new_yaml["previous_day"] = existing_prev

        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(new_yaml, f, sort_keys=False, allow_unicode=True)

        logger.info(
            "charge.yaml updated (target=%s, previous=%s)",
            new_yaml["target_day"]["date"],
            new_yaml["previous_day"]["date"] if new_yaml["previous_day"] else None,
        )
