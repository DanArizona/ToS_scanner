# scan_command_loop.py

from __future__ import annotations

import argparse
import logging
import os
import threading
import time
from collections.abc import Sequence
from pathlib import Path

from config import load_scanner_config
from file_command_ingress import FileCommandIngress
from scan_dispatcher import ScanDispatcher, ScanRuntimeFlags
from scan_job_queue import ScanJobQueue
from scan_jobs import JobKind, JobResult
from scanner_heartbeat import ScannerHeartbeatPublisher
from tos_pwidget_actions import ToSActionsController
from tos_scan_action_executor import ToSScanActionExecutor


USER_CLEAR_DELAY_S = 2.0
ENV_SCAN_CONTROL = "MB_SCAN_CONTROL"

DEFAULT_COMMAND_ROOT = Path(
    r"C:\Users\DanLa\Documents\github\stockScans_control"
)

UI_ACTION_JOBS = {
    JobKind.EXPORT_WL,
    JobKind.EXPORT_TS,
    JobKind.EXPORT_TM,
    JobKind.REPLACE_WL_SYMBOLS,
    JobKind.ADD_WL_SYMBOLS,
}


def parse_args(
    argv: Sequence[str] | None = None,
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the ToS scanner file-command loop."
        ),
    )

    parser.add_argument(
        "--root",
        type=Path,
        help=(
            "Scanner command root. Defaults to MB_SCAN_CONTROL "
            "when set, otherwise uses the local development root."
        ),
    )

    return parser.parse_args(argv)


def resolve_command_root(
    explicit_root: Path | None,
) -> Path:
    """
    Resolve the scanner command root.

    Precedence:
        1. Explicit --root argument
        2. MB_SCAN_CONTROL environment variable
        3. Local El-Cheapo development default
    """
    if explicit_root is not None:
        root_text = str(explicit_root)
    else:
        configured_root = os.environ.get(
            ENV_SCAN_CONTROL,
            "",
        ).strip()

        if configured_root:
            root_text = configured_root
        else:
            root_text = str(DEFAULT_COMMAND_ROOT)

    return Path(
        os.path.expandvars(root_text)
    ).expanduser()


def build_logger() -> logging.Logger:
    logger = logging.getLogger("scan_command_loop")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )
        logger.addHandler(handler)

    return logger


def _wait_for_operator(
    *,
    heartbeat: ScannerHeartbeatPublisher,
    flags: ScanRuntimeFlags,
    last_result: JobResult | None,
    refresh_poll_s: float = 0.5,
) -> None:
    """
    Wait for the operator while keeping the waiting heartbeat current.

    refresh_poll_s controls how often the publisher is called. The
    publisher itself still rate-limits disk writes using interval_s.
    """
    stop_event = threading.Event()

    heartbeat.publish(
        running=flags.running,
        paused=flags.paused,
        shutdown_requested=flags.shutdown_requested,
        loop_state="waiting_for_operator",
        last_result=last_result,
        force=True,
    )

    def refresh_waiting_heartbeat() -> None:
        while not stop_event.wait(refresh_poll_s):
            heartbeat.publish(
                running=flags.running,
                paused=flags.paused,
                shutdown_requested=flags.shutdown_requested,
                loop_state="waiting_for_operator",
                last_result=last_result,
            )

    heartbeat_thread = threading.Thread(
        target=refresh_waiting_heartbeat,
        name="OperatorWaitHeartbeat",
        daemon=True,
    )
    heartbeat_thread.start()

    try:
        input(
            "\nScanner command loop setup:\n"
            "  1. Confirm the Main scanner window and Watchlist "
            "window are both open.\n"
            "  2. Leave both ToS windows in their expected "
            "positions.\n"
            "  3. It is okay if File Explorer, VS Code, or the "
            "browser is in front;\n"
            "     each export action will surface its target ToS "
            "window when needed.\n"
            "  4. Drop JSON command files into the incoming folder "
            "after the loop starts.\n"
            "  5. Then press Enter here to start watching incoming "
            "commands.\n\n"
            "Press Enter when ready..."
        )
    finally:
        stop_event.set()
        heartbeat_thread.join()


def _publish_stopped_heartbeat(
    *,
    heartbeat: ScannerHeartbeatPublisher,
    flags: ScanRuntimeFlags,
    last_result: JobResult | None,
) -> None:
    """Publish a consistent final stopped state."""
    flags.running = False
    flags.paused = False
    flags.shutdown_requested = True

    heartbeat.publish(
        running=flags.running,
        paused=flags.paused,
        shutdown_requested=flags.shutdown_requested,
        loop_state="stopped",
        current_job=None,
        last_result=last_result,
        force=True,
    )


def main(
    argv: Sequence[str] | None = None,
) -> None:
    args = parse_args(argv)
    logger = build_logger()

    command_root = resolve_command_root(args.root)

    ingress = FileCommandIngress(
        command_root=command_root,
        logger=logger,
    )

    job_queue = ScanJobQueue()
    flags = ScanRuntimeFlags()

    heartbeat = ScannerHeartbeatPublisher(
        command_root=command_root,
        interval_s=5.0,
    )

    last_result: JobResult | None = None

    cfg = load_scanner_config()

    controller = ToSActionsController(
        layout_path=cfg.pwidget_yaml_path,
        cfg=cfg,
        logger=logger,
    )

    try:
        _wait_for_operator(
            heartbeat=heartbeat,
            flags=flags,
            last_result=last_result,
        )
    except KeyboardInterrupt:
        logger.info(
            "KeyboardInterrupt received while waiting for operator."
        )
        _publish_stopped_heartbeat(
            heartbeat=heartbeat,
            flags=flags,
            last_result=last_result,
        )
        logger.info("V2 command loop stopped.")
        return

    logger.info("Scanner command loop starting in 2 seconds.")

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
    logger.info(
        "Drop JSON command files into: %s",
        command_root / "incoming",
    )
    logger.info("Use Ctrl+C to exit, or send a stop command.")

    heartbeat.publish(
        running=flags.running,
        paused=flags.paused,
        shutdown_requested=flags.shutdown_requested,
        loop_state="idle",
        last_result=last_result,
        force=True,
    )

    try:
        while not flags.shutdown_requested:
            loop_state = "paused" if flags.paused else "idle"

            heartbeat.publish(
                running=flags.running,
                paused=flags.paused,
                shutdown_requested=flags.shutdown_requested,
                loop_state=loop_state,
                last_result=last_result,
            )

            accepted_count = ingress.add_pending_jobs(job_queue)

            if accepted_count:
                logger.info(
                    "Accepted %d command file(s).",
                    accepted_count,
                )

            while not job_queue.empty():
                job = job_queue.get_next(timeout=0)

                if job is None:
                    break

                heartbeat.publish(
                    running=flags.running,
                    paused=flags.paused,
                    shutdown_requested=flags.shutdown_requested,
                    loop_state="busy",
                    current_job=job,
                    last_result=last_result,
                    force=True,
                )

                try:
                    if job.kind in UI_ACTION_JOBS:
                        logger.info(
                            "Operator-clear delay: %.1f seconds before %s.",
                            USER_CLEAR_DELAY_S,
                            job.kind.value,
                        )
                        time.sleep(USER_CLEAR_DELAY_S)

                    result = dispatcher.execute(job)
                    last_result = result

                    print()
                    print("JobResult")
                    print("---------")
                    print(f"kind       : {result.request.kind}")
                    print(
                        f"command_id : "
                        f"{result.request.command_id}"
                    )
                    print(f"ok         : {result.ok}")
                    print(f"message    : {result.message}")
                    print(f"running    : {flags.running}")
                    print(f"paused     : {flags.paused}")
                    print(
                        f"shutdown   : "
                        f"{flags.shutdown_requested}"
                    )
                    print()

                finally:
                    job_queue.task_done()

                    if flags.shutdown_requested:
                        next_loop_state = "stopped"
                    elif flags.paused:
                        next_loop_state = "paused"
                    else:
                        next_loop_state = "idle"

                    heartbeat.publish(
                        running=flags.running,
                        paused=flags.paused,
                        shutdown_requested=(
                            flags.shutdown_requested
                        ),
                        loop_state=next_loop_state,
                        current_job=None,
                        last_result=last_result,
                        force=True,
                    )

                if flags.shutdown_requested:
                    logger.info(
                        "Shutdown requested; stopping command loop."
                    )
                    break

            time.sleep(0.5)

    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received; exiting.")

    finally:
        _publish_stopped_heartbeat(
            heartbeat=heartbeat,
            flags=flags,
            last_result=last_result,
        )

    logger.info("V2 command loop stopped.")


if __name__ == "__main__":
    main()
