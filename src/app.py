# src/app.py
import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path
import re

import yaml

from core.config_loader import load_config
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
    args = parse_args()
    cfg = load_config()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    logger = logging.getLogger("offline")

    modes = [m.strip().lower() for m in args.mode.split(",") if m.strip()]
    valid_modes = {"rm", "dpr", "hot_metal", "rm_hm", "charge", "rm_stock"}

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

    # -------------------------------------------------
    # DOWNLOAD STEP (ONCE)
    # -------------------------------------------------
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
        # skipped = downloader.download(modes=modes, run_date_str=run_dates[0], is_today_mode=bool(args.today))


        skipped = downloader.download(
            modes=modes,
            run_dates=run_dates,
            is_today_mode=bool(args.today),
        )


        logger.info(f"Download completed. Skipped files: {sorted(skipped)}")

    finally:
        selenium.stop()

    download_dir = Path(cfg["download"]["download_dir"]).expanduser()

    # -------------------------------------------------
    # RM
    # -------------------------------------------------
    if "rm" in modes:
        rm_files = sorted(
            download_dir.glob("11A BF-02 BUNKER*.xlsx"),
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
    # if "charge" in modes:
    #     charge_files = sorted(
    #         download_dir.glob("CHARGE_AND_DUMP_REPORT_*.xlsx"),
    #         key=lambda p: p.stat().st_mtime,
    #         reverse=True,
    #     )

    #     if not charge_files:
    #         logger.warning("No CHARGE files found after download.")
    #     else:
    #         charge_yaml_path = Path("src/config/charge.yaml")

    #         # Load rename_dict + aggregates from charge.yaml
    #         charge_user_cfg = _load_charge_user_cfg(charge_yaml_path)

    #         charge_service = ChargeService(
    #             ChargeServiceConfig(
    #                 charge_yaml_path=str(charge_yaml_path),
    #                 aggregates=charge_user_cfg.get("aggregates", {}),
    #                 rename_dict=charge_user_cfg.get("rename_dict", {}),
    #                 influx_cfg=cfg.get("influxdb", {}),
    #             )
    #         )

    #         def find_file_for_date(dt: datetime):
    #             pattern = f"CHARGE_AND_DUMP_REPORT_{dt.day}_{dt.month}_{dt.year}"
    #             for f in charge_files:
    #                 if pattern in f.name:
    #                     return f
    #             return None

    #         out_dir = Path("output")
    #         out_dir.mkdir(parents=True, exist_ok=True)

    #         for run_date in run_dates:
    #             run_dt = datetime.strptime(run_date, "%d-%b-%Y")
    #             prev_dt = run_dt - timedelta(days=1)

    #             file_today = find_file_for_date(run_dt)
    #             file_yesterday = find_file_for_date(prev_dt)

    #             if not file_today:
    #                 logger.error("Charge file not found for %s", run_date)
    #                 continue

    #             logger.info(
    #                 "Processing CHARGE for %s | today=%s | yesterday=%s",
    #                 run_date,
    #                 file_today.name,
    #                 file_yesterday.name if file_yesterday else "None",
    #             )

    #             df = charge_service.run(
    #                 file_today=str(file_today),
    #                 file_yesterday=str(file_yesterday) if file_yesterday else None,
    #                 run_date_str=run_date,
    #             )

    #             if df is None or df.empty:
    #                 logger.warning("No charge data produced for %s", run_date)
    #                 continue

    #             # Excel output (Influx write already done inside ChargeService)
    #             out_file = out_dir / f"charge_data_{run_date}.xlsx"
    #             df.rename(columns={"DATETIME": "date"}).to_excel(out_file, index=False)

    #             logger.info("Charge Excel written → %s", out_file)



    if "charge" in modes:
        charge_files = list(download_dir.glob("CHARGE_AND_DUMP_REPORT_*.xls*"))

        if not charge_files:
            logger.warning("No CHARGE files found after download.")
            return

        # ---------- build date latest-file map ----------
        charge_map: dict[datetime.date, Path] = {}

        for f in charge_files:
            m = re.search(r"CHARGE_AND_DUMP_REPORT_(\d{1,2})_(\d{1,2})_(\d{4})", f.name)
            if not m:
                continue

            d = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))).date()

            prev = charge_map.get(d)
            if not prev or f.stat().st_mtime > prev.stat().st_mtime:
                charge_map[d] = f   # keep latest only

        charge_yaml_path = Path("src/config/charge.yaml")
        charge_user_cfg = _load_charge_user_cfg(charge_yaml_path)

        charge_service = ChargeService(
            ChargeServiceConfig(
                charge_yaml_path=str(charge_yaml_path),
                aggregates=charge_user_cfg.get("aggregates", {}),
                rename_dict=charge_user_cfg.get("rename_dict", {}),
                influx_cfg=cfg.get("influxdb", {}),
            )
        )

        out_dir = Path("output")
        out_dir.mkdir(parents=True, exist_ok=True)

        # ---------- process per run date ----------
        for run_date in run_dates:
            run_dt = datetime.strptime(run_date, "%d-%b-%Y").date()
            prev_dt = run_dt - timedelta(days=1)

            file_today = charge_map.get(run_dt)
            file_yesterday = charge_map.get(prev_dt)

            if not file_today:
                logger.error("Charge file not found for %s", run_date)
                continue

            logger.info(
                "Processing CHARGE for %s | today=%s | yesterday=%s",
                run_date,
                file_today.name,
                file_yesterday.name if file_yesterday else "None",
            )

            df = charge_service.run(
                file_today=str(file_today),
                file_yesterday=str(file_yesterday) if file_yesterday else None,
                run_date_str=run_date,
            )

            if df is None or df.empty:
                logger.warning("No charge data produced for %s", run_date)
                continue

            out_file = out_dir / f"charge_data_{run_date}.xlsx"
            df.rename(columns={"DATETIME": "date"}).to_excel(out_file, index=False)
            logger.info("Charge Excel written → %s", out_file)

    logger.info("Offline data automation completed successfully.")


if __name__ == "__main__":
    main()
