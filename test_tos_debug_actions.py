# test_tos_debug_actions.py

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from pynput import keyboard

from config import WindowConfig
from tos_debug_actions import ToSDebugController, install_debug_hotkeys


def setup_logger(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"tos-debug-actions-{datetime.now():%Y-%m-%d}.log"

    logger = logging.getLogger("tos_debug_actions")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if logger.handlers:
        logger.handlers.clear()

    file_fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(threadName)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(file_fmt)

    ch = logging.StreamHandler()
    ch.setFormatter(console_fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


def make_filename() -> str:
    return f"scan-{datetime.now():%Y-%m-%d-%H-%M-%S}-ToS.csv"


def main() -> int:
    cfg = WindowConfig()
    log_dir = Path("logs")
    logger = setup_logger(log_dir)

    controller = ToSDebugController(
        layout_path=cfg.pwidget_yaml_path,
        cfg=cfg,
        logger=logger,
    )

    target_dir = cfg.scans_path

    logger.info("Debug hotkeys active:")
    logger.info("  Ctrl+Shift+1   open_scan_tab")
    logger.info("  Ctrl+Shift+2   load_scan50_query")
    logger.info("  Ctrl+Shift+3   load_pct_gainers_query")
    logger.info("  Ctrl+Shift+5   trigger_scan")
    logger.info("  Ctrl+Shift+6   export_csv_file")
    logger.info("  Ctrl+Shift+7   enter_filename")
    logger.info("  Ctrl+Shift+8   confirm_save")
    logger.info("  Ctrl+Shift+9   cancel_export")
    logger.info("  Ctrl+Shift+10   verify_save")
    logger.info("  Ctrl+Shift+11  nop")
    logger.info("Press Esc to quit.")

    hotkeys = install_debug_hotkeys(controller, make_filename, target_dir)
    hotkeys.start()

    try:
        with keyboard.Listener(
            on_press=lambda key: False if key == keyboard.Key.esc else None
        ) as listener:
            listener.join()
    finally:
        hotkeys.stop()
        logger.info("Hotkey listener stopped.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

