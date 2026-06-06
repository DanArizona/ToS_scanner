# startup_checks.py

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

import pygetwindow as gw

from config import ScannerConfig
from layout import load_widget_layout


REQUIRED_ENV_VARS: list[str] = []


class StartupValidationError(RuntimeError):
    pass


def fatal_startup(logger: logging.Logger, message: str, exit_code: int = 2) -> int:
    logger.error(message)
    print(f"ERROR: {message}", file=sys.stderr, flush=True)
    return exit_code


def validate_required_env_vars(logger: logging.Logger) -> None:
    # Earlier versions checked plain environment secrets here.
    # Pushover credentials are now optional and loaded from encrypted .ecfg.
    logger.info("Startup check passed: no required plain environment secrets.")


def get_matching_window(title_prefix: str):
    normalized_prefix = title_prefix.strip()

    for win in gw.getAllWindows():
        title = (win.title or "").strip()
        if title.startswith(normalized_prefix):
            return win

    return None


def validate_win_main_open(logger: logging.Logger, cfg: ScannerConfig):
    title_prefix = cfg.window_tos_main
    win = get_matching_window(title_prefix)

    if win is None:
        raise StartupValidationError(
            f"Required ToS window is not open/visible: "
            f"win_main startswith {title_prefix!r}"
        )

    if int(win.width) <= 0 or int(win.height) <= 0:
        raise StartupValidationError(
            f"Matched ToS window has invalid size: "
            f"title={win.title!r} size={win.width}x{win.height}"
        )

    logger.info("Startup check passed: win_main is open: actual title=%s", win.title)
    return win


def extract_expected_size_from_layout(layout, widget_name: str) -> tuple[int, int]:
    item = None

    if isinstance(layout, dict):
        item = layout.get(widget_name)
    else:
        item = getattr(layout, widget_name, None)

    if item is None:
        raise StartupValidationError(
            f"Could not find widget '{widget_name}' in loaded YAML layout."
        )

    candidates = [
        item,
        getattr(item, "root", None),
        getattr(item, "region", None),
        getattr(item, "bbox", None),
    ]

    for obj in candidates:
        if obj is None:
            continue

        w = getattr(obj, "w", None)
        h = getattr(obj, "h", None)

        if w is None:
            w = getattr(obj, "width", None)
        if h is None:
            h = getattr(obj, "height", None)

        if w is not None and h is not None:
            return int(w), int(h)

    raise StartupValidationError(
        f"Could not extract width/height for widget '{widget_name}' "
        f"from YAML layout."
    )


def validate_win_main_size(
    logger: logging.Logger,
    cfg: ScannerConfig,
    layout,
    win_main_window,
) -> None:
    expected_w, expected_h = extract_expected_size_from_layout(layout, "win_main")
    tolerance_px = int(cfg.WINDOW_ALL_MAX_DIMS_ERR)

    actual_w = int(win_main_window.width)
    actual_h = int(win_main_window.height)

    dw = abs(actual_w - expected_w)
    dh = abs(actual_h - expected_h)

    if dw > tolerance_px or dh > tolerance_px:
        raise StartupValidationError(
            "win_main size check failed. "
            f"Expected approximately {expected_w}x{expected_h} from YAML, "
            f"actual on-screen size is {actual_w}x{actual_h}, "
            f"tolerance is +/-{tolerance_px}px, "
            f"delta=({dw}, {dh})."
        )

    logger.info(
        "Startup check passed: win_main size ok. "
        "expected=%sx%s actual=%sx%s tolerance=%spx",
        expected_w,
        expected_h,
        actual_w,
        actual_h,
        tolerance_px,
    )


def validate_output_dir_access(logger: logging.Logger, output_dir: Path) -> None:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        probe = output_dir / ".__scan_write_test.tmp"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)

    except Exception as exc:
        raise StartupValidationError(
            f"Output directory is not accessible/writable: {output_dir} | {exc}"
        ) from exc

    logger.info("Startup check passed: output directory is accessible: %s", output_dir)


def run_startup_checks(
    logger: logging.Logger,
    cfg: ScannerConfig,
    layout_path: Optional[Path],
    output_dir: Path,
) -> None:
    validate_required_env_vars(logger)

    win_main_window = validate_win_main_open(logger, cfg)

    if layout_path is None:
        raise StartupValidationError(
            "Layout path is required for win_main dimension validation."
        )

    try:
        layout = load_widget_layout(layout_path, cfg.title_map)
    except Exception as exc:
        raise StartupValidationError(
            f"Could not load layout YAML '{layout_path}': {exc}"
        ) from exc

    validate_win_main_size(logger, cfg, layout, win_main_window)
    validate_output_dir_access(logger, output_dir)
