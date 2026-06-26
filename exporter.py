# exporter.py

"""Scan export implementations for the ToS scanner.

This module defines the scan-export protocol and the ThinkOrSwim pseudo-widget
exporter used to drive GUI actions or create dry-run CSV output.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Protocol
from zoneinfo import ZoneInfo

from config import ScannerConfig
from layout import load_widget_layout
from tos_pwidget_actions import ToSActionsController


ET = ZoneInfo("America/New_York")


class ScanExporter(Protocol):
    def export_scan(
        self,
        csv_path: Path,
        slot_et: datetime,
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        ...

    def perform_user_scan(
        self,
        *,
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        ...


class ToSPseudoWidgetExporter:
    """
    Reuses the tested ToSActionsController sequence:
      export_csv_file()
      enter_filename(filename, target_dir)
      confirm_save()
      verify_save(target_dir, filename)

    Also provides perform_user_scan() for the UI Scan button.
    """

    def __init__(
        self,
        *,
        logger: logging.Logger,
        cfg: ScannerConfig,
        layout_path: Optional[Path] = None,
        dry_run: bool = False,
        verify_timeout_s: float = 10.0,
    ) -> None:
        self.logger = logger
        self.cfg = cfg
        self.dry_run = dry_run
        self.verify_timeout_s = verify_timeout_s

        self.controller = ToSActionsController(
            layout_path=layout_path or cfg.pwidget_yaml_path,
            cfg=cfg,
            logger=logger,
        )

        # Normal operating timing/jitter
        self.controller.ENABLE_RANDOM_TIMING = True
        self.controller.ENABLE_RANDOM_POSITION = True
        self.controller.EXTRA_DELAY_MAX_S = 0.005
        self.controller.JITTER_X_PX = 5
        self.controller.JITTER_Y_PX = 3
        self.controller.MOVE_DURATION_S = 0.08
        self.controller.SEGMENT_DURATION_S = 0.06
        self.controller.CLICK_PAUSE_S = 0.08
        self.controller.STEP_PAUSE_S = 0.15

    def _check_stop(self, stop_event: Optional[threading.Event]) -> None:
        if stop_event is not None and stop_event.is_set():
            raise InterruptedError("Stop requested during export sequence.")




    def setup_scan_export_dialog(
        self,
        *,
        target_dir: Path,
        stop_event: Optional[threading.Event] = None,
        setup_filename: str = "__scan_export_setup__.csv",
    ) -> None:
        """
        One-time setup for the scan export dialog.

        Opens the scan export dialog, normalizes the dialog size, sets the
        export directory, saves a small setup CSV to force ToS to persist the
        directory, verifies the save, then removes the setup CSV.
        """
        self.logger.info("GUI | Begin scan export dialog setup")
        self.logger.info("GUI | Setup export directory: %s", target_dir)

        if self.dry_run:
            self.logger.info("DRY RUN | scan export dialog setup skipped")
            return

        target_dir = Path(target_dir).expanduser().resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        setup_path = target_dir / setup_filename

        if setup_path.exists():
            try:
                setup_path.unlink()
                self.logger.info("GUI | Removed stale setup CSV: %s", setup_path)
            except Exception:
                self.logger.warning(
                    "GUI | Could not remove stale setup CSV before setup save: %s",
                    setup_path,
                    exc_info=True,
                )

        with self.controller.action_lock:
            self._check_stop(stop_event)

            self.controller.export_csv_file()
            self._check_stop(stop_event)

            self.controller.normalize_scan_export_dialog()
            self._check_stop(stop_event)

            self.controller.enter_filename_then_export_directory(
                setup_filename,
                target_dir,
            )
            self._check_stop(stop_event)

            self.controller.confirm_save()
            self._check_stop(stop_event)

            ok = self.controller.verify_save(
                setup_path.parent,
                setup_path.name,
                timeout_s=self.verify_timeout_s,
                stop_event=stop_event,
            )

            if not ok:
                raise TimeoutError(
                    f"Setup CSV file was not verified within timeout: {setup_path}"
                )

        try:
            setup_path.unlink()
            self.logger.info("GUI | Removed setup CSV after verification: %s", setup_path)
        except FileNotFoundError:
            pass
        except Exception:
            self.logger.warning(
                "GUI | Could not remove setup CSV after verification: %s",
                setup_path,
                exc_info=True,
            )

        self.logger.info("GUI | Scan export dialog setup complete")



    # def setup_scan_export_dialog(
    #     self,
    #     *,
    #     target_dir: Path,
    #     stop_event: Optional[threading.Event] = None,
    #     setup_filename: str = "__scan_export_setup__.csv",
    # ) -> None:
        # """
        # One-time setup for the scan export dialog.

        # Opens the scan export dialog, normalizes the dialog size to the YAML
        # geometry, sets the export directory, then cancels the dialog.

        # Recurring exports should then only enter the filename and save.
        # """
        # self.logger.info("GUI | Begin scan export dialog setup")
        # self.logger.info("GUI | Setup export directory: %s", target_dir)

        # if self.dry_run:
        #     self.logger.info("DRY RUN | scan export dialog setup skipped")
        #     return

        # target_dir = Path(target_dir).expanduser().resolve()
        # target_dir.mkdir(parents=True, exist_ok=True)

        # with self.controller.action_lock:
        #     self._check_stop(stop_event)

        #     self.controller.export_csv_file()
        #     self._check_stop(stop_event)

        #     self.controller.normalize_scan_export_dialog()
        #     self._check_stop(stop_event)

        #     self.controller.enter_filename_then_export_directory(
        #         setup_filename,
        #         target_dir,
        #     )
        #     self._check_stop(stop_event)

        #     self.controller.cancel_export()

        # self.logger.info("GUI | Scan export dialog setup complete")


    def export_scan(
        self,
        csv_path: Path,
        slot_et: datetime,
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        self.logger.info("GUI | Begin export for slot %s", slot_et.isoformat())
        self.logger.info("GUI | Target CSV filename: %s", csv_path.name)

        if self.dry_run:
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            csv_path.write_text(
                "Symbol,%Change,Volume,Last\nTEST,+0.00%,1000,1.23\n",
                encoding="utf-8",
            )
            self.logger.info("DRY RUN | Stub CSV created at %s", csv_path)
            return

        # Hold the controller lock for the full export sequence so that
        # maintenance or user-scan actions cannot interleave with it.
        with self.controller.action_lock:
            self._check_stop(stop_event)
            self.controller.export_csv_file()

            self._check_stop(stop_event)
            self.controller.enter_filename(csv_path.name, csv_path.parent)

            self._check_stop(stop_event)
            self.controller.confirm_save()

            self._check_stop(stop_event)
            ok = self.controller.verify_save(
                csv_path.parent,
                csv_path.name,
                timeout_s=self.verify_timeout_s,
                stop_event=stop_event,
            )

            if not ok:
                raise TimeoutError(f"CSV file was not verified within timeout: {csv_path}")

        self.logger.info("CSV output verified: %s", csv_path)

    def perform_user_scan(
        self,
        *,
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        if self.dry_run:
            self.logger.info("DRY RUN | user_scan skipped")
            return

        self._check_stop(stop_event)
        self.controller.user_scan(pre_wait_s=1.0)

