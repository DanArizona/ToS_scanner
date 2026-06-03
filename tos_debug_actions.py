# tos_debug_actions.py

from __future__ import annotations

import random
import threading
import time
import numpy as np

from pathlib import Path
from typing import Optional

import pyautogui
import pygetwindow as gw

from config import ScannerConfig, load_scanner_config
from layout import load_widget_layout
from models import WidgetStack


pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.0


class ToSDebugController:
    """
    Debug controller for ToS pseudo-widget interaction.

    Notes
    -----
    - Uses the layout registry returned by load_widget_layout(...)
    - Uses ScannerConfig.title_map for root-window resolution
    - Adds small randomized delay and cursor jitter
    - Serializes actions with a lock so actions do not overlap
    """

    # Slow and deterministic for debug/observation
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

    def _log(self, msg: str, *args) -> None:
        if args:
            msg = msg % args

        if self.logger:
            self.logger.info(msg)
        else:
            now_str = time.strftime("%H:%M:%S")
            print(f"{now_str} | {msg}")

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

    def _get_matching_window(self, title_prefix: str):
        normalized_prefix = title_prefix.strip()

        for win in gw.getAllWindows():
            title = (win.title or "").strip()
            if title.startswith(normalized_prefix):
                return win

        return None

    def _bring_named_window_to_front(self, widget_name: str) -> None:
        title_prefix = self.cfg.title_map[widget_name]
        win = self._get_matching_window(title_prefix)
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

        self._log("GUI | bring window to front: %s -> %s", widget_name, win.title)
        self._sleep(self.STEP_PAUSE_S)

    def _wait_for_window(self, widget_name: str, timeout_s: float = 2.0) -> bool:
        title_prefix = self.cfg.title_map[widget_name]
        deadline = time.monotonic() + timeout_s

        while time.monotonic() < deadline:
            if self._get_matching_window(title_prefix) is not None:
                return True
            time.sleep(0.05)
        return False

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
            self._move_center("tab_scan")
            self._click()

    def load_scan50_query(self) -> None:
        with self.action_lock:
            self._log("ACTION | load_scan50_query")
            self._bring_named_window_to_front("win_main")
            self._move_center("tab_scan")
            self._click()

            self._move_center("btn_query_menu")
            self._click()
            self._move_vh("pick_load_query")
            self._click()
            self._move_hv("pick_personal_query")
            self._click()
            self._move_hv("pick_scan50_data")
            self._click()

    def load_pct_gainers_query(self) -> None:
        with self.action_lock:
            self._log("ACTION | load_pct_gainers_query")
            self._bring_named_window_to_front("win_main")
            self._move_center("tab_scan")
            self._click()

            self._move_center("btn_query_menu")
            self._click()
            self._move_vh("pick_load_query")
            self._click()
            self._move_hv("pick_public_query")
            self._click()
            self._move_hv("pick_pct_gainers")
            self._click()

    def trigger_scan(self) -> None:
        with self.action_lock:
            self._log("ACTION | trigger_scan")
            self._bring_named_window_to_front("win_main")
            self._move_center("tab_scan")
            self._click()
            self._move_center("btn_scan")
            self._click()

    def export_csv_file(self) -> None:
        with self.action_lock:
            self._log("ACTION | export_csv_file")
            self._bring_named_window_to_front("win_main")
            self._move_center("tab_scan")
            self._click()

            self._move_center("btn_action_menu")
            self._click()
            self._move_vh("pick_export")
            self._click()
            self._move_hv("pick_to_file")
            self._click()

            if not self._wait_for_window("win_saver", timeout_s=2.0):
                raise RuntimeError("win_saver did not appear after export path.")

            self._log("GUI | win_saver detected")


    def enter_filename(self, filename: str, target_dir: str | Path) -> None:
        """
        Enter the full save path into the filename field.

        Example typed text:
            C:\\Users\\DanLa\\Documents\\github\\ToS_scans\\scan-2026-03-13-09-30-05-ToS.csv

        Use confirm_save() to click Save.
        """
        with self.action_lock:
            full_path = str(Path(target_dir) / filename)

            self._log("ACTION | enter_filename -> %s", filename)
            self._log("ACTION | full save path -> %s", full_path)

            self._bring_named_window_to_front("win_saver")
            self._move_center("ledit_fname")
            self._click()
            self._select_all()
            self._delete_selection()
            self._type_text(full_path)


    # def enter_filename(self, filename: str) -> None:
    #     """
    #     Enter the filename only.
    #     Use confirm_save() to click Save.
    #     """
    #     with self.action_lock:
    #         self._log("ACTION | enter_filename -> %s", filename)
    #         self._bring_named_window_to_front("win_saver")
    #         self._move_center("ledit_fname")
    #         self._click()
    #         self._select_all()
    #         self._delete_selection()
    #         self._type_text(filename)

    def confirm_save(self) -> None:
        with self.action_lock:
            self._log("ACTION | confirm_save")
            self._bring_named_window_to_front("win_saver")
            self._move_vh("btn_save_file")
            self._click()

    def cancel_export(self) -> None:
        with self.action_lock:
            self._log("ACTION | cancel_export")
            self._bring_named_window_to_front("win_saver")
            self._move_vh("btn_save_cancel")
            self._click()

    def verify_save(
        self,
        target_dir: str | Path,
        filename: str,
        timeout_s: float = 5.0,
        stable_window_s: float = 0.4,
        poll_s: float = 0.1,
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

    def nop(self) -> None:
        self._log("ACTION | nop")

    def scan_region_is_active(
        self,
        *,
        widget_name: str = "ocr_MyR_5",
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
            self._move_center("tab_scan")
            self._click()

            self._move_center("btn_stock_hacker")
            self._click()

            self._move_center("btn_action_menu")
            self._click()
            self._move_vh("pick_export")
            self._click()
            self._move_hv("pick_to_file")
            self._click()

            if self._wait_for_window("win_saver", timeout_s=2.0):
                self._log("ACTION | manual_init waiting %.1f seconds for user adjustments", user_wait_s)
                time.sleep(user_wait_s)

                if self._wait_for_window("win_saver", timeout_s=0.1):
                    self._log("ACTION | manual_init cancelling still-open save dialog")
                    self._bring_named_window_to_front("win_saver")
                    self._move_vh("btn_save_cancel")
                    self._click()
            else:
                self._log("WARNING | manual_init did not detect win_saver after export path")

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
            self._move_center("tab_scan")
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
        - click tab_scan
        - wait 1 second
        - click btn_scan
        """
        with self.action_lock:
            self._log("ACTION | user_scan begin")

            self._bring_named_window_to_front("win_main")
            self._move_center("tab_scan")
            self._click()

            self._log("ACTION | user_scan waiting %.1f seconds before btn_scan", pre_wait_s)
            time.sleep(pre_wait_s)

            self._move_center("btn_scan")
            self._log("ACTION | btn_scan pressed for user scan")
            self._click()

            self._log("ACTION | user_scan end")