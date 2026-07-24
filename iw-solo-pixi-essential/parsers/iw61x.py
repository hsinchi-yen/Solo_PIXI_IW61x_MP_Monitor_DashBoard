import os
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from .base import (
    DataQualityWarning,
    ParsedFailure,
    ParsedIdentity,
    ParsedMeasurement,
    ParsedResult,
)
from .common import compute_file_hash, resolve_work_order, parse_filename

STEP_RE = re.compile(r"^(\d+)\.([A-Z0-9_]+)(.*?)_{5,}\s*$")
MEAS_RE = re.compile(
    r"^([A-Z0-9_./+\-]+)\s*:\s*([+-]?\d+(?:\.\d+)?)\s*([A-Za-z%/._\-]+)?\s*\(\s*([^,)]*)\s*,\s*([^)]*)\)",
    re.MULTILINE,
)
SUMMARY_FAIL_RE = re.compile(
    r"^\s*(\d+)\.([A-Z0-9_]+).*?---\s*\[Failed\]",
    re.MULTILINE,
)


def parse_iw61x_log_file(filepath: str | os.PathLike, work_order_override: str | None = None) -> ParsedResult:
    path = Path(filepath)
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        content = handle.read()

    file_hash = compute_file_hash(content)
    filename = path.name
    folder_path = str(path.parent)
    file_info = parse_filename(filename)

    package_name = _header_value(content, "Package Name")
    flow_file = _header_value(content, "Flow File")
    start_time = _parse_timestamp(_header_value(content, "Timestamp"))
    body_mac1 = _last_match(content, r"Generetaed MAC Address:\s*([0-9A-Fa-f]{12})")
    body_mac2 = _last_match(content, r"Generetaed BD Address:\s*([0-9A-Fa-f]{12})")
    tester_sn = _first_value(content, "IQTESTER_INFO") or _first_value(content, "TESTER_SN")
    fw_version = _first_value(content, "FW_VERSION") or _first_value(content, "VDUT_VERSION")

    is_pass = "*  P A S S  *" in content
    is_fail = "*  F A I L  *" in content
    if is_pass:
        final_result = "PASS"
    elif is_fail:
        final_result = "FAIL"
    elif file_info["result"] == "STOP":
        final_result = "STOP"
    else:
        final_result = file_info["result"]

    warnings = []
    mac1 = _normalize_mac(file_info["mac1"] or body_mac1)
    mac2 = _normalize_mac(file_info["mac2"] or body_mac2)
    mac1_source = "filename" if file_info["mac1"] else "log_body"

    if not mac1 or file_info["mac1"] is None:
        mac1 = None
        mac2 = None if file_info["mac2"] is None else mac2
        mac1_source = "unknown"
        warnings.append(DataQualityWarning("unknown_mac", "MAC address is missing or UNKNOWN in filename/log body"))
    elif body_mac1 and mac1.upper() != body_mac1.upper():
        warnings.append(DataQualityWarning("mac_conflict", f"Filename MAC {mac1} differs from body MAC {body_mac1}"))

    if "Test item function retry" in content:
        retry_count = len(re.findall(r"Test item function retry #\d+", content))
        warnings.append(DataQualityWarning("retry", f"Log contains {retry_count} retry marker(s)"))

    failure = _parse_failure(content, final_result)
    duration = _parse_duration(content)
    product = _infer_product(flow_file, package_name)

    identity = ParsedIdentity(
        mac1=mac1,
        mac2=mac2,
        mac1_source=mac1_source,
        start_time=start_time,
        end_time=None,
        test_duration_sec=duration,
        product=product,
        flow_file=flow_file,
        package_name=package_name,
        tester_sn=tester_sn,
        fw_version=fw_version
    )

    work_order = resolve_work_order(work_order_override or file_info.get("work_order"), filename, folder_path)

    return ParsedResult(
        identity=identity,
        result=final_result,
        work_order=work_order,
        failure=failure,
        measurements=_parse_measurements(content),
        warnings=warnings,
        raw_log=content,
        file_hash=file_hash,
        source_file=str(path)
    )


def parse_log_file(filepath: str | os.PathLike, work_order_override: str | None = None) -> ParsedResult:
    return parse_iw61x_log_file(filepath, work_order_override)


def audit_folder(folder: str | os.PathLike, work_order_override: str | None = None) -> dict:
    root = Path(folder)
    results = Counter()
    work_orders = Counter()
    errors = []
    unknown = 0
    total_measurements = 0
    for path in sorted(root.glob("*.txt")):
        if not _is_legacy_log_name(path.name):
            continue
        try:
            parsed = parse_iw61x_log_file(path, work_order_override)
            results[parsed.result] += 1
            work_orders[parsed.work_order] += 1
            total_measurements += len(parsed.measurements)
            if parsed.mac1_source == "unknown":
                unknown += 1
        except Exception as exc:
            errors.append({"file": str(path), "error": str(exc)})

    return {
        "total_files": sum(results.values()) + len(errors),
        "results": {key: results.get(key, 0) for key in ("PASS", "FAIL", "STOP")},
        "unknown_mac_files": unknown,
        "work_orders": dict(work_orders),
        "total_measurements": total_measurements,
        "errors": errors,
    }


def _header_value(content: str, label: str) -> str | None:
    match = re.search(rf"========\s+{re.escape(label)}:\s*(.+)", content)
    return match.group(1).strip() if match else None


def _is_legacy_log_name(name: str) -> bool:
    return re.match(r"^\d{8}_\d{6}_.+_.+_(PASS|FAIL|STOP)\.txt$", name, re.IGNORECASE) is not None


def _first_value(content: str, key: str) -> str | None:
    match = re.search(rf"^{re.escape(key)}\s*:\s*(.+?)\s*$", content, re.MULTILINE)
    return match.group(1).strip() if match else None


def _last_match(content: str, pattern: str) -> str | None:
    matches = re.findall(pattern, content)
    return matches[-1].strip() if matches else None


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d_%H:%M:%S")
    except ValueError:
        return None


def _normalize_mac(value: str | None) -> str | None:
    if not value:
        return None
    compact = re.sub(r"[^0-9A-Fa-f]", "", value).upper()
    if compact in {"", "UNKNOWNMAC1", "UNKNOWNMAC2"}:
        return None
    return compact if len(compact) == 12 else None


def _infer_product(flow_file: str | None, package_name: str | None) -> str:
    haystack = f"{flow_file or ''} {package_name or ''}".upper()
    if "IW611" in haystack:
        return "IW611"
    if "W61X" in haystack or "IW61" in haystack:
        return "IW61x"
    return "IW611"


def _parse_duration(content: str) -> float | None:
    match = re.search(r"Average Flow Run Time\s*:\s*([0-9.]+)\s*s", content)
    if not match:
        match = re.search(r"Flow Run Time\s*:\s*([0-9.]+)\s*s", content)
    return float(match.group(1)) if match else None


def _parse_failure(content: str, result: str) -> ParsedFailure | None:
    if result != "FAIL":
        return None
    matches = SUMMARY_FAIL_RE.findall(content)
    if not matches:
        return ParsedFailure(None, "Unknown", _first_error_message(content), "TestFail")
    step_num, step_name = matches[-1]
    return ParsedFailure(int(step_num), step_name, _first_error_message(content), "TestFail")


def _first_error_message(content: str) -> str | None:
    messages = re.findall(r"ERROR_MESSAGE\s*:\s*(.+)", content)
    for message in reversed(messages):
        text = message.strip()
        if text and "[Info]" not in text:
            return text
    return "test function error"


def _parse_measurements(content: str) -> list[ParsedMeasurement]:
    measurements: list[ParsedMeasurement] = []
    current = None
    for line in content.splitlines():
        step_match = STEP_RE.match(line)
        if step_match:
            current = _step_context(step_match)
            continue
        if current is None:
            continue
        metric_match = MEAS_RE.match(line)
        if not metric_match:
            continue
        name, value_text, unit, low_text, high_text = metric_match.groups()
        value = float(value_text)
        low = _parse_limit(low_text)
        high = _parse_limit(high_text)
        passed = _passes_limits(value, low, high)
        measurements.append(
            ParsedMeasurement(
                technology=current["technology"],
                direction=current["direction"],
                standard=current["standard"],
                band=current["band"],
                frequency_mhz=current["frequency_mhz"],
                bandwidth=current["bandwidth"],
                rate=current["rate"],
                antenna=current["antenna"],
                step_num=current["step_num"],
                step_name=current["step_name"],
                metric_name=name.strip(),
                value=value,
                unit=unit.strip() if unit else None,
                limit_low=low,
                limit_high=high,
                passed=passed,
            )
        )
    return measurements


def _step_context(step_match: re.Match) -> dict:
    step_num = int(step_match.group(1))
    step_name = step_match.group(2)
    raw_tail = " ".join(step_match.group(3).split())
    tail = raw_tail.replace("_", " ")
    tokens = tail.split()
    frequency = next((int(token) for token in tokens if token.isdigit() and 2000 <= int(token) <= 6000), None)
    band = "2.4GHz" if frequency and frequency < 3000 else ("5GHz" if frequency else None)
    bandwidth = _find_bandwidth(tokens)
    rate = _find_rate(tokens)
    antenna = next((token for token in tokens if token.startswith("ANT") or token.startswith("TX")), None)

    technology = "OTHER"
    direction = None
    standard = None
    if step_name.startswith("TEST_VERIFY"):
        technology = "WIFI"
        direction = "RX" if " PER " in f" {tail} " else "TX"
        standard = _wifi_standard(tokens, frequency, raw_tail)
    elif step_name.startswith("TX_"):
        technology = "BT"
        direction = "TX"
        standard = _bt_standard(step_name)
    elif step_name.startswith("RX_"):
        technology = "BT"
        direction = "RX"
        standard = _bt_standard(step_name)
    elif step_name in {"RF_POWER_CAL", "NXP_XTAL_CAL"}:
        technology = "CALIBRATION"
        direction = "CAL"
        standard = _wifi_standard(tokens, frequency, raw_tail)

    return {
        "step_num": step_num,
        "step_name": step_name,
        "technology": technology,
        "direction": direction,
        "standard": standard,
        "band": band,
        "frequency_mhz": frequency,
        "bandwidth": bandwidth,
        "rate": rate,
        "antenna": antenna,
    }


def _find_bandwidth(tokens: list[str]) -> str | None:
    for index, token in enumerate(tokens):
        if token.startswith("BW-"):
            return token.replace("-", "")
        if token == "BW" and index + 1 < len(tokens):
            return f"BW{tokens[index + 1]}"
    return None


def _find_rate(tokens: list[str]) -> str | None:
    for token in tokens:
        if token.startswith(("CCK-", "OFDM-", "MCS")) or re.match(r"\d(DH|LE)", token):
            return token.replace("-", "")
        if token.endswith("KLE"):
            return token
    return None


def _wifi_standard(tokens: list[str], frequency: int | None, raw_tail: str) -> str | None:
    joined = " ".join(tokens)
    if "HE_SU" in raw_tail or "HE SU" in joined:
        return "11ax"
    if "VHT" in tokens:
        return "11ac"
    if "HT" in joined:
        return "11n"
    if any(token.startswith("CCK") for token in tokens):
        return "11b"
    if any(token.startswith("OFDM") for token in tokens):
        return "11g" if frequency and frequency < 3000 else "11a"
    return None


def _bt_standard(step_name: str) -> str | None:
    if "BDR" in step_name:
        return "BDR"
    if "EDR" in step_name:
        return "EDR"
    if "LE" in step_name:
        return "LE"
    return None


def _parse_limit(value: str) -> float | None:
    text = value.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _passes_limits(value: float, low: float | None, high: float | None) -> bool | None:
    if low is None and high is None:
        return None
    if low is not None and value < low:
        return False
    if high is not None and value > high:
        return False
    return True
