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
from export_gate import ExportGate
from file_command_ingress import (
    FileCommandIngress,
)
from scan_dispatcher import (
    ScanDispatcher,
    ScanRuntimeFlags,
)
from scan_job_queue import ScanJobQueue
from scan_jobs import JobKind, JobResult
from scanner_heartbeat import (
    ScannerHeartbeatPublisher,
)
from tos_pwidget_actions import (
    ToSActionsController,
)
from tos_scan_action_executor import (
    ToSScanActionExecutor,
)


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
            "Run the ToS scanner file-command "
            "loop."
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        help=(
            "Scanner command root. Defaults to "
            "MB_SCAN_CONTROL when set, "
            "otherwise uses the local "
            "development root."
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
            root_text = str(
                DEFAULT_COMMAND_ROOT
            )

    return Path(
        os.path.expandvars(root_text)
    ).expanduser()


def build_logger() -> logging.Logger:
    logger = logging.getLogger(
        "scan_command_loop"
    )
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s "
                "%(message)s"
            )
        )
        logger.addHandler(handler)

    return logger


def _load_runtime_flags(
    export_gate: ExportGate,
    logger: logging.Logger,
) -> ScanRuntimeFlags:
    """Initialize runtime flags from the persisted export gate."""

    snapshot = export_gate.snapshot()

    if snapshot.error is not None:
        logger.error(
            "Export gate is fail-closed: %s",
            snapshot.error,
        )

    return ScanRuntimeFlags(
        exports_suspended=(
            snapshot.suspended
        ),
    )


def _loop_state(
    flags: ScanRuntimeFlags,
) -> str:
    if flags.shutdown_requested:
        return "stopped"

    if flags.paused:
        return "paused"

    if flags.exports_suspended:
        return "exports_suspended"

    return "idle"


def _publish_heartbeat(
    *,
    heartbeat: ScannerHeartbeatPublisher,
    flags: ScanRuntimeFlags,
    loop_state: str,
    current_job=None,
    last_result: JobResult | None = None,
    force: bool = False,
) -> bool:
    """Publish current command-loop state consistently."""

    return heartbeat.publish(
        running=flags.running,
        paused=flags.paused,
        exports_suspended=(
            flags.exports_suspended
        ),
        shutdown_requested=(
            flags.shutdown_requested
        ),
        loop_state=loop_state,
        current_job=current_job,
        last_result=last_result,
        force=force,
    )


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

    _publish_heartbeat(
        heartbeat=heartbeat,
        flags=flags,
        loop_state="waiting_for_operator",
        last_result=last_result,
        force=True,
    )

    def refresh_waiting_heartbeat() -> None:
        while not stop_event.wait(
            refresh_poll_s
        ):
            _publish_heartbeat(
                heartbeat=heartbeat,
                flags=flags,
                loop_state=(
                    "waiting_for_operator"
                ),
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
            "  1. Confirm the Main scanner "
            "window and Watchlist window are "
            "both open.\n"
            "  2. Leave both ToS windows in "
            "their expected positions.\n"
            "  3. It is okay if File Explorer, "
            "VS Code, or the browser is in "
            "front;\n"
            "     each export action will "
            "surface its target ToS window when "
            "needed.\n"
            "  4. Drop JSON command files into "
            "the incoming folder after the loop "
            "starts.\n"
            "  5. Then press Enter here to start "
            "watching incoming commands.\n\n"
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

    _publish_heartbeat(
        heartbeat=heartbeat,
        flags=flags,
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

    command_root = resolve_command_root(
        args.root
    )
    ingress = FileCommandIngress(
        command_root=command_root,
        logger=logger,
    )

    job_queue = ScanJobQueue()
    export_gate = ExportGate(command_root)
    flags = _load_runtime_flags(
        export_gate,
        logger,
    )

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
            "KeyboardInterrupt received while "
            "waiting for operator."
        )

        try:
            _publish_stopped_heartbeat(
                heartbeat=heartbeat,
                flags=flags,
                last_result=last_result,
            )
        finally:
            export_gate.close()

        logger.info(
            "V2 command loop stopped."
        )
        return

    logger.info(
        "Scanner command loop starting in "
        "2 seconds."
    )

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
        export_gate=export_gate,
    )

    logger.info(
        "Starting v2 command loop with real "
        "ToS executor."
    )
    logger.info(
        "Command root: %s",
        command_root,
    )
    logger.info(
        "Drop JSON command files into: %s",
        command_root / "incoming",
    )
    logger.info(
        "Use Ctrl+C to exit, or send a stop "
        "command."
    )

    _publish_heartbeat(
        heartbeat=heartbeat,
        flags=flags,
        loop_state=_loop_state(flags),
        last_result=last_result,
        force=True,
    )

    try:
        while not flags.shutdown_requested:
            _publish_heartbeat(
                heartbeat=heartbeat,
                flags=flags,
                loop_state=_loop_state(flags),
                last_result=last_result,
            )

            accepted_count = (
                ingress.add_pending_jobs(
                    job_queue
                )
            )

            if accepted_count:
                logger.info(
                    "Accepted %d command file(s).",
                    accepted_count,
                )

            while not job_queue.empty():
                job = job_queue.get_next(
                    timeout=0
                )

                if job is None:
                    break

                _publish_heartbeat(
                    heartbeat=heartbeat,
                    flags=flags,
                    loop_state="busy",
                    current_job=job,
                    last_result=last_result,
                    force=True,
                )

                try:
                    if (
                        job.kind
                        in UI_ACTION_JOBS
                    ):
                        logger.info(
                            "Operator-clear delay: "
                            "%.1f seconds before %s.",
                            USER_CLEAR_DELAY_S,
                            job.kind.value,
                        )
                        time.sleep(
                            USER_CLEAR_DELAY_S
                        )

                    result = (
                        dispatcher.execute(job)
                    )
                    last_result = result

                    print()
                    print("JobResult")
                    print("---------")
                    print(
                        f"kind       : "
                        f"{result.request.kind}"
                    )
                    print(
                        f"command_id : "
                        f"{result.request.command_id}"
                    )
                    print(
                        f"ok         : "
                        f"{result.ok}"
                    )
                    print(
                        f"message    : "
                        f"{result.message}"
                    )
                    print(
                        f"running    : "
                        f"{flags.running}"
                    )
                    print(
                        f"paused     : "
                        f"{flags.paused}"
                    )
                    print(
                        f"exports    : "
                        f"{'suspended' if flags.exports_suspended else 'active'}"
                    )
                    print(
                        f"shutdown   : "
                        f"{flags.shutdown_requested}"
                    )
                    print()
                finally:
                    job_queue.task_done()

                    _publish_heartbeat(
                        heartbeat=heartbeat,
                        flags=flags,
                        loop_state=(
                            _loop_state(flags)
                        ),
                        current_job=None,
                        last_result=last_result,
                        force=True,
                    )

                if flags.shutdown_requested:
                    logger.info(
                        "Shutdown requested; "
                        "stopping command loop."
                    )
                    break

            time.sleep(0.5)

    except KeyboardInterrupt:
        logger.info(
            "KeyboardInterrupt received; "
            "exiting."
        )

    finally:
        try:
            _publish_stopped_heartbeat(
                heartbeat=heartbeat,
                flags=flags,
                last_result=last_result,
            )
        finally:
            export_gate.close()

    logger.info("V2 command loop stopped.")


if __name__ == "__main__":
    main()
