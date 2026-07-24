"""Pure parsing and filesystem operations for the IW611 splitter utility."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import os
import re
from typing import Callable, Iterable


RUN_HEADER_PATTERN = re.compile(
    r"(?m)^\ufeff?={90,}\r?\n"
    r"=  Running IQfact\+ component[^\r\n]*\r?\n"
)
TIMESTAMP_PATTERN = re.compile(
    r"(?im)^=+\s*Timestamp:\s*"
    r"(\d{4})-(\d{2})-(\d{2})_(\d{2}):(\d{2}):(\d{2})\s*$"
)
GENERATED_MAC_PATTERN = re.compile(
    r"(?im)^(?:Generetaed|Generated)\s+MAC Address:\s*"
    r"([0-9A-Fa-f:.-]{12,20})\s*$"
)
MAC_ADDRESS_PATTERN = re.compile(
    r"(?im)^MAC_ADDRESS\s*:\s*([0-9A-Fa-f:.-]{12,20})\s*$"
)
BD_ADDRESS_PATTERN = re.compile(
    r"(?im)^BD_ADDRESS\s*:\s*([0-9A-Fa-f:.-]{12,20})\s*$"
)
PASSED_RUN_PATTERN = re.compile(
    r"(?im)^Passed Run\(s\)\s*:\s*(\d+)\s*$"
)
FAILED_RUN_PATTERN = re.compile(
    r"(?im)^Failed Run\(s\) on (?:Limits|Errors)\s*:\s*(\d+)\s*$"
)
FINAL_FLOW_ERROR_PATTERN = re.compile(
    r"(?im)^IQfactRun_Console:.*FLOW RUNNING ERROR.*\[FAIL\]\s*$"
)
OUTPUT_FILENAME_PATTERN = re.compile(
    r"^\d{8}_\d{6}_[^_]+_[^_]+_(PASS|FAIL|STOP)"
    r"(?:_\d+)?\.txt$",
    re.IGNORECASE,
)

ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class ParsedRun:
    date: str
    time: str
    mac1: str
    mac2: str
    result: str
    content: str


@dataclass
class SplitReport:
    total: int = 0
    created: int = 0
    skipped: int = 0
    pass_count: int = 0
    fail_count: int = 0
    stop_count: int = 0
    output_paths: list[Path] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return self.pass_count / self.total * 100 if self.total else 0.0

    @property
    def fail_rate(self) -> float:
        return self.fail_count / self.total * 100 if self.total else 0.0


def split_run_blocks(content: str) -> list[str]:
    """Return independent IQfact Run blocks, one per Timestamp header."""
    matches = list(RUN_HEADER_PATTERN.finditer(content))
    blocks: list[str] = []

    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        raw_block = content[match.start():end]
        if not TIMESTAMP_PATTERN.search(raw_block):
            continue

        newline = "\r\n" if "\r\n" in raw_block else "\n"
        blocks.append(raw_block.rstrip("\r\n") + newline)

    return blocks


def _normalize_mac(value: str) -> str | None:
    normalized = re.sub(r"[^0-9A-Fa-f]", "", value).upper()
    if len(normalized) != 12:
        return None
    if normalized in {"000000000000", "FFFFFFFFFFFF"}:
        return None
    return normalized


def _first_valid_mac(matches: Iterable[re.Match[str]]) -> str | None:
    for match in matches:
        normalized = _normalize_mac(match.group(1))
        if normalized:
            return normalized
    return None


def extract_mac_addresses(block: str) -> tuple[str | None, str | None]:
    """Extract normalized Wi-Fi MAC (MAC1) and Bluetooth address (MAC2)."""
    mac1 = _first_valid_mac(GENERATED_MAC_PATTERN.finditer(block))
    if mac1 is None:
        mac1 = _first_valid_mac(MAC_ADDRESS_PATTERN.finditer(block))

    mac2 = _first_valid_mac(BD_ADDRESS_PATTERN.finditer(block))
    return mac1, mac2


def classify_run(block: str) -> str:
    """Classify a Run from its conclusive final summary."""
    passed_values = [int(match.group(1)) for match in PASSED_RUN_PATTERN.finditer(block)]
    if passed_values and passed_values[-1] > 0:
        return "PASS"

    failed_values = [int(match.group(1)) for match in FAILED_RUN_PATTERN.finditer(block)]
    if any(value > 0 for value in failed_values[-2:]):
        return "FAIL"

    if FINAL_FLOW_ERROR_PATTERN.search(block):
        return "FAIL"

    return "STOP"


def parse_run(block: str) -> ParsedRun:
    """Parse fields required by the approved output filename."""
    timestamp = TIMESTAMP_PATTERN.search(block)
    if timestamp is None:
        raise ValueError("Run block has no valid Timestamp")

    mac1, mac2 = extract_mac_addresses(block)
    date_text = "".join(timestamp.group(1, 2, 3))
    time_text = "".join(timestamp.group(4, 5, 6))

    return ParsedRun(
        date=date_text,
        time=time_text,
        mac1=mac1 or "UNKNOWNMAC1",
        mac2=mac2 or "UNKNOWNMAC2",
        result=classify_run(block),
        content=block,
    )


def build_output_filename(parsed: ParsedRun) -> str:
    return (
        f"{parsed.date}_{parsed.time}_{parsed.mac1}_{parsed.mac2}_"
        f"{parsed.result}.txt"
    )


def ensure_unique_output_path(output_dir: os.PathLike[str] | str, filename: str) -> Path:
    directory = Path(output_dir)
    candidate = directory / filename
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    index = 1

    while candidate.exists():
        candidate = directory / f"{stem}_{index}{suffix}"
        index += 1

    return candidate


def _count_results(results: Iterable[str], report: SplitReport) -> None:
    for result in results:
        if result == "PASS":
            report.pass_count += 1
        elif result == "FAIL":
            report.fail_count += 1
        elif result == "STOP":
            report.stop_count += 1


def split_log_file(
    source_path: os.PathLike[str] | str,
    output_dir: os.PathLike[str] | str,
    progress: ProgressCallback | None = None,
) -> SplitReport:
    """Split a consolidated source log into one file per Timestamp Run."""
    source = Path(source_path)
    destination = Path(output_dir)
    if not source.is_file():
        raise FileNotFoundError(f"Source file does not exist: {source}")

    destination.mkdir(parents=True, exist_ok=True)
    if progress:
        progress(f"Reading source file: {source}")

    with source.open(
        "r",
        encoding="utf-8-sig",
        errors="replace",
        newline="",
    ) as source_file:
        content = source_file.read()
    blocks = split_run_blocks(content)
    report = SplitReport(total=len(blocks))
    parsed_results: list[str] = []

    if progress:
        progress(f"Detected Timestamp Runs: {len(blocks)}")

    for index, block in enumerate(blocks, start=1):
        try:
            parsed = parse_run(block)
            output_path = ensure_unique_output_path(
                destination,
                build_output_filename(parsed),
            )
            with output_path.open("w", encoding="utf-8", newline="") as output_file:
                output_file.write(parsed.content)
            report.output_paths.append(output_path)
            report.created += 1
            parsed_results.append(parsed.result)
        except (OSError, ValueError) as exc:
            report.skipped += 1
            if progress:
                progress(f"Skipped Run {index}: {exc}")

        if progress and (index % 10 == 0 or index == len(blocks)):
            progress(f"Progress: {index}/{len(blocks)}")

    _count_results(parsed_results, report)
    if progress:
        progress(f"Split complete: {report.created} files created in {destination}")
    return report


def summarize_output_directory(
    output_dir: os.PathLike[str] | str,
) -> SplitReport:
    """Count split files already present in an output directory."""
    directory = Path(output_dir)
    if not directory.is_dir():
        raise FileNotFoundError(f"Output directory does not exist: {directory}")

    report = SplitReport()
    results: list[str] = []

    for path in directory.iterdir():
        if not path.is_file() or path.name.lower() == "summary.txt":
            continue
        match = OUTPUT_FILENAME_PATTERN.match(path.name)
        if match:
            results.append(match.group(1).upper())
        else:
            report.skipped += 1

    report.total = len(results)
    report.created = len(results)
    _count_results(results, report)
    return report


def create_summary_file(
    output_dir: os.PathLike[str] | str,
    report: SplitReport | None = None,
) -> Path:
    """Write a human-readable summary for a split session or output folder."""
    directory = Path(output_dir)
    if not directory.is_dir():
        raise FileNotFoundError(f"Output directory does not exist: {directory}")
    current_report = report or summarize_output_directory(directory)
    summary_path = directory / "summary.txt"
    summary_text = (
        "IW611 Log Split Summary\n"
        "========================================\n"
        f"Generated at: {datetime.now():%Y-%m-%d %H:%M:%S}\n"
        f"Output directory: {directory.resolve()}\n"
        f"Total Timestamp Runs: {current_report.total}\n"
        f"Split files created: {current_report.created}\n"
        f"Skipped entries/files: {current_report.skipped}\n"
        "----------------------------------------\n"
        f"PASS count: {current_report.pass_count}\n"
        f"FAIL count: {current_report.fail_count}\n"
        f"STOP count: {current_report.stop_count}\n"
        f"Pass rate: {current_report.pass_rate:.2f}%\n"
        f"Fail rate: {current_report.fail_rate:.2f}%\n"
    )
    summary_path.write_text(summary_text, encoding="utf-8")
    return summary_path
