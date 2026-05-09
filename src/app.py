# src/app.py
import argparse
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
import re

import yaml

from core.config_loader import load_config
from core.logging import setup_logging, get_logger, LogTemplates
from infrastructure.selenium_client import SeleniumClient, SeleniumConfig
from domains.download.service import PortalDownloader, DownloadConfig

from domains.rm.service import RMService
from domains.dpr.service import DPRService
from domains.hot_metal.service import HotMetalService
from domains.rm_hm.service import RMHMService
from domains.charge.service import ChargeService, ChargeServiceConfig


# -------------------------------------------------
# ARGUMENT PARSING
# -------------------------------------------------
def parse_args():
    parser = argparse.ArgumentParser("Offline Data Automation")

    parser.add_argument(
        "--mode",
        required=True,
        help="Comma separated modes. Supported: rm, dpr, hot_metal, rm_hm, charge",
    )

    parser.add_argument(
        "--today",
        action="store_true",
        help="Use today as run date",
    )

    parser.add_argument(
        "--rundate",
        type=str,
        help="DD-Mon-YYYY | DD-MM-YYYY | 'DD-MM-YYYY to DD-MM-YYYY'",
    )

    return parser.parse_args()


# -------------------------------------------------
# DATE PARSING (SINGLE + RANGE)
# -------------------------------------------------
def parse_run_dates(raw: str | None, today: bool) -> list[str]:
    if today:
        return [datetime.today().strftime("%d-%b-%Y")]

    if not raw:
        raise SystemExit("Provide --today or --rundate")

    raw = raw.strip()

    if "to" in raw.lower():
        start_raw, end_raw = [x.strip() for x in raw.lower().split("to")]

        start = datetime.strptime(start_raw, "%d-%m-%Y").date()
        end = datetime.strptime(end_raw, "%d-%m-%Y").date()

        if start > end:
            raise SystemExit("Start date must be before end date")

        days = (end - start).days + 1
        return [(start + timedelta(days=i)).strftime("%d-%b-%Y") for i in range(days)]

    for fmt in ("%d-%b-%Y", "%d-%m-%Y"):
        try:
            return [datetime.strptime(raw, fmt).strftime("%d-%b-%Y")]
        except ValueError:
            pass

    raise SystemExit(
        "Invalid --rundate.\n"
        "Use:\n"
        "  DD-Mon-YYYY\n"
        "  DD-MM-YYYY\n"
        "  DD-MM-YYYY to DD-MM-YYYY"
    )


def _load_charge_user_cfg(charge_yaml_path: Path) -> dict:
    """
    Loads rename_dict + aggregates from src/config/charge.yaml
    so they persist across runs and are applied in processor.

    Your ChargeConfigUpdater must preserve these keys.
    """
    if not charge_yaml_path.exists():
        return {}

    with open(charge_yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    return {
        "rename_dict": data.get("rename_dict", {}) or {},
        "aggregates": data.get("aggregates", {}) or {},
    }


# -------------------------------------------------
# MAIN
# -------------------------------------------------
def main():
    start_time = time.time()
    args = parse_args()
    cfg = load_config()

    # Setup central logging
    setup_logging()
    logger = get_logger("offline")

    modes = [m.strip().lower() for m in args.mode.split(",") if m.strip()]
    valid_modes = {"rm", "dpr", "hot_metal", "rm_hm", "charge", "rm_stock", "rm_postgresql", "dpr_postgresql", "hot_metal_postgresql", "rm_hm_postgresql", "charge_postgresql", "rm_stock_postgresql"}

    invalid = set(modes) - valid_modes
    if invalid:
        raise SystemExit(f"Unsupported modes: {sorted(invalid)}")

    # -------------------------------------------------
    # RESOLVE RUN DATES
    # -------------------------------------------------
    run_dates = parse_run_dates(args.rundate, args.today)
    logger.info(LogTemplates.start(args.mode, run_dates[0]))

    # -------------------------------------------------
    # DOWNLOAD STEP (ONCE)
    # -------------------------------------------------
    logger.info("START | step=download")

    selenium = SeleniumClient(
        SeleniumConfig(default_timeout=int(cfg["download"]["default_timeout"]))
    )

    downloader = PortalDownloader(
        selenium,
        DownloadConfig(
            download_dir=cfg["download"]["download_dir"],
            metadata_path=cfg["download"]["metadata_path"],
            file_station_url=cfg["eml"]["file_station_url"],
            hourly_url=cfg["eml"]["hourly_url"],
            portal_files=cfg["portal_files"],
            mode_keywords=cfg["portal"].get("mode_keywords", {}),
        ),
        logger,
    )

    try:
        selenium.start()
        selenium.login(
            login_url=cfg["eml"]["login_url"],
            user=cfg["eml"]["user"],
            password=cfg["eml"]["password"],
        )
        # skipped = downloader.download(modes=modes, run_date_str=run_dates[0], is_today_mode=bool(args.today))


        skipped = downloader.download(
            modes=modes,
            run_dates=run_dates,
            is_today_mode=bool(args.today),
        )


        logger.info(f"DOWNLOAD | skipped={sorted(skipped)}")

    finally:
        selenium.stop()

    download_dir = Path(cfg["download"]["download_dir"]).expanduser()

    # -------------------------------------------------
    # RM
    # -------------------------------------------------
    if "rm" in modes:
        rm_files = sorted(
            download_dir.glob("*BUNKER*.xlsx"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if rm_files:
            RMService(logger).process(str(rm_files[0]), cfg, run_dates)

    # -------------------------------------------------
    # DPR
    # -------------------------------------------------
    if "dpr" in modes:
        dpr_files = sorted(
            download_dir.glob("*DPR*.xlsx"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if dpr_files:
            DPRService(logger).process(str(dpr_files[0]), cfg, run_dates)

    # -------------------------------------------------
    # HOT METAL
    # -------------------------------------------------
    if "hot_metal" in modes:
        hm_files = sorted(
            download_dir.glob("*HOT METAL*.xlsx"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if hm_files:
            HotMetalService(logger).process(str(hm_files[0]), cfg, run_dates)

    # -------------------------------------------------
    # RM & HM
    # -------------------------------------------------
    if "rm_hm" in modes:
        rm_hm_files = sorted(
            download_dir.glob("*RM & HM*.xlsx"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if rm_hm_files:
            RMHMService(logger).process(str(rm_hm_files[0]), cfg, run_dates)
        else:
            logger.warning(LogTemplates.skipped("no_rm_hm_file"))

    # -------------------------------------------------
    # RM STOCK
    # -------------------------------------------------
    if "rm_stock" in modes:
        stock_files = sorted(
            download_dir.glob("RM BULK STOCK*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
            
        )

        if not stock_files:
            logger.warning(LogTemplates.skipped("no_rm_stock_file"))
        else:
            from domains.rm_stock.service import RMStockService

            RMStockService(logger).process(
                file_path=str(stock_files[0]),
                cfg=cfg,
                run_dates=run_dates,
            )

    # -------------------------------------------------
    # CHARGE
    # -------------------------------------------------

    if "charge" in modes:
        charge_files = sorted(
            download_dir.glob(f"CHARGE_AND_DUMP_REPORT_*.xlsx"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        neon_cfg = cfg["neon_dev"]

        charge_service = ChargeService(
            ChargeServiceConfig(
            output_dir="outputs",
            neon_dev_cfg=cfg["neon_dev"], 
            neondb_cfg=cfg["neondb"],  
            charge_cfg=cfg["charge"], 
            write_to_neon=True
            ),
            logger,
        )

        for run_date in run_dates:
            dt = datetime.strptime(run_date, "%d-%b-%Y")

            matching = [
                p for p in charge_files
                if f"CHARGE_AND_DUMP_REPORT_{dt.day}_{dt.month}_{dt.year}" in p.name
            ]

            if not matching:
                logger.error(LogTemplates.failed(f"charge_file_not_found={run_date}"))
                continue

            charge_service.run(
                charge_file=str(matching[0]),
                run_date_str=run_date,
            )

    # -------------------------------------------------
    # RM POSTGRESQL
    # -------------------------------------------------
    if "rm_postgresql" in modes:
        from domains.postgresql.rm.service import RMPostgreSQLService
        rm_postgresql_service = RMPostgreSQLService(cfg)
        rm_postgresql_service.process(run_dates)

    # -------------------------------------------------
    # DPR POSTGRESQL
    # -------------------------------------------------
    if "dpr_postgresql" in modes:
        from domains.postgresql.dpr.service import DPRPostgreSQLService
        dpr_postgresql_service = DPRPostgreSQLService(cfg)
        dpr_postgresql_service.process(run_dates)

    # -------------------------------------------------
    # HOT METAL POSTGRESQL
    # -------------------------------------------------
    if "hot_metal_postgresql" in modes:
        from domains.postgresql.hot_metal.service import HotMetalPostgreSQLService
        hot_metal_postgresql_service = HotMetalPostgreSQLService(cfg)
        hot_metal_postgresql_service.process(run_dates)

    # -------------------------------------------------
    # RM & HM POSTGRESQL
    # -------------------------------------------------
    if "rm_hm_postgresql" in modes:
        from domains.postgresql.rm_hm.service import RMHMPostgreSQLService
        rm_hm_postgresql_service = RMHMPostgreSQLService(cfg)
        rm_hm_postgresql_service.process(run_dates)

    # -------------------------------------------------
    # RM STOCK POSTGRESQL
    # -------------------------------------------------
    if "rm_stock_postgresql" in modes:
        from domains.postgresql.rm_stock.service import RMStockPostgreSQLService
        rm_stock_postgresql_service = RMStockPostgreSQLService(cfg)
        rm_stock_postgresql_service.process(run_dates)

    # -------------------------------------------------
    # CHARGE POSTGRESQL
    # -------------------------------------------------
    if "charge_postgresql" in modes:
        from domains.postgresql.charge.service import ChargePostgreSQLService, ChargePostgreSQLServiceConfig

        charge_postgresql_service = ChargePostgreSQLService(
            ChargePostgreSQLServiceConfig(
                output_dir="outputs",
                postgresql_cfg=cfg["postgresql"],
                charge_cfg=cfg["charge"],
                write_to_postgresql=True
            )
        )

        for run_date in run_dates:
            dt = datetime.strptime(run_date, "%d-%b-%Y")

            charge_files = sorted(
                download_dir.glob(f"CHARGE_AND_DUMP_REPORT_*.xlsx"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )

            matching = [
                p for p in charge_files
                if f"CHARGE_AND_DUMP_REPORT_{dt.day}_{dt.month}_{dt.year}" in p.name
            ]

            if not matching:
                logger.error(LogTemplates.failed(f"charge_file_not_found={run_date}"))
                continue

            charge_postgresql_service.run(
                charge_file=str(matching[0]),
                run_date_str=run_date,
            )

    duration = time.time() - start_time
    logger.info(LogTemplates.success(duration))


if __name__ == "__main__":
    main()
