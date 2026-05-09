from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Set, List

from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support import expected_conditions as EC

from core.logging import get_logger, LogTemplates

logger = get_logger(__name__)


# -------------------------------------------------
# CONFIG
# -------------------------------------------------
@dataclass(frozen=True)
class DownloadConfig:
    download_dir: str
    metadata_path: str
    file_station_url: str
    hourly_url: str
    portal_files: Dict[str, str]
    mode_keywords: Dict[str, List[str]] = None


# -------------------------------------------------
# UTILS
# -------------------------------------------------
def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    for fmt in ("%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except Exception:
            pass
    return None


# -------------------------------------------------
# DOWNLOADER
# -------------------------------------------------
class PortalDownloader:
    def __init__(self, selenium_client, cfg: DownloadConfig, logger):
        self.sc = selenium_client
        self.cfg = cfg
        self.logger = logger

    # -------------------------------------------------
    # METADATA
    # -------------------------------------------------
    def _load_metadata(self):
        if not os.path.exists(self.cfg.metadata_path):
            return {"root": {}, "hourly": {}}
        with open(self.cfg.metadata_path, "r") as f:
            return json.load(f)

    def _save_metadata(self, data):
        with open(self.cfg.metadata_path, "w") as f:
            json.dump(data, f, indent=2)

    def _normalize_name(self, name: str):
        return (
            name.lower()
            .replace("&", "and")
            .replace("\xa0", " ")
            .replace("  ", " ")
            .strip()
        )
    def _wait_for_rows(self, timeout=15):
        """Wait until actual file rows are loaded (not just DOM)"""
        for _ in range(timeout):
            rows = self._get_visible_rows()
            if rows and any(r["name"].strip() for r in rows):
                return rows
            time.sleep(1)

        return []

    # -------------------------------------------------
    # WAIT FOR DOWNLOAD
    # -------------------------------------------------
    def _wait_for_download(self, started_at: float, timeout: int = 240) -> bool:
        end = time.time() + timeout
        bad_ext = (".crdownload", ".tmp", ".part")
        d = os.path.expanduser(self.cfg.download_dir)

        while time.time() < end:
            for f in os.listdir(d):
                p = os.path.join(d, f)
                if os.path.isfile(p) and not f.endswith(bad_ext):
                    if os.path.getmtime(p) >= started_at:
                        return True
            time.sleep(1)
        return False

    # -------------------------------------------------
    # GET ROWS
    # -------------------------------------------------
    def _get_visible_rows(self):
        return self.sc.driver.execute_script("""
            return [...document.querySelectorAll('.x-grid3-row')].map(r=>{
                const t=[...r.querySelectorAll('.x-grid3-cell-inner')]
                            .map(c=>c.innerText.trim());
                return {
                    el: r,
                    name: t[0] || '',
                    modified: t[3] || ''
                };
            });
        """)

    # -------------------------------------------------
    # FIND LATEST FILE
    # -------------------------------------------------
    def _find_latest_matching_file(self, rows, keywords: List[str]):
        matches = []

        for r in rows:
            name = self._normalize_name(r["name"])
            keywords = [self._normalize_name(k) for k in keywords]

            if not name.strip():
                continue

            if all(k in name for k in keywords):
                dt = _parse_dt(r["modified"]) or datetime.min
                matches.append((r, dt))

        if not matches:
            return None

        matches.sort(key=lambda x: x[1], reverse=True)
        return matches[0][0]

    # -------------------------------------------------
    # DOWNLOAD WITH METADATA CHECK
    # -------------------------------------------------
    def _download_latest_file(self, url: str, keywords: List[str]) -> str:
        logger.info(LogTemplates.download(f"searching_keywords={keywords}"))

        self.sc.driver.get(url)
        self.sc.wait.until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )

        time.sleep(2)

        # ensure sorting
        try:
            self.sc.driver.find_element(
                By.XPATH, "//span[contains(text(),'Modified Date')]"
            ).click()
            time.sleep(1)
        except Exception:
            pass
        # wait for grid container first
        self.sc.wait.until(
            EC.presence_of_element_located((By.CLASS_NAME, "x-grid3-body"))
        )

        # then wait for actual rows
        rows = self._wait_for_rows()

        if not rows:
            logger.error(LogTemplates.failed("file_list_not_loaded"))
            return "failed"

        target = self._find_latest_matching_file(rows, keywords)

        if not target:
            logger.error(LogTemplates.failed(f"no_file_found_keywords={keywords}"))
            return "failed"

        metadata = self._load_metadata()
        name = self._normalize_name(target["name"])
        modified = target["modified"]

        logger.info(f"FILE | name={name} modified={modified}")

        prev_modified = metadata["root"].get(name)

        if prev_modified == modified:
            logger.info(LogTemplates.skipped(f"no_change={name}"))
            return "skipped"

        # ---------------- DOWNLOAD ----------------
        logger.info(LogTemplates.download(f"name={target['name']}"))

        start = time.time()
        self.sc.driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'})", target["el"]
        )
        ActionChains(self.sc.driver).double_click(target["el"]).perform()

        if not self._wait_for_download(start):
            logger.error(LogTemplates.failed("download_timeout"))
            return "failed"

        # ---------------- VALIDATE ----------------
        files = os.listdir(os.path.expanduser(self.cfg.download_dir))
        if not any(all(k in f.lower() for k in keywords) for f in files):
            logger.error(LogTemplates.failed("file_mismatch"))
            return "failed"

        # ---------------- UPDATE METADATA ----------------
        metadata["root"][name] = modified
        self._save_metadata(metadata)

        logger.info(f"METADATA | updated={name}")

        return "downloaded"

    # -------------------------------------------------
    # RETRY WRAPPER
    # -------------------------------------------------
    def _safe_download(self, url, keywords):
        for attempt in range(3):
            result = self._download_latest_file(url, keywords)
            if result in ("downloaded", "skipped"):
                return result
            logger.warning(f"RETRY | attempt={attempt+1}/3 keywords={keywords}")
            time.sleep(3)
        return "failed"

    # -------------------------------------------------
    # CHARGE
    # -------------------------------------------------
    def _scroll_and_download_charge(self, url: str, required_files: Set[str]) -> Set[str]:
        skipped = set()

        self.sc.driver.get(url)
        self.sc.wait.until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )

        panel = self.sc.wait.until(
            EC.presence_of_element_located((By.CLASS_NAME, "x-grid3-scroller"))
        )

        found = set()

        for _ in range(30):
            rows = self._get_visible_rows()

            for r in rows:
                name = r["name"]

                if name in required_files and name not in found:
                    logger.info(LogTemplates.download(f"charge={name}"))

                    start = time.time()
                    self.sc.driver.execute_script(
                        "arguments[0].scrollIntoView({block:'center'})", r["el"]
                    )
                    ActionChains(self.sc.driver).double_click(r["el"]).perform()

                    if self._wait_for_download(start):
                        found.add(name)

                        if found == required_files:
                            return skipped

            self.sc.driver.execute_script("arguments[0].scrollTop += 1000", panel)
            time.sleep(0.5)

        return skipped | (required_files - found)

    # -------------------------------------------------
    # MAIN ENTRY
    # -------------------------------------------------
    def download(self, modes, run_dates, is_today_mode):
        skipped = set()

        mode_keywords = self.cfg.mode_keywords

        for m in modes:
            if m == "charge":
                continue

            keywords = mode_keywords.get(m)
            if not keywords:
                continue

            result = self._safe_download(
                self.cfg.file_station_url,
                keywords
            )

            if result == "failed":
                skipped.add(m)

            elif result == "skipped":
                logger.info(LogTemplates.skipped(f"mode={m}"))

        # ---------------- CHARGE ----------------
        if "charge" in modes:
            if is_today_mode:
                dates = {datetime.today()}
            else:
                dates = {datetime.strptime(rd, "%d-%b-%Y") for rd in run_dates}

            required = {
                f"CHARGE_AND_DUMP_REPORT_{d.day}_{d.month}_{d.year}.xlsx"
                for d in dates
            }

            skipped |= self._scroll_and_download_charge(
                self.cfg.hourly_url,
                required
            )

        return skipped