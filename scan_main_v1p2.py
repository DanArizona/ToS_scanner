# scan_main.py

from __future__ import annotations

import argparse
import logging
import os
import threading

from pathlib import Path

from PySide6.QtWidgets import QApplication

from config import ScannerConfig, load_scanner_config
from control_manager import ScanControlManager
from control_panel import ScanControlPanel
from scanner_logging import build_logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_STATE_FILE = Path("./runtime/scan_runner_state.json")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run timed ToS scan exports with a small control panel."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for CSV files; defaults to MB_SCANS",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path("./logs"),
        help="Directory for daily log files",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_FILE,
        help=f"Path to persistent runtime state json (default: {DEFAULT_STATE_FILE})",
    )
    parser.add_argument(
        "--layout-path",
        type=Path,
        default=None,
        help="Optional layout file for pseudo-widget definitions; defaults to MB_PWIDGET_YAML",
    )
    parser.add_argument(
        "--verify-timeout",
        type=float,
        default=10.0,
        help="Seconds to wait for CSV file to be verified after GUI export",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Create stub CSVs instead of interacting with ToS",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()
    cfg = load_scanner_config()

    logger, listener = build_logger(args.log_dir)
    listener.start()

    app = QApplication([])

    try:
        logger.info("========================================================")
        logger.info("scan_main.py startup")
        logger.info("output_dir=%s", args.output_dir or cfg.scans_path)
        logger.info("log_dir=%s", args.log_dir)
        logger.info("state_file=%s", args.state_file)
        # logger.info("layout_path=%s", args.layout_path or Path(cfg.pwidget_yaml_path))
        logger.info("layout_path=%s", args.layout_path or cfg.pwidget_yaml_path)
        logger.info("dry_run=%s", args.dry_run)
        logger.info("window_tos_main prefix=%s", cfg.window_tos_main)

        manager = ScanControlManager(
            args=args,
            cfg=cfg,
            logger=logger,
        )

        panel = ScanControlPanel(
            manager=manager,
            logger=logger,
        )
        panel.show()

        rc = app.exec()
        manager.stop()
        return rc

    finally:
        listener.stop()


if __name__ == "__main__":
    raise SystemExit(main())

    