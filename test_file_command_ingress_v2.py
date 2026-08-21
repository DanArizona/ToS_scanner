# test_file_command_ingress.py

from __future__ import annotations

import logging
from pathlib import Path

import pytest

import file_command_ingress
from file_command_ingress import FileCommandIngress
from scan_dispatcher import ScanDispatcher, ScanRuntimeFlags
from scan_job_queue import ScanJobQueue
from scan_jobs import JobKind


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


def test_processing_file_permission_error_is_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command_root = tmp_path / "SCANCTRL"

    incoming_dir = command_root / "incoming"
    incoming_dir.mkdir(parents=True)

    command_path = (
        incoming_dir / "test-resume.json"
    )

    command_path.write_text(
        '{"command": "resume_exports"}',
        encoding="utf-8",
    )

    ingress = FileCommandIngress(
        command_root=command_root,
    )
    job_queue = ScanJobQueue()

    original_read_text = Path.read_text
    processing_read_attempts = 0

    def flaky_read_text(
        self: Path,
        *args,
        **kwargs,
    ) -> str:
        nonlocal processing_read_attempts

        if (
            self.parent.name == "processing"
            and self.name == "test-resume.json"
        ):
            processing_read_attempts += 1

            if processing_read_attempts == 1:
                raise PermissionError(
                    "processing file temporarily locked"
                )

        return original_read_text(
            self,
            *args,
            **kwargs,
        )

    monkeypatch.setattr(
        Path,
        "read_text",
        flaky_read_text,
    )

    accepted_count = ingress.add_pending_jobs(
        job_queue
    )

    assert accepted_count == 1
    assert processing_read_attempts == 2
    assert job_queue.qsize() == 1

    job = job_queue.get_next(timeout=0)

    assert job is not None
    assert job.kind is JobKind.RESUME_EXPORTS

    assert not list(
        (command_root / "failed").glob("*.json")
    )
    assert not list(
        (command_root / "failed").glob(
            "*.error.txt"
        )
    )

    assert (
        command_root
        / "processed"
        / "test-resume.json"
    ).exists()


def test_processing_file_persistent_permission_error_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command_root = tmp_path / "SCANCTRL"

    incoming_dir = command_root / "incoming"
    incoming_dir.mkdir(parents=True)

    command_path = (
        incoming_dir / "test-resume.json"
    )

    command_path.write_text(
        '{"command": "resume_exports"}',
        encoding="utf-8",
    )

    ingress = FileCommandIngress(
        command_root=command_root,
    )
    job_queue = ScanJobQueue()

    original_read_text = Path.read_text
    processing_read_attempts = 0

    def locked_read_text(
        self: Path,
        *args,
        **kwargs,
    ) -> str:
        nonlocal processing_read_attempts

        if (
            self.parent.name == "processing"
            and self.name == "test-resume.json"
        ):
            processing_read_attempts += 1

            raise PermissionError(
                "processing file remains locked"
            )

        return original_read_text(
            self,
            *args,
            **kwargs,
        )

    monkeypatch.setattr(
        Path,
        "read_text",
        locked_read_text,
    )

    monkeypatch.setattr(
        file_command_ingress,
        "PROCESSING_READ_RETRY_SECONDS",
        0.02,
    )

    monkeypatch.setattr(
        file_command_ingress,
        "PROCESSING_READ_RETRY_INTERVAL_SECONDS",
        0.001,
    )

    accepted_count = ingress.add_pending_jobs(
        job_queue
    )

    assert accepted_count == 0
    assert processing_read_attempts >= 2
    assert job_queue.qsize() == 0

    assert not list(
        (command_root / "processed").glob("*.json")
    )

    failed_files = list(
        (command_root / "failed").glob("*.json")
    )

    assert len(failed_files) == 1
    assert failed_files[0].name == "test-resume.json"

    error_path = failed_files[0].with_suffix(
        ".json.error.txt"
    )

    assert error_path.exists()

    assert (
        "processing file remains locked"
        in error_path.read_text(encoding="utf-8")
    )


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

