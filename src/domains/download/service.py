from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Set, List

from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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
        for _ in range(timeout):
            rows = self._get_visible_rows()
            if rows and any(r["name"].strip() for r in rows):
                return rows
            time.sleep(1)
        return []

    # -------------------------------------------------
    # WAIT FOR DOWNLOAD
    # -------------------------------------------------
    def _wait_for_download(self, started_at: float, expected_name: str, timeout: int = 240) -> bool:
        end = time.time() + timeout
        d = os.path.expanduser(self.cfg.download_dir)

        while time.time() < end:
            files = os.listdir(d)

            for f in files:
                # ignore temp files
                if f.endswith((".crdownload", ".tmp", ".part")):
                    continue

                # must match expected file
                if expected_name.lower() in f.lower():
                    p = os.path.join(d, f)

                    # ensure file is stable (size not changing)
                    size1 = os.path.getsize(p)
                    time.sleep(1)
                    size2 = os.path.getsize(p)

                    if size1 == size2:
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
        self.logger.info(f"Searching latest file using keywords: {keywords}")

        self.sc.driver.get(url)
        self.sc.wait.until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )

        time.sleep(2)

        try:
            self.sc.driver.find_element(
                By.XPATH, "//span[contains(text(),'Modified Date')]"
            ).click()
            time.sleep(1)
        except Exception:
            pass

        self.sc.wait.until(
            EC.presence_of_element_located((By.CLASS_NAME, "x-grid3-body"))
        )

        rows = self._wait_for_rows()

        if not rows:
            self.logger.error("File list not loaded (timeout)")
            return "failed"

        target = self._find_latest_matching_file(rows, keywords)

        if not target:
            self.logger.error(f"No file found for {keywords}")
            return "failed"

        metadata = self._load_metadata()
        name = self._normalize_name(target["name"])
        modified = target["modified"]

        self.logger.info(f"Latest file: {name} | Modified: {modified}")

        prev_modified = metadata["root"].get(name)

        if prev_modified == modified:
            self.logger.info(f"SKIPPED (no change): {name}")
            return "skipped"

        self.logger.info(f"Downloading: {target['name']}")

        start = time.time()
        self.sc.driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'})", target["el"]
        )
        ActionChains(self.sc.driver).double_click(target["el"]).perform()

        if not self._wait_for_download(start, name):
            self.logger.error("Download failed")
            return "failed"

        files = os.listdir(os.path.expanduser(self.cfg.download_dir))
        if not any(all(k in f.lower() for k in keywords) for f in files):
            self.logger.error("Downloaded file mismatch")
            return "failed"

        metadata["root"][name] = modified
        self._save_metadata(metadata)

        self.logger.info(f"Metadata updated for: {name}")

        return "downloaded"

    # -------------------------------------------------
    # RETRY WRAPPER
    # -------------------------------------------------
    def _safe_download(self, url, keywords):
        for attempt in range(3):
            result = self._download_latest_file(url, keywords)
            if result in ("downloaded", "skipped"):
                return result
            self.logger.warning(f"Retry {attempt+1}/3 for {keywords}")
            time.sleep(3)
        return "failed"

    # -------------------------------------------------
    # CHARGE
    # -------------------------------------------------
    def _scroll_and_download_charge(self, url: str, run_dates: list) -> Set[str]:
        self.sc.driver.get(url)
        self.sc.wait.until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
        time.sleep(1)

        try:
            panel = self.sc.wait.until(
                EC.presence_of_element_located((By.CLASS_NAME, "x-grid3-scroller"))
            )
        except Exception:
            self.logger.error("Could not locate scroll panel.")
            self.sc.stop()
            return {"charge_and_dump"}

        candidates = set()
        stems = []
        for rd in run_dates:
            dt = datetime.strptime(rd, "%d-%b-%Y")
            for stem in (
                f"CHARGE_AND_DUMP_REPORT_{dt.day}_{dt.month}_{dt.year}",
            ):
                candidates |= { stem + ".xlsx"}
                stems.append(stem)

        self.logger.info(f"Looking for: {', '.join(sorted(candidates))}")

        seen: Set[str] = set()
        found = False
        skipped: Set[str] = set()

        for _ in range(60):
            rows = self.sc.driver.execute_script("""
                return [...document.querySelectorAll('.x-grid3-body .x-grid3-row')].map(r=>{
                    const t=[...r.querySelectorAll('.x-grid3-cell-inner')].map(c=>c.innerText.trim());
                    return {el:r, n:t[0]||'', m:t[3]||''};
                });
            """)

            for r in rows:
                name = r["n"].strip()
                if not name or name in seen:
                    continue
                seen.add(name)

                if not (
                    name in candidates
                    or any(
                        name.startswith(s) and name.lower().endswith((".xlsx"))
                        for s in stems
                    )
                ):
                    continue

                self.logger.info(f"Found charge file: {name}")
                self.sc.driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'})", r["el"]
                )
                start = time.time()
                ActionChains(self.sc.driver).move_to_element(r["el"]).double_click(r["el"]).perform()

                if self._wait_for_download(start, name):
                    self.logger.info(f"Downloaded: {name}")
                    found = True
                else:
                    self.logger.error(f"Download failed: {name}")
                    skipped.add(name)
                break

            if found or skipped:
                break

            ActionChains(self.sc.driver).move_to_element(panel).click().send_keys(Keys.PAGE_DOWN).perform()
            time.sleep(0.6)

        if not found and not skipped:
            self.logger.warning("Charge file not found after scrolling.")
            skipped.add("charge_and_dump")

        self.sc.stop()
        return skipped

    # -------------------------------------------------
    # MAIN ENTRY
    # -------------------------------------------------
    def download(self, modes: list[str], run_dates: list[str], is_today_mode: bool) -> Set[str]:
        skipped = set()

        mode_keywords = {
            "rm": ["bf-02", "bunker"],
            "dpr": ["bf-02", "dpr"],
            "hot_metal": ["bf-02", "hot", "metal"],
            "rm_hm": ["rm", "hm"],
            "rm_stock": ["bulk", "stock"],
        }

        for m in modes:
            if m == "charge":
                continue

            keywords = mode_keywords.get(m)
            if not keywords:
                continue

            result = self._safe_download(self.cfg.file_station_url, keywords)

            if result == "failed":
                skipped.add(m)
            elif result == "skipped":
                self.logger.info(f"{m} skipped (no update)")

        # ---------------- CHARGE ----------------
        if "charge" in modes:
            charge_dates = (
                [datetime.today().strftime("%d-%b-%Y")]
                if is_today_mode
                else run_dates
            )
            skipped |= self._scroll_and_download_charge(
                self.cfg.hourly_url,
                charge_dates,
            )

        return skipped
