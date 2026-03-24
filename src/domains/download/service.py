from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Set

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support import expected_conditions as EC
import logging

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
    portal_files: Dict[str, str]  # mode -> visible name


# -------------------------------------------------
# METADATA
# -------------------------------------------------
class MetadataStore:
    def __init__(self, path: str):
        self.path = path

    def load(self) -> Dict:
        if not os.path.exists(self.path):
            return {}

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return {}
                return json.loads(content)
        except Exception:
            return {}

    def save(self, data: Dict) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)


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
        self.meta = MetadataStore(cfg.metadata_path)
    
    def _normalize_name(self, name: str) -> str:
        import re
        name = name.lower().strip()
        name = re.sub(r"\(\d+\)", "", name)   # remove (1), (2)
        name = re.sub(r"\s+", " ", name)      # normalize spaces
        return name
    def _month_key_from_date(self, run_date: str) -> str:
        dt = datetime.strptime(run_date, "%d-%b-%Y")
        return dt.strftime("%b'%y").lower()   # mar'26
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
    # GENERIC SCROLLER (PREFIX / FAMILY FILES)
    # -------------------------------------------------
    def _scroll_and_download_family(
        self, url: str, prefixes: Set[str], meta_key: str, run_dates: list[str], modes: list[str]
    ) -> Set[str]:
        if "dpr" in modes:
            target_month = self._month_key_from_date(run_dates[0])
        else: 
            target_month = None
        skipped = set()
        meta = self.meta.load()
        meta.setdefault(meta_key, {})

        patterns = [re.compile(rf"^{re.escape(p)}.*", re.I) for p in prefixes]
        remaining = set(patterns)
        discovered = set()
        idle = 0

        self.sc.driver.get(url)
        self.sc.wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
        panel = self.sc.wait.until(EC.presence_of_element_located((By.CLASS_NAME, "x-grid3-scroller")))

        while remaining and idle < 5:
            rows = self.sc.driver.execute_script(
                """
                return [...document.querySelectorAll('.x-grid3-row')].map(r=>{
                  const t=[...r.querySelectorAll('.x-grid3-cell-inner')].map(c=>c.innerText.trim());
                  return {el:r,name:t[0]||'',modified:t[3]||''};
                });
                """
            )

            found_new = False

            for r in rows:
                name = r["name"]
                if target_month and target_month not in name.lower():
                    continue
                if not name or name in discovered:
                    continue

                discovered.add(name)
                found_new = True

                match = next((p for p in remaining if p.match(name)), None)
                if not match:
                    continue

                cur = _parse_dt(r["modified"]) or datetime.now()
                norm_name = self._normalize_name(name)
                # normalize metadata keys
                meta_norm = {
                    self._normalize_name(k): v
                    for k, v in meta[meta_key].items()
                }

                prev = _parse_dt(meta_norm.get(norm_name))
                if prev and cur <= prev:
                    self.logger.info(f"Skipping (no change): {name}")
                    remaining.remove(match)
                    continue

                self.logger.info("Downloading: %s", name)
                start = time.time()
                self.sc.driver.execute_script("arguments[0].scrollIntoView({block:'center'})", r["el"])
                ActionChains(self.sc.driver).double_click(r["el"]).perform()

                if self._wait_for_download(start):
                    meta[meta_key][norm_name] = cur.strftime("%Y-%m-%d %H:%M:%S")
                else:
                    skipped.add(name)

                self.meta.save(meta)
                remaining.remove(match)

            idle = idle + 1 if not found_new else 0
            ActionChains(self.sc.driver).move_to_element(panel).click().send_keys(Keys.PAGE_DOWN).perform()
            time.sleep(0.6)

        skipped |= {p.pattern for p in remaining}
        return skipped

    # -------------------------------------------------
    # CHARGE (EXACT FILES ONLY)
    # -------------------------------------------------
    def _scroll_and_download_charge(
        self, url: str, required_files: Set[str]
    ) -> Set[str]:
        skipped = set()
        meta = self.meta.load()
        meta.setdefault("hourly", {})

        remaining = set(required_files)
        discovered = set()
        idle = 0

        self.sc.driver.get(url)
        self.sc.wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
        panel = self.sc.wait.until(EC.presence_of_element_located((By.CLASS_NAME, "x-grid3-scroller")))

        while remaining and idle < 5:
            rows = self.sc.driver.execute_script(
                """
                return [...document.querySelectorAll('.x-grid3-row')].map(r=>{
                  const t=[...r.querySelectorAll('.x-grid3-cell-inner')].map(c=>c.innerText.trim());
                  return {el:r,name:t[0]||'',modified:t[3]||''};
                });
                """
            )

            found_new = False

            for r in rows:
                name = r["name"]
                if not name or name in discovered:
                    continue

                discovered.add(name)
                found_new = True

                if name not in remaining:
                    continue

                cur = _parse_dt(r["modified"]) or datetime.now()
                prev = _parse_dt(meta["hourly"].get(name))

                if prev and cur <= prev:
                    remaining.remove(name)
                    continue

                self.logger.info("Downloading CHARGE: %s", name)
                start = time.time()
                self.sc.driver.execute_script("arguments[0].scrollIntoView({block:'center'})", r["el"])
                ActionChains(self.sc.driver).double_click(r["el"]).perform()

                if self._wait_for_download(start):
                    meta["hourly"][name] = cur.strftime("%Y-%m-%d %H:%M:%S")
                else:
                    skipped.add(name)

                self.meta.save(meta)
                remaining.remove(name)

            idle = idle + 1 if not found_new else 0
            ActionChains(self.sc.driver).move_to_element(panel).click().send_keys(Keys.PAGE_DOWN).perform()
            time.sleep(0.6)

        skipped |= remaining
        return skipped

    # -------------------------------------------------
    # MAIN ENTRY
    # -------------------------------------------------
    def download(self, modes: list[str], run_dates: list[str], is_today_mode: bool) -> Set[str]:
        skipped = set()

        # ---------- ROOT FILES ----------
        root_prefixes = {
            self.cfg.portal_files[m]
            for m in modes
            if m in self.cfg.portal_files and m != "charge"
        }

        if root_prefixes:
            skipped |= self._scroll_and_download_family(
                self.cfg.file_station_url, root_prefixes, meta_key="root", run_dates=run_dates,modes=modes
            )

        # ---------- CHARGE ----------
        if "charge" in modes:
            dates = set()
            if is_today_mode:
                today = datetime.today()
                dates |= {today, today - timedelta(days=1)}
            else:
                for rd in run_dates:
                    d = datetime.strptime(rd, "%d-%b-%Y")
                    dates |= {d, d - timedelta(days=1)}

            required = set()
            for d in dates:
                for stem in (
                    f"CHARGE_AND_DUMP_REPORT_{d.day}_{d.month}_{d.year}",
                    f"CHARGE_AND_DUMP_REPORT_{d.day:02d}_{d.month:02d}_{d.year}",
                ):
                    required |= {stem + ".xls", stem + ".xlsx"}

            skipped |= self._scroll_and_download_charge(
                    self.cfg.hourly_url,
                    required
                )
        return skipped
