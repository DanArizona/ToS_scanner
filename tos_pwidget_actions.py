# tos_pwidget_actions.py

from __future__ import annotations

import random
import threading
import time
import numpy as np
import subprocess
import ctypes
import sys
from ctypes import wintypes

from pathlib import Path
from typing import Optional
from collections.abc import Mapping

import pyautogui
import pygetwindow as gw

from config import ScannerConfig, load_scanner_config
from layout import load_widget_layout
from models import WidgetStack


pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.0


def _get_native_hwnd(win: object) -> int | None:
    """
    Return native Windows HWND from a pygetwindow window object, if available.
    """
    hwnd = getattr(win, "_hWnd", None)

    if isinstance(hwnd, int) and hwnd:
        return hwnd

    return None


def _set_hwnd_to_top(hwnd: int) -> bool:
    """
    Ask Windows to move this window to the top of the normal Z-order.

    This does not move or resize the window.
    It also does not make the window permanently topmost.
    """
    if sys.platform != "win32":
        return False

    user32 = ctypes.windll.user32

    SetWindowPos = user32.SetWindowPos
    SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    SetWindowPos.restype = wintypes.BOOL

    HWND_TOP = 0

    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_SHOWWINDOW = 0x0040

    flags = SWP_NOSIZE | SWP_NOMOVE | SWP_SHOWWINDOW

    result = SetWindowPos(hwnd, HWND_TOP, 0, 0, 0, 0, flags)
    return bool(result)


def _pulse_hwnd_topmost(hwnd: int) -> bool:
    """
    Temporarily make a window topmost, then immediately make it not-topmost.

    This is more forceful than HWND_TOP, but should not leave the window
    permanently always-on-top.
    """
    if sys.platform != "win32":
        return False

    user32 = ctypes.windll.user32

    SetWindowPos = user32.SetWindowPos
    SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    SetWindowPos.restype = wintypes.BOOL

    HWND_TOPMOST = -1
    HWND_NOTOPMOST = -2

    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_SHOWWINDOW = 0x0040

    flags = SWP_NOSIZE | SWP_NOMOVE | SWP_SHOWWINDOW

    ok_topmost = bool(
        SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags)
    )
    ok_notopmost = bool(
        SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, flags)
    )

    return ok_topmost and ok_notopmost


class ToSActionsController:
    """
    Controller for scripted ToS pseudo-widget interaction.

    Notes
    -----
    - Uses the layout registry returned by load_widget_layout(...)
    - Uses ScannerConfig.title_map for root-window resolution
    - Adds small randomized delay and cursor jitter
    - Serializes actions with a lock so actions do not overlap
    """

    # Slow and deterministic for diagnostics/observation
    ENABLE_RANDOM_TIMING = True
    ENABLE_RANDOM_POSITION = True

    EXTRA_DELAY_MAX_S = 0.010
    JITTER_X_PX = 5
    JITTER_Y_PX = 3

    # MOVE_DURATION_S = 0.35
    # SEGMENT_DURATION_S = 0.28
    # CLICK_PAUSE_S = 0.25
    # STEP_PAUSE_S = 0.40

    MOVE_DURATION_S = 0.25
    SEGMENT_DURATION_S = 0.20
    CLICK_PAUSE_S = 0.20
    STEP_PAUSE_S = 0.30
    WINDOW_SIZE_TOLERANCE_PX = 4


    def __init__(
        self,
        *,
        layout_path: str | Path,
        cfg: Optional[ScannerConfig] = None,        
        logger=None,
    ) -> None:
        self.cfg = cfg or load_scanner_config()        
        self.logger = logger
        self.layout = load_widget_layout(layout_path, self.cfg.title_map)        
        self.action_lock = threading.RLock()

    # ------------------------------------------------------------------
    # Logging / timing helpers
    # ------------------------------------------------------------------

    def _log(self, msg: str, *args, level: str = "info") -> None:
        if args:
            msg = msg % args

        if self.logger:
            log_method = getattr(self.logger, level, self.logger.info)
            log_method(msg)
        else:
            now_str = time.strftime("%H:%M:%S")
            print(f"{now_str} | {level.upper()} | {msg}")

    def _sleep(self, base_s: float) -> None:
        extra = random.uniform(0.0, self.EXTRA_DELAY_MAX_S) if self.ENABLE_RANDOM_TIMING else 0.0
        time.sleep(base_s + extra)

    def _jitter(self, x: int, y: int) -> tuple[int, int]:
        if not self.ENABLE_RANDOM_POSITION:
            return x, y

        jx = random.randint(-self.JITTER_X_PX, self.JITTER_X_PX)
        jy = random.randint(-self.JITTER_Y_PX, self.JITTER_Y_PX)
        return x + jx, y + jy

    # ------------------------------------------------------------------
    # Widget / window helpers
    # ------------------------------------------------------------------

    def _widget(self, name: str) -> WidgetStack:
        try:
            return self.layout[name]
        except KeyError as exc:
            raise KeyError(f"Pseudo-widget not found in layout: {name}") from exc

    def _widget_center(self, name: str) -> tuple[int, int]:
        x, y = self._widget(name).get_absolute_center()
        self._log("GUI | widget center resolved -> %s @ (%d, %d)", name, x, y)
        return x, y

    def _get_named_window(self, widget_name: str):
        title_prefix = self.cfg.title_map[widget_name]
        return self._get_matching_window(title_prefix)

    def _get_matching_window(self, title_prefix: str):
        normalized_prefix = title_prefix.strip()

        for win in gw.getAllWindows():
            title = (win.title or "").strip()
            if title.startswith(normalized_prefix):
                return win

        return None


    def _bring_named_window_to_front(self, widget_name: str) -> Any:
        title_prefix = self.cfg.title_map[widget_name]
        win = self._get_named_window(widget_name)

        if win is None:
            raise RuntimeError(
                f"Could not find window for {widget_name!r} with prefix {title_prefix!r}"
            )

        try:
            if hasattr(win, "isMinimized") and win.isMinimized:
                win.restore()
        except Exception:
            pass

        try:
            win.activate()
        except Exception:
            pass

        hwnd = _get_native_hwnd(win)

        if hwnd is not None:
            try:
                ok = _set_hwnd_to_top(hwnd)
                self._log(
                    "GUI | SetWindowPos HWND_TOP: %s -> %s",
                    widget_name,
                    "OK" if ok else "FAILED",
                )

                pulse_ok = _pulse_hwnd_topmost(hwnd)
                self._log(
                    "GUI | SetWindowPos TOPMOST pulse: %s -> %s",
                    widget_name,
                    "OK" if pulse_ok else "FAILED",
                )

            except Exception as exc:
                self._log(
                    "GUI | SetWindowPos raise failed: %s -> %s",
                    widget_name,
                    exc,
                )

        self._sleep(self.STEP_PAUSE_S)

        self._log("GUI | bring window to front: %s -> %s", widget_name, win.title)
        self._log_front_status_for_title(
            label=widget_name,
            title=win.title,
        )

        return win


    def _wait_for_window(self, widget_name: str, timeout_s: float = 2.0) -> bool:
        title_prefix = self.cfg.title_map[widget_name]
        deadline = time.monotonic() + timeout_s

        while time.monotonic() < deadline:
            if self._get_matching_window(title_prefix) is not None:
                return True
            time.sleep(0.05)
        return False
    
    def _diag_field_as_str(
        self,
        record: Mapping[str, object],
        key: str,
        default: str = "",
    ) -> str:
        value = record.get(key)

        if value is None:
            return default

        return str(value)


    def _diag_field_as_int_or_none(
        self,
        record: Mapping[str, object],
        key: str,
    ) -> int | None:
        value = record.get(key)

        if value is None:
            return None

        if isinstance(value, int):
            return value

        if isinstance(value, str):
            if not value:
                return None

            return int(value)

        return None


    def _log_front_status_for_title(
        self,
        *,
        label: str,
        title: str,
    ) -> None:
        """
        Diagnostic only.

        After requesting that a window be brought to the front, log whether it
        appears foreground/frontmost according to mb_tools.window_survey.

        This does not change window state.
        """
        try:
            from mb_tools.window_survey import survey_windows
        except Exception as exc:
            self._log(
                f"GUI | front-status diagnostic unavailable for {label}: {exc}"
            )
            return

        try:
            records = survey_windows(
                visible_only=True,
                sort_by_z_order=True,
            )
        except Exception as exc:
            self._log(
                f"GUI | front-status diagnostic failed for {label}: {exc}"
            )
            return

        target: Mapping[str, object] | None = None

        for record in records:
            record_title = self._diag_field_as_str(record, "title")

            if record_title == title:
                target = record
                break

        if target is None:
            for record in records:
                record_title = self._diag_field_as_str(record, "title")

                if title and title in record_title:
                    target = record
                    break

        foreground: Mapping[str, object] | None = None

        for record in records:
            if bool(record.get("is_foreground")):
                foreground = record
                break

        if target is None:
            fg_title = (
                self._diag_field_as_str(foreground, "title")
                if foreground is not None
                else "(none)"
            )
            self._log(
                f"GUI | front-status after request: {label} target not found; "
                f"foreground={fg_title}"
            )
            return

        z_order = self._diag_field_as_int_or_none(target, "z_order")
        cov_count = self._diag_field_as_int_or_none(target, "covered_by_count")
        is_foreground = bool(target.get("is_foreground"))
        is_topmost = bool(target.get("is_topmost"))
        covered_by = self._diag_field_as_str(target, "covered_by")

        fg_title = (
            self._diag_field_as_str(foreground, "title")
            if foreground is not None
            else "(none)"
        )
        fg_z = (
            self._diag_field_as_int_or_none(foreground, "z_order")
            if foreground is not None
            else None
        )

        self._log(
            "GUI | front-status after request: "
            f"{label} z={z_order} "
            f"foreground={'YES' if is_foreground else 'NO'} "
            f"topmost={'YES' if is_topmost else 'NO'} "
            f"covered_by_count={cov_count}"
        )

        self._log(
            f"GUI | foreground after request: z={fg_z} title={fg_title}"
        )

        if covered_by:
            self._log(
                f"GUI | {label} still covered by: {covered_by}"
            )

    def _enter_filename_in_export_dialog(
        self,
        *,
        window_name: str,
        filename_widget: str,
        filename: str,
        target_dir: str | Path,
        log_label: str,
    ) -> None:
        """
        Enter only the filename into an export/save dialog.

        The export directory is expected to already be selected/remembered by
        the dialog. The expected full path is logged for verification/diagnostics.
        """
        target_dir = Path(target_dir).expanduser().resolve()
        expected_path = target_dir / filename

        self._log("ACTION | enter_%s_filename -> %s", log_label, filename)
        self._log("ACTION | expected %s save path -> %s", log_label, expected_path)

        # self._bring_named_window_to_front(window_name)
        # self._assert_window_size(window_name)
        win = self._bring_named_window_to_front(window_name)
        self._assert_window_size(window_name, win=win)

        self._move_center(filename_widget)
        self._click()
        self._sleep(0.5)
        self._select_all()
        self._delete_selection()
        self._type_text(filename)


    def _enter_filename_then_export_directory(
        self,
        *,
        window_name: str,
        filename_widget: str,
        directory_widget: str,
        filename: str,
        target_dir: str | Path,
        log_label: str,
    ) -> None:
        """
        First-time setup workflow for an export/save dialog.

        Enter the filename while the dialog is still in its usual geometry,
        then enter the target directory. Changing the directory may shift the
        filename field, but the filename has already been entered.
        """
        target_dir = Path(target_dir).expanduser().resolve()
        expected_path = target_dir / filename

        self._log("ACTION | enter_%s_filename_then_export_directory", log_label)
        self._log("ACTION | filename -> %s", filename)
        self._log("ACTION | target directory -> %s", target_dir)
        self._log("ACTION | expected %s save path -> %s", log_label, expected_path)

        # self._bring_named_window_to_front(window_name)
        # self._assert_window_size(window_name)
        win = self._bring_named_window_to_front(window_name)
        self._assert_window_size(window_name, win=win)

        self._move_center(filename_widget)
        self._click()
        self._select_all()
        self._delete_selection()
        self._type_text(filename)

        self._move_center(directory_widget)
        self._click()
        self._select_all()
        self._delete_selection()
        self._type_text(str(target_dir))
        pyautogui.press("enter")
        self._sleep(1.0)

    def _confirm_export_save(
        self,
        *,
        window_name: str,
        save_button_widget: str,
        log_label: str,
    ) -> None:
        self._log("ACTION | confirm_%s_save", log_label)

        # self._bring_named_window_to_front(window_name)
        # self._assert_window_size(window_name)
        win = self._bring_named_window_to_front(window_name)
        self._assert_window_size(window_name, win=win)

        self._move_vh(save_button_widget)
        self._click()

    def _cancel_export_dialog(
        self,
        *,
        window_name: str,
        cancel_button_widget: str,
        log_label: str,
    ) -> None:
        self._log("ACTION | cancel_%s_export", log_label)

        # self._bring_named_window_to_front(window_name)
        # self._assert_window_size(window_name)
        win = self._bring_named_window_to_front(window_name)
        self._assert_window_size(window_name, win=win)

        self._move_vh(cancel_button_widget)
        self._click()


    def _widget_size(self, widget_name: str) -> tuple[int, int]:
        """
        Return expected widget/window size from the loaded YAML layout.
        """
        widget = self._widget(widget_name)

        width = getattr(widget, "width", None)
        height = getattr(widget, "height", None)

        if width is not None and height is not None:
            return int(width), int(height)

        # Fallback if the dimensions live on a nested region/bbox object.
        for attr_name in ("region", "bbox"):
            region = getattr(widget, attr_name, None)
            if region is None:
                continue

            width = getattr(region, "width", None)
            height = getattr(region, "height", None)

            if width is not None and height is not None:
                return int(width), int(height)

        raise RuntimeError(f"Could not determine expected size for widget {widget_name!r}")



    def _assert_window_size(
        self,
        window_name: str,
        *,
        win=None,
        tolerance_px: int | None = None,
    ) -> None:
        """
        Verify that an open ToS window/dialog matches the YAML root size.

        This is important for save dialogs because resizing the dialog changes
        child widget locations relative to the YAML layout.
        """
        tolerance = self.WINDOW_SIZE_TOLERANCE_PX if tolerance_px is None else tolerance_px

        expected_w, expected_h = self._widget_size(window_name)

        # win = self._get_matching_window(window_name)
        # if win is None:
        #     raise RuntimeError(f"{window_name} is not open; cannot check size.")


        # if win is None:
        #     win = self._get_matching_window(window_name)
        if win is None:
            win = self._get_named_window(window_name)

        if win is None:
            raise RuntimeError(f"{window_name} is not open; cannot check size.")
    
        actual_w = int(win.width)
        actual_h = int(win.height)

        dw = abs(actual_w - expected_w)
        dh = abs(actual_h - expected_h)

        self._log(
            "GUI | window size check -> %s expected=%sx%s actual=%sx%s tolerance=%s",
            window_name,
            expected_w,
            expected_h,
            actual_w,
            actual_h,
            tolerance,
        )

        if dw > tolerance or dh > tolerance:
            raise RuntimeError(
                f"{window_name} size mismatch: "
                f"expected {expected_w}x{expected_h}, "
                f"actual {actual_w}x{actual_h}, "
                f"tolerance {tolerance}px. "
                "Resize the dialog to match the YAML layout before continuing."
            )


    def normalize_window_size(self, window_name: str) -> None:
        """
        Resize an open ToS window/dialog to the YAML root size, then verify it.

        Intended for setup/initialization workflows, not for every recurring
        export action.
        """
        with self.action_lock:
            win = self._bring_named_window_to_front(window_name)

            expected_w, expected_h = self._widget_size(window_name)

            actual_w = int(getattr(win, "width"))
            actual_h = int(getattr(win, "height"))

            self._log(
                "GUI | normalize window size -> %s expected=%sx%s actual=%sx%s",
                window_name,
                expected_w,
                expected_h,
                actual_w,
                actual_h,
            )

            resize_to = getattr(win, "resizeTo", None)
            if resize_to is None:
                raise RuntimeError(
                    f"{window_name} window object does not support resizeTo()."
                )

            resize_to(expected_w, expected_h)
            self._sleep(1.0)

            if not self._wait_for_window(window_name, timeout_s=3.0):
                raise RuntimeError(f"{window_name} is not open after resize.")

            # win = self._get_matching_window(window_name)
            # if win is None:
            #     raise RuntimeError(f"{window_name} could not be re-fetched after resize.")
            win = self._get_named_window(window_name)
            if win is None:
                raise RuntimeError(f"{window_name} could not be re-fetched after resize.")

            self._assert_window_size(window_name, win=win)


    def normalize_scan_export_dialog(self) -> None:
        self.normalize_window_size("win_export")


    def normalize_watchlist_export_dialog(self) -> None:
        self.normalize_window_size("win_wl_export")


    # ------------------------------------------------------------------
    # Mouse / keyboard primitives
    # ------------------------------------------------------------------

    def _move_center(self, widget_name: str) -> None:
        x, y = self._widget_center(widget_name)
        x, y = self._jitter(x, y)
        self._log("GUI | move center -> %s @ (%d, %d)", widget_name, x, y)
        pyautogui.moveTo(x, y, duration=self.MOVE_DURATION_S)
        self._sleep(self.STEP_PAUSE_S)

    def _move_vh(self, widget_name: str) -> None:
        """
        Move vertically first, then horizontally.
        Useful for navigating cascading menus without collapsing them.
        """
        tx, ty = self._widget_center(widget_name)
        tx, ty = self._jitter(tx, ty)

        cx, cy = pyautogui.position()
        self._log("GUI | move VH -> %s @ (%d, %d)", widget_name, tx, ty)

        pyautogui.moveTo(cx, ty, duration=self.SEGMENT_DURATION_S)
        self._sleep(0.03)
        pyautogui.moveTo(tx, ty, duration=self.SEGMENT_DURATION_S)
        self._sleep(self.STEP_PAUSE_S)

    def _move_hv(self, widget_name: str) -> None:
        """
        Move horizontally first, then vertically.
        """
        tx, ty = self._widget_center(widget_name)
        tx, ty = self._jitter(tx, ty)

        cx, cy = pyautogui.position()
        self._log("GUI | move HV -> %s @ (%d, %d)", widget_name, tx, ty)

        pyautogui.moveTo(tx, cy, duration=self.SEGMENT_DURATION_S)
        self._sleep(0.03)
        pyautogui.moveTo(tx, ty, duration=self.SEGMENT_DURATION_S)
        self._sleep(self.STEP_PAUSE_S)

    def _click(self) -> None:
        self._log("GUI | mouse click")
        pyautogui.click()
        self._sleep(self.CLICK_PAUSE_S)

    def _select_all(self) -> None:
        self._log("GUI | Ctrl+A")
        pyautogui.hotkey("ctrl", "a")
        self._sleep(self.STEP_PAUSE_S)

    def _delete_selection(self) -> None:
        self._log("GUI | Delete selection")
        pyautogui.press("backspace")
        self._sleep(self.STEP_PAUSE_S)

    def _type_text(self, text: str, interval_s: float = 0.03) -> None:
        self._log("GUI | type text: %s", text)
        pyautogui.write(text, interval=interval_s)
        self._sleep(self.STEP_PAUSE_S)


    # ------------------------------------------------------------------
    # Future OCR hook
    # ------------------------------------------------------------------

    def _future_check_ptxt(self, widget_name: str) -> None:
        """
        Placeholder for future OCR validation against widget.ptxt.
        Not active yet.
        """
        widget = self._widget(widget_name)
        _ = widget.ptxt

    # ------------------------------------------------------------------
    # High-level actions
    # ------------------------------------------------------------------

    def open_scan_tab(self) -> None:
        with self.action_lock:
            self._log("ACTION | open_scan_tab")
            self._bring_named_window_to_front("win_main")
            self._move_center("tab_main_scan")
            self._click()

    def load_scan50_query(self) -> None:
        with self.action_lock:
            self._log("ACTION | load_scan50_query")
            self._bring_named_window_to_front("win_main")
            self._move_center("tab_main_scan")
            self._click()

            self._move_center("btn_query_menu")
            self._click()
            self._move_vh("pick_query_load")
            self._click()
            self._move_hv("pick_query_personal")
            self._click()
            self._move_hv("pick_query_scan50")
            self._click()

    def load_pct_gainers_query(self) -> None:
        with self.action_lock:
            self._log("ACTION | load_pct_gainers_query")
            self._bring_named_window_to_front("win_main")
            self._move_center("tab_main_scan")
            self._click()

            self._move_center("btn_query_menu")
            self._click()
            self._move_vh("pick_query_load")
            self._click()
            self._move_hv("pick_query_public")
            self._click()
            self._move_hv("pick_query_pct_gainers")
            self._click()

    def trigger_scan(self) -> None:
        with self.action_lock:
            self._log("ACTION | trigger_scan")
            self._bring_named_window_to_front("win_main")
            self._move_center("tab_main_scan")
            self._click()
            self._move_center("btn_hacker_scan")
            self._click()

    def export_csv_file(self) -> None:
        with self.action_lock:
            self._log("ACTION | export_csv_file")
            self._bring_named_window_to_front("win_main")
            self._move_center("tab_main_scan")
            self._click()

            self._move_center("btn_scan_actions")
            self._click()
            self._move_vh("pick_scan_export")
            self._click()
            self._move_hv("pick_scan_to_file")
            self._click()

            if not self._wait_for_window("win_export", timeout_s=2.0):
                raise RuntimeError("win_export did not appear after export path.")

            self._log("GUI | win_export detected")

    def enter_filename_then_export_directory(
        self,
        filename: str,
        target_dir: str | Path,
    ) -> None:
        with self.action_lock:
            self._enter_filename_then_export_directory(
                window_name="win_export",
                filename_widget="ledit_exp_fname",
                directory_widget="ledit_exp_dir",
                filename=filename,
                target_dir=target_dir,
                log_label="scan",
            )

    def enter_export_directory(self, target_dir: str | Path) -> None:
        """
        Enter the target directory into the scan export dialog.

        This sets the save dialog's current directory. The filename should still
        be entered separately into ledit_exp_fname.
        """
        with self.action_lock:
            target_dir = Path(target_dir).expanduser().resolve()

            self._log("ACTION | enter_export_directory -> %s", target_dir)

            self._bring_named_window_to_front("win_export")
            self._move_center("ledit_exp_dir")
            self._click()
            self._select_all()
            self._delete_selection()
            self._type_text(str(target_dir))
            pyautogui.press("enter")
            self._sleep(1.0)

    def enter_filename(self, filename: str, target_dir: str | Path) -> None:
        with self.action_lock:
            self._enter_filename_in_export_dialog(
                window_name="win_export",
                filename_widget="ledit_exp_fname",
                filename=filename,
                target_dir=target_dir,
                log_label="scan",
            )

    def confirm_save(self) -> None:
        with self.action_lock:
            self._confirm_export_save(
                window_name="win_export",
                save_button_widget="btn_exp_save",
                log_label="scan",
            )

    def cancel_export(self) -> None:
        with self.action_lock:
            self._cancel_export_dialog(
                window_name="win_export",
                cancel_button_widget="btn_exp_cancel",
                log_label="scan",
            )


    def verify_save(
        self,
        target_dir: str | Path,
        filename: str,
        timeout_s: float = 5.0,
        stable_window_s: float = 0.4,
        poll_s: float = 0.1,
        stop_event=None,
    ) -> bool:
        """
        Verify that the file exists and its size has stabilized.
        """
        with self.action_lock:
            path = Path(target_dir) / filename
            self._log("ACTION | verify_save -> %s", path)

            deadline = time.monotonic() + timeout_s
            last_size: Optional[int] = None
            stable_since: Optional[float] = None

            while time.monotonic() < deadline:
                if stop_event is not None and stop_event.is_set():
                    raise InterruptedError(
                        "Stop requested while waiting for CSV save verification."
                    )

                if path.exists():
                    size = path.stat().st_size
                    if last_size == size:
                        if stable_since is None:
                            stable_since = time.monotonic()
                        elif time.monotonic() - stable_since >= stable_window_s:
                            self._log("VERIFY | save complete: %s size=%d", path, size)
                            return True
                    else:
                        last_size = size
                        stable_since = None

                time.sleep(poll_s)

            self._log("VERIFY | save not confirmed within timeout: %s", path)
            return False

    # def verify_save(
    #     self,
    #     target_dir: str | Path,
    #     filename: str,
    #     timeout_s: float = 5.0,
    #     stable_window_s: float = 0.4,
    #     poll_s: float = 0.1,
    # ) -> bool:
    #     """
    #     Verify that the file exists and its size has stabilized.
    #     """
    #     with self.action_lock:
    #         path = Path(target_dir) / filename
    #         self._log("ACTION | verify_save -> %s", path)

    #         deadline = time.monotonic() + timeout_s
    #         last_size: Optional[int] = None
    #         stable_since: Optional[float] = None

    #         while time.monotonic() < deadline:
    #             if path.exists():
    #                 size = path.stat().st_size
    #                 if last_size == size:
    #                     if stable_since is None:
    #                         stable_since = time.monotonic()
    #                     elif time.monotonic() - stable_since >= stable_window_s:
    #                         self._log("VERIFY | save complete: %s size=%d", path, size)
    #                         return True
    #                 else:
    #                     last_size = size
    #                     stable_since = None

    #             time.sleep(poll_s)

    #         self._log("VERIFY | save not confirmed within timeout: %s", path)
    #         return False

    # ------------------------------------------------------------------
    # Watchlist actions
    # ------------------------------------------------------------------

    def _require_symbol_text(self, symbols: str) -> str:
        """
        Validate symbol text before interacting with the ToS symbols import dialog.

        ToS can lock up if the clipboard is empty when the symbols import
        radio buttons are clicked, so refuse empty/whitespace-only input.
        """
        symbol_text = symbols.strip()
        if not symbol_text:
            raise RuntimeError(
                "Refusing to open symbols import with empty symbol text. "
                "The ToS symbols import dialog can lock up if the clipboard is empty."
            )
        return symbol_text

    def put_symbols_on_clipboard(self, symbols: str) -> None:
        """
        Put symbol text on the Windows clipboard.

        The text is passed through unchanged except for trimming leading/trailing
        whitespace. Newline-separated symbols are recommended.
        """
        with self.action_lock:
            symbol_text = self._require_symbol_text(symbols)

            self._log("ACTION | put_symbols_on_clipboard")
            self._log("ACTION | symbol text length -> %d", len(symbol_text))

            subprocess.run(
                ["clip"],
                input=symbol_text,
                text=True,
                check=True,
            )

    def open_watchlist_symbols_import(self) -> None:
        with self.action_lock:
            self._log("ACTION | open_watchlist_symbols_import")
            self._bring_named_window_to_front("win_wl_main")

            self._move_center("btn_wl_actions")
            self._click()
            self._move_vh("pick_wl_import")
            self._click()

            if not self._wait_for_window("win_wl_symbols_import", timeout_s=2.0):
                raise RuntimeError(
                    "win_wl_symbols_import did not appear after watchlist import path."
                )

            self._log("GUI | win_wl_symbols_import detected")

    def _apply_watchlist_symbols_from_clipboard(self, *, mode: str) -> None:
        """
        Apply symbols already on the clipboard in the symbols import dialog.

        mode must be either "replace" or "add".
        """
        if mode not in {"replace", "add"}:
            raise ValueError(f"Unsupported watchlist symbol import mode: {mode!r}")

        mode_widget = {
            "replace": "rbutt_si_replace",
            "add": "rbutt_si_add",
        }[mode]

        self._log("ACTION | apply_watchlist_symbols_from_clipboard -> %s", mode)
        self._bring_named_window_to_front("win_wl_symbols_import")

        self._move_center("rbutt_si_paste")
        self._click()

        self._move_center(mode_widget)
        self._click()

        self._move_vh("btn_si_save")
        self._click()

    def replace_watchlist_symbols(self, symbols: str) -> None:
        """
        Replace the Default watchlist symbols using clipboard import.
        """
        symbol_text = self._require_symbol_text(symbols)

        with self.action_lock:
            self._log("ACTION | replace_watchlist_symbols")

        self.put_symbols_on_clipboard(symbol_text)
        self.select_watchlist_default()
        self.open_watchlist_symbols_import()

        with self.action_lock:
            self._apply_watchlist_symbols_from_clipboard(mode="replace")

    def add_watchlist_symbols(self, symbols: str) -> None:
        """
        Add symbols to the Default watchlist using clipboard import.
        """
        symbol_text = self._require_symbol_text(symbols)

        with self.action_lock:
            self._log("ACTION | add_watchlist_symbols")

        self.put_symbols_on_clipboard(symbol_text)
        self.select_watchlist_default()
        self.open_watchlist_symbols_import()

        with self.action_lock:
            self._apply_watchlist_symbols_from_clipboard(mode="add")

    def select_watchlist_default(self) -> None:
        with self.action_lock:
            self._log("ACTION | select_watchlist_default")
            self._bring_named_window_to_front("win_wl_main")

            self._move_center("btn_wl_actions")
            self._click()
            self._move_vh("pick_wl_personal")
            self._click()
            self._move_hv("pick_wl_default")
            self._click()

    def select_watchlist_scan50_data(self) -> None:
        with self.action_lock:
            self._log("ACTION | select_watchlist_scan50_data")
            self._bring_named_window_to_front("win_wl_main")

            self._move_center("btn_wl_actions")
            self._click()
            self._move_vh("pick_wl_personal")
            self._click()
            self._move_hv("pick_wl_scan50_data")
            self._click()

    def open_watchlist_export(self) -> None:
        with self.action_lock:
            self._log("ACTION | open_watchlist_export")
            self._bring_named_window_to_front("win_wl_main")

            self._move_center("btn_wl_export_menu")
            self._click()
            self._move_vh("pick_wl_export")
            self._click()

            if not self._wait_for_window("win_wl_export", timeout_s=5.0):
                raise RuntimeError("win_wl_export did not appear after watchlist export path.")

            self._log("GUI | win_wl_export detected")

    def enter_watchlist_filename(self, filename: str, target_dir: str | Path) -> None:
        with self.action_lock:
            self._enter_filename_in_export_dialog(
                window_name="win_wl_export",
                filename_widget="ledit_wl_fname",
                filename=filename,
                target_dir=target_dir,
                log_label="watchlist",
            )

    def enter_watchlist_filename_then_export_directory(
        self,
        filename: str,
        target_dir: str | Path,
    ) -> None:
        with self.action_lock:
            self._enter_filename_then_export_directory(
                window_name="win_wl_export",
                filename_widget="ledit_wl_fname",
                directory_widget="ledit_wl_dir",
                filename=filename,
                target_dir=target_dir,
                log_label="watchlist",
            )

    def confirm_watchlist_save(self) -> None:
        with self.action_lock:
            self._confirm_export_save(
                window_name="win_wl_export",
                save_button_widget="btn_wl_save",
                log_label="watchlist",
            )

    def cancel_watchlist_export(self) -> None:
        with self.action_lock:
            self._cancel_export_dialog(
                window_name="win_wl_export",
                cancel_button_widget="btn_wl_cancel",
                log_label="watchlist",
            )

    # ------------------------------------------------------------------
    # Misc / diagnostics
    # ------------------------------------------------------------------

    def nop(self) -> None:
        self._log("ACTION | nop")

    def scan_region_is_active(
        self,
        *,
        widget_name: str = "ocr_hacker_MyR5",
        delta_gray: int = 18,
        min_light_pct: float = 1.2,
        min_stddev: float = 6.0,
    ) -> bool:
        widget = self._widget(widget_name)
        x, y = widget.get_absolute_position()
        w = widget.bbox.width
        h = widget.bbox.height

        self._log("CHECK | capture scan activity region -> %s @ (%d, %d) %dx%d", widget_name, x, y, w, h)

        screenshot = pyautogui.screenshot(region=(x, y, w, h))
        gray = screenshot.convert("L")
        arr = np.array(gray, dtype=np.uint8)

        median = float(np.median(arr))
        stddev = float(np.std(arr))
        light_mask = arr > (median + delta_gray)
        light_pct = 100.0 * float(np.mean(light_mask))

        active = (light_pct >= min_light_pct) or (stddev >= min_stddev)

        self._log(
            "CHECK | scan_region_is_active median=%.1f stddev=%.2f light_pct=%.2f%% active=%s",
            median,
            stddev,
            light_pct,
            active,
        )
        return active

    def manual_init(self, *, user_wait_s: float = 15.0) -> None:
        with self.action_lock:
            self._log("ACTION | manual_init begin")

            self._bring_named_window_to_front("win_main")
            self._move_center("tab_main_scan")
            self._click()

            self._move_center("btn_stock_hacker")
            self._click()

            self._move_center("btn_scan_actions")
            self._click()
            self._move_vh("pick_scan_export")
            self._click()
            self._move_hv("pick_scan_to_file")
            self._click()

            if self._wait_for_window("win_export", timeout_s=2.0):
                self._log("ACTION | manual_init waiting %.1f seconds for user adjustments", user_wait_s)
                time.sleep(user_wait_s)

                if self._wait_for_window("win_export", timeout_s=0.1):
                    self._log("ACTION | manual_init cancelling still-open save dialog")
                    self._bring_named_window_to_front("win_export")
                    self._move_vh("btn_exp_cancel")
                    self._click()
            else:
                self._log("WARNING | manual_init did not detect win_export after export path")

            self._log("ACTION | manual_init end")

    def unlock_scan(
        self,
        *,
        max_passes: int = 3,
        settle_s: float = 2.0,
    ) -> bool:
        with self.action_lock:
            self._log("ACTION | unlock_scan begin")

            self._bring_named_window_to_front("win_main")
            self._move_center("tab_main_scan")
            self._click()

            self._move_center("btn_stock_hacker")
            self._click()

            scan_locked = not self.scan_region_is_active()
            pass_num = 1

            while pass_num <= max_passes and scan_locked:
                self._log("ACTION | unlock_scan pass %d starting", pass_num)

                self.load_pct_gainers_query()
                self.trigger_scan()
                self._log("ACTION | unlock_scan waiting %.1f seconds after pct_gainers", settle_s)
                time.sleep(settle_s)

                self.load_scan50_query()
                self.trigger_scan()
                self._log("ACTION | unlock_scan waiting %.1f seconds after scan050_data", settle_s)
                time.sleep(settle_s)

                scan_locked = not self.scan_region_is_active()
                pass_num += 1

            unlocked = not scan_locked
            self._log("ACTION | unlock_scan end unlocked=%s", unlocked)
            return unlocked
        
    def user_scan(self, *, pre_wait_s: float = 1.0) -> None:
        """
        User-initiated scan sequence:
        - bring main window to front
        - click tab_main_scan
        - wait 1 second
        - click btn_hacker_scan
        """
        with self.action_lock:
            self._log("ACTION | user_scan begin")

            self._bring_named_window_to_front("win_main")
            self._move_center("tab_main_scan")
            self._click()

            self._log("ACTION | user_scan waiting %.1f seconds before btn_hacker_scan", pre_wait_s)
            time.sleep(pre_wait_s)

            self._move_center("btn_hacker_scan")
            self._log("ACTION | btn_hacker_scan pressed for user scan")
            self._click()

            self._log("ACTION | user_scan end")


def install_diagnostic_hotkeys(controller: ToSActionsController, make_filename, target_dir):
    """
    Install manual diagnostic hotkeys for ToSActionsController.

    Returns the hotkey mapping so the caller can inspect/log it if desired.

    Hotkeys:
        Ctrl+Alt+I  manual_init
        Ctrl+Alt+U  unlock_scan
        Ctrl+Alt+S  user_scan
        Ctrl+Alt+E  export_csv_file
        Ctrl+Alt+F  enter_filename
        Ctrl+Alt+C  confirm_save
        Ctrl+Alt+X  cancel_export
        Ctrl+Alt+N  nop
    """
    from pynput import keyboard

    def enter_filename_action() -> None:
        filename = make_filename()
        controller.enter_filename(filename, target_dir)

    hotkeys = {
        "<ctrl>+<alt>+i": controller.manual_init,
        "<ctrl>+<alt>+u": controller.unlock_scan,
        "<ctrl>+<alt>+s": controller.user_scan,
        "<ctrl>+<alt>+e": controller.export_csv_file,
        "<ctrl>+<alt>+f": enter_filename_action,
        "<ctrl>+<alt>+c": controller.confirm_save,
        "<ctrl>+<alt>+x": controller.cancel_export,
        "<ctrl>+<alt>+n": controller.nop,
    }

    listener = keyboard.GlobalHotKeys(hotkeys)
    listener.start()

    controller._log("Installed diagnostic hotkeys:")
    for combo in hotkeys:
        controller._log("  %s", combo)

    return listener

