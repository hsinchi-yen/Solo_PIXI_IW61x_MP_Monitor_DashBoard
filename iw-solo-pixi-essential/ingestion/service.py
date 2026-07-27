from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Protocol

from parsers.iw61x import parse_iw61x_log_file


# How many files to parse in parallel and how often to commit
_PARSE_WORKERS = min(4, (os.cpu_count() or 2))
_COMMIT_EVERY = 10          # commit transaction every N uploaded files


@dataclass
class FileIngestionResult:
    source_file: str
    status: str
    result: str | None = None
    work_order: str | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class BatchReport:
    total_files: int = 0
    uploaded: int = 0
    duplicates: int = 0
    rejected: int = 0
    warnings: int = 0
    files: list[FileIngestionResult] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "total_files": self.total_files,
            "uploaded": self.uploaded,
            "duplicates": self.duplicates,
            "rejected": self.rejected,
            "warnings": self.warnings,
            "files": [f.__dict__ for f in self.files],
        }


class RunRepository(Protocol):
    def has_hash(self, file_hash: str) -> bool:
        ...

    def save(self, parsed, source: str = "api") -> int:
        ...


class InMemoryRunRepository:
    def __init__(self):
        self.records = []
        self.hashes: set[str] = set()

    def has_hash(self, file_hash: str) -> bool:
        return file_hash in self.hashes

    def save(self, parsed, source: str = "test") -> int:
        self.hashes.add(parsed.file_hash)
        self.records.append(parsed)
        return len(self.records)


def collect_log_files(paths: Iterable[str | Path]) -> list[Path]:
    """Collect all IW log .txt files from a mix of directories and files."""
    files: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_dir():
            files.extend(
                sorted(child for child in path.rglob("*.txt") if _is_iw_log(child))
            )
        elif path.is_file() and _is_iw_log(path):
            files.append(path)
    return sorted(set(files))


def _parse_one(
    path: Path, work_order_override: str | None
) -> tuple[Path, object | None, str | None]:
    """
    Parse a single log file.  Returns (path, parsed_result, error_str).
    Designed to run in a thread-pool worker.
    """
    try:
        return path, parse_iw61x_log_file(path, work_order_override), None
    except Exception as exc:
        return path, None, str(exc)


def ingest_paths(
    paths: Iterable[str | Path],
    repo: RunRepository,
    work_order_override: str | None = None,
    source: str = "api",
    progress_callback: Callable[[int, int, FileIngestionResult], None] | None = None,
) -> BatchReport:
    """
    Parse + ingest a list of log files into *repo*.

    Performance strategy
    --------------------
    1. Pre-load all known hashes from DB into an in-memory set (one query).
    2. Parse files in parallel using a thread-pool (CPU-bound parsing + file I/O).
    3. Commit to DB every ``_COMMIT_EVERY`` uploaded files to keep
       transaction sizes small (mirrors the reference uploader's behaviour).
    4. Progress callback is invoked once per file (not per measurement row).
    """
    log_files = collect_log_files(paths)
    report = BatchReport(total_files=len(log_files))

    if not log_files:
        return report

    # Pre-load hash cache if the repo supports it (PostgresRunRepository does)
    if hasattr(repo, "preload_hashes"):
        repo.preload_hashes()

    # --- Parallel parse --------------------------------------------------
    # We submit all files at once; results arrive in completion order.
    # DB writes stay sequential (psycopg2 connections are not thread-safe).
    parsed_map: dict[Path, tuple[object | None, str | None]] = {}
    with ThreadPoolExecutor(max_workers=_PARSE_WORKERS) as pool:
        futures = {
            pool.submit(_parse_one, path, work_order_override): path
            for path in log_files
        }
        for future in as_completed(futures):
            path, parsed, err = future.result()
            parsed_map[path] = (parsed, err)

    # --- Sequential DB write (order-preserving) -------------------------
    uploads_since_commit = 0
    for completed, path in enumerate(log_files, start=1):
        parsed, err = parsed_map[path]

        if err is not None or parsed is None:
            item = FileIngestionResult(str(path), "rejected", error=err or "unknown error")
            report.rejected += 1
        else:
            warning_text = [issue.description for issue in parsed.issues]
            if repo.has_hash(parsed.file_hash):
                item = FileIngestionResult(
                    str(path), "duplicate", parsed.result, parsed.work_order, warning_text
                )
                report.duplicates += 1
            else:
                try:
                    repo.save(parsed, source=source)
                    item = FileIngestionResult(
                        str(path), "uploaded", parsed.result, parsed.work_order, warning_text
                    )
                    report.uploaded += 1
                    report.warnings += len(warning_text)
                    uploads_since_commit += 1

                    # Periodic commit — keeps transactions small
                    if uploads_since_commit >= _COMMIT_EVERY and hasattr(repo, "conn"):
                        repo.conn.commit()
                        uploads_since_commit = 0

                except Exception as exc:
                    item = FileIngestionResult(str(path), "rejected", error=str(exc))
                    report.rejected += 1

        report.files.append(item)
        if progress_callback:
            progress_callback(completed, report.total_files, item)

    return report


def _is_iw_log(path: Path) -> bool:
    parts = path.stem.split("_")
    return (
        len(parts) >= 5
        and len(parts[0]) == 8
        and parts[0].isdigit()
        and parts[-1].upper() in {"PASS", "FAIL", "STOP"}
    )
