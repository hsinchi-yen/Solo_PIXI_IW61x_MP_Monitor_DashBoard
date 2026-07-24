from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Protocol

from parsers.iw61x import parse_iw61x_log_file


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
            "files": [file.__dict__ for file in self.files],
        }


class RunRepository(Protocol):
    def has_hash(self, file_hash: str) -> bool:
        ...

    def save(self, parsed, source: str = "api") -> int:
        ...


class InMemoryRunRepository:
    def __init__(self):
        self.records = []
        self.hashes = set()

    def has_hash(self, file_hash: str) -> bool:
        return file_hash in self.hashes

    def save(self, parsed, source: str = "test") -> int:
        self.hashes.add(parsed.file_hash)
        self.records.append(parsed)
        return len(self.records)


def collect_log_files(paths: Iterable[str | Path]) -> list[Path]:
    files: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_dir():
            files.extend(sorted(child for child in path.glob("*.txt") if _is_iw_log(child)))
        elif path.is_file() and _is_iw_log(path):
            files.append(path)
    return sorted(files)


def ingest_paths(
    paths: Iterable[str | Path],
    repo: RunRepository,
    work_order_override: str | None = None,
    source: str = "api",
) -> BatchReport:
    report = BatchReport()
    for path in collect_log_files(paths):
        report.total_files += 1
        try:
            parsed = parse_iw61x_log_file(path, work_order_override)
            warning_text = [issue.description for issue in parsed.issues]
            if repo.has_hash(parsed.file_hash):
                report.duplicates += 1
                report.files.append(
                    FileIngestionResult(str(path), "duplicate", parsed.result, parsed.work_order, warning_text)
                )
                continue
            repo.save(parsed, source=source)
            report.uploaded += 1
            report.warnings += len(warning_text)
            report.files.append(
                FileIngestionResult(str(path), "uploaded", parsed.result, parsed.work_order, warning_text)
            )
        except Exception as exc:
            report.rejected += 1
            report.files.append(FileIngestionResult(str(path), "rejected", error=str(exc)))
    return report


def _is_iw_log(path: Path) -> bool:
    parts = path.stem.split("_")
    return (
        len(parts) >= 5
        and len(parts[0]) == 8
        and parts[0].isdigit()
        and parts[-1].upper() in {"PASS", "FAIL", "STOP"}
    )
