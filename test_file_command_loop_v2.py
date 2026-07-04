# test_file_command_loop_v2.py

from __future__ import annotations

import logging
import time
from pathlib import Path

from config import load_scanner_config
from file_command_ingress import FileCommandIngress
from scan_dispatcher import ScanDispatcher, ScanRuntimeFlags
from scan_job_queue import ScanJobQueue
from tos_pwidget_actions import ToSActionsController
from tos_scan_action_executor import ToSScanActionExecutor
from scan_jobs import JobKind

USER_CLEAR_DELAY_S = 2.0

UI_ACTION_JOBS = {
    JobKind.EXPORT_WL,
    JobKind.EXPORT_TS,
    JobKind.EXPORT_TM,
    JobKind.REPLACE_WL_SYMBOLS,
    JobKind.ADD_WL_SYMBOLS,
}

def build_logger() -> logging.Logger:
    logger = logging.getLogger("test_file_command_loop_v2")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )
        logger.addHandler(handler)

    return logger


def main() -> None:
    logger = build_logger()

    command_root = Path(r"C:\Users\DanLa\Documents\github\stockScans_control")

    ingress = FileCommandIngress(
        command_root=command_root,
        logger=logger,
    )

    job_queue = ScanJobQueue()

    flags = ScanRuntimeFlags()

    cfg = load_scanner_config()

    controller = ToSActionsController(
        layout_path=cfg.pwidget_yaml_path,
        cfg=cfg,
        logger=logger,
    )

    logger.info("Manual test warm-up: bringing Watchlist window to front.")
    controller._bring_named_window_to_front("win_wl_main")
    time.sleep(1.0)

    input(
        "\nManual test setup:\n"
        "  1. Confirm the Watchlist window is visible.\n"
        "  2. Move VS Code, File Explorer, browser, and other windows so they do NOT cover it.\n"
        "  3. Leave the Watchlist window in its expected position.\n"
        "  4. Then press Enter here to start watching incoming commands.\n\n"
        "Press Enter when ready..."
    )

    logger.info("Manual test starting in 2 seconds.")
    time.sleep(2.0)
    
    action_executor = ToSScanActionExecutor(
        action_controller=controller,
        output_dir=cfg.scans_path,
        logger=logger,
        dry_run=False,
    )

    dispatcher = ScanDispatcher(
        flags=flags,
        action_executor=action_executor,
        logger=logger,
    )

    logger.info("Starting v2 command loop with real ToS executor.")
    logger.info("Command root: %s", command_root)
    logger.info("Drop JSON command files into: %s", command_root / "incoming")
    logger.info("Use Ctrl+C to exit, or send a stop command.")

    try:
        while not flags.shutdown_requested:
            accepted_count = ingress.add_pending_jobs(job_queue)

            if accepted_count:
                logger.info("Accepted %d command file(s).", accepted_count)

            while not job_queue.empty():
                job = job_queue.get_next(timeout=0)

                if job is None:
                    break

                try:
                    if job.kind in UI_ACTION_JOBS:
                        logger.info(
                            "Manual test delay: %.1f seconds to clear mouse/keyboard before %s.",
                            USER_CLEAR_DELAY_S,
                            job.kind.value,
                        )
                        time.sleep(USER_CLEAR_DELAY_S)

                    result = dispatcher.execute(job)

                    print()
                    print("JobResult")
                    print("---------")
                    print(f"kind       : {result.request.kind}")
                    print(f"command_id : {result.request.command_id}")
                    print(f"ok         : {result.ok}")
                    print(f"message    : {result.message}")
                    print(f"running    : {flags.running}")
                    print(f"paused     : {flags.paused}")
                    print(f"shutdown   : {flags.shutdown_requested}")
                    print()

                finally:
                    job_queue.task_done()

                if flags.shutdown_requested:
                    logger.info("Shutdown requested; stopping command loop.")
                    break

            time.sleep(0.5)

    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received; exiting.")

    logger.info("V2 command loop stopped.")


if __name__ == "__main__":
    main()

