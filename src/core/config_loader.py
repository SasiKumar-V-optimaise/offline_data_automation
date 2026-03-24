from __future__ import annotations

from pathlib import Path
from typing import Any, Dict
import os
import yaml
import re


_ENV_PATTERN = re.compile(r"^\$\{([A-Z0-9_]+)\}$")


def _deep_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(a)
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _expand_env(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, str):
        m = _ENV_PATTERN.match(value.strip())
        if m:
            return os.getenv(m.group(1), "")
    return value


def load_yaml(path: str | Path) -> Dict[str, Any]:
    p = Path(path).resolve()
    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_config(
    base_path: str = "src/config/base.yaml",
    secrets_path: str = "src/config/secrets.yaml",
    rm_path: str = "src/config/rm.yaml",
    dpr_path: str = "src/config/dpr.yaml",
    hot_metal_path: str = "src/config/hot_metal.yaml",
    rm_hm_path: str = "src/config/rm_hm.yaml",
):
    base = load_yaml(base_path)
    secrets = load_yaml(secrets_path)
    rm_file_cfg = load_yaml(rm_path)
    dpr_file_cfg = load_yaml(dpr_path)
    hm_file_cfg = load_yaml(hot_metal_path)
    rm_hm_file_cfg = load_yaml(rm_hm_path)

    # Merge base + secrets
    merged = _deep_merge(base, secrets)

    # -----------------------------
    # RM CONFIG
    # -----------------------------
    merged["rm"] = rm_file_cfg["rm"] if "rm" in rm_file_cfg else rm_file_cfg

    # -----------------------------
    # DPR CONFIG
    # -----------------------------
    merged["dpr"] = dpr_file_cfg["dpr"] if "dpr" in dpr_file_cfg else dpr_file_cfg

    # -----------------------------
    # HOT METAL CONFIG
    # -----------------------------
    merged["hot_metal"] = (
        hm_file_cfg["hot_metal"] if "hot_metal" in hm_file_cfg else hm_file_cfg
    )

    # -----------------------------
    # RM_HM CONFIG
    # -----------------------------
    merged["rm_hm"] = rm_hm_file_cfg.get("rm_hm", {})
    merged["rm_hm_fields"] = rm_hm_file_cfg.get("rm_hm_fields", {})

    return _expand_env(merged)
