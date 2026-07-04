# test_file_command_ingress.py

from __future__ import annotations

import logging
from pathlib import Path

from file_command_ingress import FileCommandIngress
from scan_job_queue import ScanJobQueue
from scan_dispatcher import ScanDispatcher, ScanRuntimeFlags


def build_logger() -> logging.Logger:
    logger = logging.getLogger("test_file_command_ingress")
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

    # Adjust this path to wherever you created the control directory.
    command_root = Path(r"C:\Users\DanLa\Documents\github\stockScans_control")

    ingress = FileCommandIngress(
        command_root=command_root,
        logger=logger,
    )

    job_queue = ScanJobQueue()

    accepted_count = ingress.add_pending_jobs(job_queue)

    print()
    print(f"Accepted command files: {accepted_count}")
    print(f"Queued jobs: {job_queue.qsize()}")
    print()


    flags = ScanRuntimeFlags()
    dispatcher = ScanDispatcher(flags=flags, logger=logger)

    while not job_queue.empty():
        job = job_queue.get_next(timeout=0)

        if job is None:
            break

        result = dispatcher.execute(job)

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

        job_queue.task_done()

        if flags.shutdown_requested:
            print("Shutdown requested; stopping dispatch loop.")
            break


if __name__ == "__main__":
    main()

