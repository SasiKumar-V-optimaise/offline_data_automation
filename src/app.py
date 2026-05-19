# src/app.py
import argparse
from datetime import datetime, timedelta
from pathlib import Path
import re
from zoneinfo import ZoneInfo

import yaml

from core.config_loader import load_config
from core.logging import configure_logging
from infrastructure.selenium_client import SeleniumClient, SeleniumConfig
from domains.download.service import PortalDownloader, DownloadConfig

from domains.rm.service import RMService
from domains.fines_analysis.service import FinesAnalysisService
from domains.dpr.service import DPRService
from domains.hot_metal.service import HotMetalService
from domains.rm_hm.service import RMHMService
from domains.charge.service import ChargeService, ChargeServiceConfig

BUSINESS_TZ = ZoneInfo("Asia/Kolkata")


# -------------------------------------------------
# ARGUMENT PARSING
# -------------------------------------------------
def parse_args():
    parser = argparse.ArgumentParser("Offline Data Automation")

    parser.add_argument(
        "--mode",
        required=True,
        help="Comma separated modes. Supported: rm, fines_analysis, dpr, hot_metal, rm_hm, charge",
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
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip Selenium download step (use existing files)",
    )

    return parser.parse_args()


# -------------------------------------------------
# DATE PARSING (SINGLE + RANGE)
# -------------------------------------------------
def parse_run_dates(raw: str | None, today: bool) -> list[str]:
    if today:
        return [datetime.now(BUSINESS_TZ).strftime("%d-%b-%Y")]

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



# -------------------------------------------------
# MAIN
# -------------------------------------------------
def main():
    args = parse_args()
    cfg = load_config()

    logging_cfg = cfg.get("logging") or {}
    logger = configure_logging(
        level=logging_cfg.get("level", "INFO"),
        log_dir=logging_cfg.get("dir", "output/logs"),
    )

    mode_aliases = {"fines": "fines_analysis"}
    modes = [
        mode_aliases.get(m.strip().lower(), m.strip().lower())
        for m in args.mode.split(",")
        if m.strip()
    ]
    valid_modes = {"rm", "fines_analysis", "dpr", "hot_metal", "rm_hm", "charge", "rm_stock"}

    invalid = set(modes) - valid_modes
    if invalid:
        raise SystemExit(f"Unsupported modes: {sorted(invalid)}")

    # -------------------------------------------------
    # RESOLVE RUN DATES
    # -------------------------------------------------
    run_dates = parse_run_dates(args.rundate, args.today)
    logger.info(
        f"Run dates resolved: {run_dates[0]} → {run_dates[-1]} "
        f"({len(run_dates)} day(s))"
    )
    download_dir = Path(cfg["download"]["download_dir"]).expanduser()

    # -------------------------------------------------
    # DOWNLOAD STEP (OPTIONAL)
    # -------------------------------------------------
    if args.skip_download:
        logger.info(" Skipping download step (using existing files)")
    else:
        logger.info("Starting download step")

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

            skipped = downloader.download(
                modes=modes,
                run_dates=run_dates,
                is_today_mode=bool(args.today),
            )

            logger.info(f"Download completed. Skipped files: {sorted(skipped)}")

        finally:
            selenium.stop()


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
    # FINES ANALYSIS
    # -------------------------------------------------
    if "fines_analysis" in modes:
        fines_files = sorted(
            download_dir.glob("*BUNKER*.xlsx"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if fines_files:
            FinesAnalysisService(logger).process(str(fines_files[0]), cfg, run_dates)
        else:
            logger.warning("No BUNKER file found for fines analysis.")

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
            RMHMService(
            logger,
            neon_cfg=cfg["neon_developer"],
            write_to_neon=True
        ).process(str(rm_hm_files[0]), cfg, run_dates)
        else:
            logger.warning("No RM & HM file found after download.")

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
            logger.warning("No RM STOCK file found after download.")
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

        charge_service = ChargeService(
            ChargeServiceConfig(
                output_dir="outputs",
                neon_cfg=cfg["neon_developer"],
                charge_yaml_path="src/config/charge.yaml",
                write_to_neon=True,
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
                logger.error(f"Charge file not found for {run_date}")
                continue

            charge_service.run(
                charge_file=str(matching[0]),
                run_date_str=run_date,
            )




    logger.info("Offline data automation completed successfully.")


if __name__ == "__main__":
    main()
