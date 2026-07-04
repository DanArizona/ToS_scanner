# test_file_command_ingress.py

from __future__ import annotations

import logging
from pathlib import Path

from file_command_ingress import FileCommandIngress
from scan_job_queue import ScanJobQueue


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

    while not job_queue.empty():
        job = job_queue.get_next(timeout=0)

        if job is None:
            break

        print("JobRequest")
        print("----------")
        print(f"kind           : {job.kind}")
        print(f"origin         : {job.origin}")
        print(f"requested_at   : {job.requested_at}")
        print(f"source         : {job.source}")
        print(f"symbols        : {job.symbols}")
        print(f"symbol_file    : {job.symbol_file}")
        print(f"output_dir     : {job.output_dir}")
        print(f"target_filename: {job.target_filename}")
        print(f"command_id     : {job.command_id}")
        print()

        job_queue.task_done()


if __name__ == "__main__":
    main()

