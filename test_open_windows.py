from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pygetwindow as gw


@dataclass
class WindowInfo:
    title: str
    width: int
    height: int


def setup_logger(logger_name: str, log_stem: str, log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{log_stem}-{datetime.now():%Y-%m-%d}.log"

    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if logger.handlers:
        logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


def collect_windows(only_titled: bool) -> list[WindowInfo]:
    items: list[WindowInfo] = []

    for win in gw.getAllWindows():
        try:
            raw_title = win.title or ""
            title = raw_title.strip() or "<untitled>"
            width = int(win.width)
            height = int(win.height)
        except Exception:
            continue

        if only_titled and title == "<untitled>":
            continue

        items.append(WindowInfo(title=title, width=width, height=height))

    return items


def format_window_table(windows: list[WindowInfo]) -> str:
    if not windows:
        return "No windows found."

    index_width = max(3, len(str(len(windows))))
    title_width = max(20, max(len(w.title) for w in windows))

    lines = [
        f'{"#":>{index_width}}  {"Title":<{title_width}}  {"Width":>6}  {"Height":>6}',
        f'{"-" * index_width}  {"-" * title_width}  {"-" * 6}  {"-" * 6}',
    ]

    for idx, win in enumerate(windows, start=1):
        lines.append(
            f"{idx:>{index_width}}  {win.title:<{title_width}}  {win.width:>6}  {win.height:>6}"
        )

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="List open windows and log each window title with width and height."
    )
    parser.add_argument(
        "--only-titled",
        action="store_true",
        help="Skip windows with blank titles",
    )
    parser.add_argument(
        "--log-dir",
        default="logs",
        help="Directory for the log file (default: ./logs)",
    )
    args = parser.parse_args()

    log_dir = Path(args.log_dir).expanduser().resolve()
    logger = setup_logger("open_windows_test", "open-windows-test", log_dir)

    try:
        windows = collect_windows(only_titled=args.only_titled)
        table = format_window_table(windows)

        logger.info("Window count: %d", len(windows))
        logger.info("Open windows:\n%s", table)

    except Exception:
        logger.exception("Failed while collecting open-window information.")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

