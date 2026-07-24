from __future__ import annotations

import tempfile
import time
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, Body, Header, HTTPException, Query
from fastapi.responses import PlainTextResponse

from admin_routes import ADMIN_PASS, ADMIN_USER, TOKEN_TTL_SEC, _require_token, _sign
from db import get_connection
from parsers.iw61x import parse_iw61x_log_file
from query_service import fetch_filter_options, fetch_records


router = APIRouter(prefix="/api", tags=["reference-dashboard"])


def _where(
    work_order: str | None = None,
    year: int | None = None,
    month: int | None = None,
    week: int | None = None,
    day: str | None = None,
    alias: str = "",
) -> tuple[str, list]:
    column = lambda name: f"{alias}.{name}" if alias else name
    clauses: list[str] = []
    params: list = []
    if work_order:
        clauses.append(f"{column('work_order')} = %s")
        params.append(work_order)
    if year is not None:
        clauses.append(f"EXTRACT(YEAR FROM {column('start_time')}) = %s")
        params.append(year)
    if month is not None:
        clauses.append(f"EXTRACT(MONTH FROM {column('start_time')}) = %s")
        params.append(month)
    if week is not None:
        clauses.append(f"EXTRACT(WEEK FROM {column('start_time')}) = %s")
        params.append(week)
    if day:
        clauses.append(f"DATE({column('start_time')}) = %s")
        params.append(day)
    return ("WHERE " + " AND ".join(clauses) if clauses else ""), params


def _token(value: str | None) -> None:
    _require_token(value)


@router.get("/pass-fail-split")
def pass_fail_split(
    work_order: str | None = None,
    year: int | None = None,
    month: int | None = None,
    week: int | None = None,
    day: str | None = None,
):
    with get_connection() as conn:
        rows = fetch_records(conn, work_order=work_order, year=year, month=month, week=week, day=day)
    grouped: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in rows:
        if row.mac1 and row.mac1_source != "unknown":
            grouped[(row.work_order, row.mac1)].add(row.result)
    counts = {"PASS": 0, "FAIL": 0, "STOP": 0}
    for results in grouped.values():
        result = "PASS" if "PASS" in results else "FAIL" if "FAIL" in results else "STOP"
        counts[result] += 1
    return [{"result": result, "count": counts[result]} for result in ("PASS", "FAIL", "STOP")]


@router.get("/monthly-count")
def monthly_count(year: int | None = None, work_order: str | None = None):
    target_year = year
    with get_connection() as conn:
        with conn.cursor() as cur:
            if target_year is None:
                cur.execute("SELECT MAX(EXTRACT(YEAR FROM start_time))::int FROM test_results")
                target_year = cur.fetchone()[0]
            if target_year is None:
                return {"year": None, "months": []}
            params: list = [target_year]
            work_clause = ""
            if work_order:
                work_clause = "AND work_order = %s"
                params.append(work_order)
            cur.execute(
                f"""
                WITH units AS (
                    SELECT DISTINCT ON (work_order, mac1, EXTRACT(MONTH FROM start_time))
                           EXTRACT(MONTH FROM start_time)::int AS month, work_order, mac1, result
                    FROM test_results
                    WHERE EXTRACT(YEAR FROM start_time) = %s
                      AND mac1 IS NOT NULL AND mac1_source != 'unknown'
                      {work_clause}
                    ORDER BY work_order, mac1, EXTRACT(MONTH FROM start_time),
                             CASE WHEN result = 'PASS' THEN 0 ELSE 1 END,
                             start_time DESC
                )
                SELECT month, COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE result = 'PASS') AS passed,
                       COUNT(*) FILTER (WHERE result = 'FAIL') AS failed,
                       COUNT(*) FILTER (WHERE result = 'STOP') AS stopped
                FROM units GROUP BY month ORDER BY month
                """,
                params,
            )
            rows = [
                {"month": row[0], "total": row[1], "passed": row[2], "failed": row[3], "stopped": row[4]}
                for row in cur.fetchall()
            ]
    month_map = {row["month"]: row for row in rows}
    return {
        "year": target_year,
        "months": [
            month_map.get(month, {"month": month, "total": 0, "passed": 0, "failed": 0, "stopped": 0})
            for month in range(1, 13)
        ],
    }


@router.get("/config-analysis")
def config_analysis(work_order: str | None = None):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT result, COUNT(*)
                FROM test_results
                WHERE (%s IS NULL OR work_order = %s)
                  AND result IN ('FAIL', 'STOP')
                  AND (fail_category = 'DeviceConfigure' OR fail_step_name IS NULL)
                GROUP BY result ORDER BY COUNT(*) DESC
                """,
                (work_order, work_order),
            )
            return [{"result": row[0], "count": row[1]} for row in cur.fetchall()]


@router.get("/fails")
def fails(
    work_order: str | None = None,
    year: int | None = None,
    month: int | None = None,
    week: int | None = None,
    day: str | None = None,
    limit: int = Query(50, ge=1, le=5000),
):
    with get_connection() as conn:
        rows = fetch_records(
            conn,
            work_order=work_order,
            year=year,
            month=month,
            week=week,
            day=day,
            limit=10000,
        )
    return [_public_row(row) for row in rows if row.result in {"FAIL", "STOP"}][:limit]


@router.get("/test-duration")
def test_duration(
    work_order: str | None = None,
    year: int | None = None,
    month: int | None = None,
    week: int | None = None,
    day: str | None = None,
):
    with get_connection() as conn:
        rows = fetch_records(conn, work_order=work_order, year=year, month=month, week=week, day=day)
    return [
        {"test_duration_sec": row.test_duration_sec, "result": row.result}
        for row in sorted(rows, key=lambda item: item.start_time)
        if row.test_duration_sec is not None
    ]


@router.get("/mac-range")
def mac_range(work_order: str | None = None):
    with get_connection() as conn:
        rows = fetch_records(conn, work_order=work_order)
    grouped: dict[str, list] = defaultdict(list)
    for row in rows:
        if row.mac1 and row.mac1_source != "unknown":
            grouped[row.mac1].append(row)
    incidents = []
    for mac, items in sorted(grouped.items()):
        results = {item.result for item in items}
        if "PASS" not in results and results:
            incidents.append(
                {
                    "mac1": mac,
                    "work_order": items[0].work_order,
                    "fail_count": sum(item.result == "FAIL" for item in items),
                    "stop_count": sum(item.result == "STOP" for item in items),
                    "last_attempt": max(item.start_time for item in items),
                    "true_status": "FAIL" if "FAIL" in results else "STOP",
                }
            )
    macs = sorted(grouped)
    return {
        "mac1_min": macs[0] if macs else None,
        "mac1_max": macs[-1] if macs else None,
        "unique_mac1": len(macs),
        "pass_mac1": sum(any(item.result == "PASS" for item in items) for items in grouped.values()),
        "true_fail_mac1": sum(row["true_status"] == "FAIL" for row in incidents),
        "true_stop_mac1": sum(row["true_status"] == "STOP" for row in incidents),
        "true_incidents": incidents,
    }


@router.get("/bt-metrics")
def bt_metrics(
    work_order: str | None = None,
    year: int | None = None,
    month: int | None = None,
    week: int | None = None,
    day: str | None = None,
):
    filter_where, params = _where(work_order, year, month, week, day, alias="tr")
    extra = filter_where.replace("WHERE", "AND", 1) if filter_where else ""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT tm.standard,
                       COUNT(DISTINCT tm.test_result_id) FILTER (WHERE tm.passed IS DISTINCT FROM FALSE) AS passed,
                       COUNT(DISTINCT tm.test_result_id) FILTER (WHERE tm.passed = FALSE) AS failed
                FROM test_measurements tm
                JOIN test_results tr ON tr.id = tm.test_result_id
                WHERE tm.technology = 'BT' AND tm.standard IN ('BDR', 'LE')
                {extra}
                GROUP BY tm.standard
                """,
                params,
            )
            pass_rows = {row[0]: {"passed": row[1], "failed": row[2]} for row in cur.fetchall()}
            cur.execute(
                f"""
                SELECT tm.metric_name, tm.standard, tm.frequency_mhz, AVG(tm.value)
                FROM test_measurements tm
                JOIN test_results tr ON tr.id = tm.test_result_id
                WHERE tm.technology = 'BT'
                  AND tm.metric_name IN ('BER', 'PER')
                  {extra}
                GROUP BY tm.metric_name, tm.standard, tm.frequency_mhz
                """,
                params,
            )
            rx = {(row[0], row[1], row[2]): float(row[3]) for row in cur.fetchall() if row[3] is not None}
            cur.execute(
                f"""
                SELECT tm.standard, tm.metric_name, tm.value
                FROM test_measurements tm
                JOIN test_results tr ON tr.id = tm.test_result_id
                WHERE tm.technology = 'BT'
                  AND ((tm.standard = 'BDR' AND tm.metric_name = 'POWER_AVERAGE_DBM')
                    OR (tm.standard = 'EDR' AND tm.metric_name IN ('EDR_EVM_AV', 'EDR_EVM_PK')))
                  AND tm.value IS NOT NULL
                  {extra}
                ORDER BY tm.id
                """,
                params,
            )
            values: dict[tuple[str, str], list[float]] = defaultdict(list)
            for standard, metric, value in cur.fetchall():
                values[(standard, metric)].append(value)
    return {
        "bt_summary": {
            "bdr_passed": pass_rows.get("BDR", {}).get("passed", 0),
            "bdr_failed": pass_rows.get("BDR", {}).get("failed", 0),
            "le_passed": pass_rows.get("LE", {}).get("passed", 0),
            "le_failed": pass_rows.get("LE", {}).get("failed", 0),
            "avg_ber_2441": rx.get(("BER", "BDR", 2441)),
            "avg_ber_2480": rx.get(("BER", "BDR", 2480)),
            "avg_per_le": _avg(value for (metric, standard, _), value in rx.items() if metric == "PER" and standard == "LE"),
        },
        "bdr_powers": values[("BDR", "POWER_AVERAGE_DBM")],
        "edr1_devm": values[("EDR", "EDR_EVM_AV")],
        "edr2_devm": values[("EDR", "EDR_EVM_PK")],
    }


@router.get("/wifi-metrics")
def wifi_metrics(
    work_order: str | None = None,
    year: int | None = None,
    month: int | None = None,
    week: int | None = None,
    day: str | None = None,
):
    filter_where, params = _where(work_order, year, month, week, day, alias="tr")
    extra = filter_where.replace("WHERE", "AND", 1) if filter_where else ""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT tm.band, tm.standard, tm.bandwidth, tm.rate, AVG(tm.value)
                FROM test_measurements tm
                JOIN test_results tr ON tr.id = tm.test_result_id
                WHERE tm.technology = 'WIFI' AND tm.direction = 'TX'
                  AND tm.metric_name IN ('EVM_DB_ALL', 'EVM_DB_AVG_S1', 'EVM_DB_MAX_S1')
                  AND tm.value IS NOT NULL
                  {extra}
                GROUP BY tm.band, tm.standard, tm.bandwidth, tm.rate
                """,
                params,
            )
            evm = {(row[0], row[1], row[2], row[3]): float(row[4]) for row in cur.fetchall()}
            cur.execute(
                f"""
                SELECT tr.id, tr.start_time,
                       MAX(tm.value) FILTER (WHERE tm.technology = 'CALIBRATION' AND tm.metric_name = 'XTAL_REG') AS xtal_cap,
                       AVG(tm.value) FILTER (WHERE tm.technology = 'WIFI' AND tm.metric_name = 'FREQ_ERROR_AVG') AS ppm
                FROM test_results tr
                JOIN test_measurements tm ON tm.test_result_id = tr.id
                {filter_where}
                GROUP BY tr.id, tr.start_time
                HAVING MAX(tm.value) FILTER (WHERE tm.technology = 'CALIBRATION' AND tm.metric_name = 'XTAL_REG') IS NOT NULL
                ORDER BY tr.start_time
                """,
                params,
            )
            xtal = [
                {"start_time": row[1], "xtal_cap": row[2], "xtal_freq_error_ppm": float(row[3]) if row[3] is not None else None}
                for row in cur.fetchall()
            ]
    pick = lambda band, standard, bandwidth, rate: evm.get((band, standard, bandwidth, rate))
    return {
        "avg_evm_24g": {
            "cck11": pick("2.4GHz", "11n", "BW20", "CCK11"),
            "ofdm54": pick("2.4GHz", "11n", "BW20", "OFDM54"),
            "ht20": pick("2.4GHz", "11n", "BW20", "MCS7"),
            "he20": pick("2.4GHz", "11ax", "BW20", "MCS7"),
        },
        "avg_evm_5g": {
            "ofdm54": pick("5GHz", "11n", "BW20", "OFDM54"),
            "ht20": pick("5GHz", "11n", "BW20", "MCS7"),
            "vht80": pick("5GHz", "11ac", "BW80", "MCS9"),
            "he20": pick("5GHz", "11ax", "BW20", "MCS7"),
        },
        "xtal_data": xtal,
    }


@router.get("/calibration")
def calibration(
    work_order: str | None = None,
    year: int | None = None,
    month: int | None = None,
    week: int | None = None,
    day: str | None = None,
):
    return wifi_metrics(work_order, year, month, week, day)["xtal_data"]


@router.get("/alignment-targets")
def alignment_targets():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT work_order, target_total FROM alignment_targets ORDER BY work_order")
            return {row[0]: row[1] for row in cur.fetchall()}


@router.post("/alignment-targets")
def set_alignment_targets(payload: dict = Body(...)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            for work_order, target in payload.items():
                if target is None or target == "":
                    cur.execute("DELETE FROM alignment_targets WHERE work_order = %s", (work_order,))
                else:
                    cur.execute(
                        """
                        INSERT INTO alignment_targets (work_order, target_total, updated_at)
                        VALUES (%s, %s, NOW())
                        ON CONFLICT (work_order) DO UPDATE
                        SET target_total = EXCLUDED.target_total, updated_at = NOW()
                        """,
                        (work_order, max(0, int(target))),
                    )
        conn.commit()
    return {"status": "ok"}


@router.post("/tweak/login")
def tweak_login(payload: dict = Body(...)):
    if payload.get("username") != ADMIN_USER or payload.get("password") != ADMIN_PASS:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    expires_at = int(time.time()) + TOKEN_TTL_SEC
    return {"ok": True, "token": _sign({"sub": ADMIN_USER, "exp": expires_at}), "expires_at": expires_at}


@router.get("/tweak/filter-options")
def tweak_filter_options(
    work_order: str | None = None,
    year: int | None = None,
    month: int | None = None,
    week: int | None = None,
    day: str | None = None,
    x_tweak_token: str | None = Header(default=None),
):
    _token(x_tweak_token)
    with get_connection() as conn:
        return fetch_filter_options(conn, work_order, year, month, week, day)


@router.get("/tweak/records")
def tweak_records(
    work_order: str | None = None,
    year: int | None = None,
    month: int | None = None,
    week: int | None = None,
    day: str | None = None,
    page: int = Query(1, ge=1),
    page_size: str = "50",
    x_tweak_token: str | None = Header(default=None),
):
    _token(x_tweak_token)
    where, params = _where(work_order, year, month, week, day)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM test_results {where}", params)
            total = cur.fetchone()[0]
            size = None if page_size.lower() == "all" else max(1, min(int(page_size), 500))
            total_pages = 1 if size is None and total else ((total + size - 1) // size if total else 0)
            current_page = min(page, max(total_pages, 1))
            paging = ""
            query_params = list(params)
            if size is not None:
                paging = "LIMIT %s OFFSET %s"
                query_params.extend([size, (current_page - 1) * size])
            cur.execute(
                f"""
                SELECT id, mac1, mac2, work_order, DATE(start_time) AS unit_date,
                       start_time, end_time, test_duration_sec, result, source_file,
                       fail_step_name, fail_message, (raw_log IS NOT NULL AND raw_log != '') AS has_raw_log
                FROM test_results {where}
                ORDER BY start_time DESC, id DESC {paging}
                """,
                query_params,
            )
            columns = [desc[0] for desc in cur.description]
            items = [dict(zip(columns, row)) for row in cur.fetchall()]
    return {
        "items": items,
        "total": total,
        "page": current_page,
        "page_size": "All" if size is None else size,
        "total_pages": total_pages,
        "has_prev": current_page > 1,
        "has_next": total_pages > 0 and current_page < total_pages,
    }


@router.delete("/tweak/delete-scope")
def tweak_delete_scope(
    work_order: str | None = None,
    year: int | None = None,
    month: int | None = None,
    week: int | None = None,
    day: str | None = None,
    x_tweak_token: str | None = Header(default=None),
):
    _token(x_tweak_token)
    if not any((work_order, year, month, week, day)):
        raise HTTPException(status_code=400, detail="At least one filter is required")
    where, params = _where(work_order, year, month, week, day)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM test_results {where}", params)
            deleted = cur.rowcount
        conn.commit()
    return {"deleted": deleted}


@router.delete("/tweak/records/{record_id}")
def tweak_delete_record(record_id: int, x_tweak_token: str | None = Header(default=None)):
    _token(x_tweak_token)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM test_results WHERE id = %s", (record_id,))
            deleted = cur.rowcount
        conn.commit()
    return {"deleted": deleted, "id": record_id}


@router.get("/tweak/raw-log/{record_id}")
def tweak_raw_log(
    record_id: int,
    token: str | None = None,
    x_tweak_token: str | None = Header(default=None),
):
    _token(x_tweak_token or token)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT raw_log, source_file FROM test_results WHERE id = %s", (record_id,))
            row = cur.fetchone()
    if not row or not row[0]:
        raise HTTPException(status_code=404, detail="Raw log not available")
    return PlainTextResponse(row[0], headers={"Content-Disposition": f'attachment; filename="{row[1] or f"record_{record_id}.txt"}"'})


@router.post("/reparse")
def reparse(x_tweak_token: str | None = Header(default=None)):
    _token(x_tweak_token)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, work_order, raw_log, source_file FROM test_results WHERE raw_log IS NOT NULL AND raw_log != ''")
            rows = cur.fetchall()
        updated = 0
        for record_id, work_order, raw_log, source_file in rows:
            original_name = Path(source_file or "").name
            if not original_name:
                continue
            with tempfile.TemporaryDirectory(prefix="iw-reparse-") as temp_dir:
                path = Path(temp_dir) / original_name
                path.write_text(raw_log, encoding="utf-8")
                parsed = parse_iw61x_log_file(path, work_order_override=work_order)
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE test_results
                        SET fail_step_num = %s, fail_step_name = %s,
                            fail_message = %s, fail_category = %s
                        WHERE id = %s
                        """,
                        (parsed.fail_step_num, parsed.fail_step_name, parsed.fail_message, parsed.fail_category, record_id),
                    )
                    updated += cur.rowcount
        conn.commit()
    return {"updated": updated, "total": len(rows)}


def _public_row(row) -> dict:
    return {
        "id": row.id,
        "work_order": row.work_order,
        "mac1": row.mac1,
        "mac2": row.mac2,
        "unit_date": row.start_time.date() if row.start_time else None,
        "start_time": row.start_time,
        "test_duration_sec": row.test_duration_sec,
        "result": row.result,
        "fail_step_num": row.fail_step_num,
        "fail_step_name": row.fail_step_name,
        "fail_step": row.fail_step_name,
        "fail_message": row.fail_message,
        "fail_error_code": None,
        "fail_category": row.fail_category,
        "source_file": row.source_file,
    }


def _avg(values) -> float | None:
    rows = [value for value in values if value is not None]
    return sum(rows) / len(rows) if rows else None
