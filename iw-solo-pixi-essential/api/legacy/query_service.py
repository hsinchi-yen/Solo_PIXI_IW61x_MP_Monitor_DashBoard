from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from statistics import mean


def build_summary_from_records(records) -> dict:
    rows = list(records)
    attempts = Counter(row.result for row in rows)
    unknown = sum(1 for row in rows if row.mac1_source == "unknown" or not row.mac1)
    unit_rows = [row for row in rows if row.mac1 and row.mac1_source != "unknown"]
    grouped = _group_units(unit_rows)
    any_pass = _any_pass_yield(grouped)
    retry_units = sum(1 for items in grouped.values() if len(items) > 1)

    return {
        # Reference-dashboard compatibility. The reference "Raw KPI" is
        # pass-priority unique-unit yield, which maps to IW any-pass semantics.
        "total": any_pass["total"],
        "pass": any_pass["PASS"],
        "fail": any_pass["FAIL"],
        "stop": any_pass["STOP"],
        "yield_pct": any_pass["yield_pct"],
        "retry_units": retry_units,
        "retry_rate": _pct(retry_units, len(grouped)),
        "attempts": {
            "total": len(rows),
            "PASS": attempts.get("PASS", 0),
            "FAIL": attempts.get("FAIL", 0),
            "STOP": attempts.get("STOP", 0),
            "yield_pct": _pct(attempts.get("PASS", 0), len(rows)),
        },
        "unit_yield": {
            "first_pass": _yield_for([sorted(items, key=lambda row: row.start_time)[0] for items in grouped.values()]),
            "latest_result": _yield_for([sorted(items, key=lambda row: row.start_time)[-1] for items in grouped.values()]),
            "any_pass": any_pass,
        },
        "data_quality": {
            "unknown_mac_attempts": unknown,
            "unique_units_excluding_unknown": len(grouped),
        },
    }


def build_yield_trend_from_records(records) -> list[dict]:
    rows = [row for row in records if row.start_time]
    if not rows:
        return []
    target_day = max(row.start_time.date() for row in rows)
    by_hour = defaultdict(list)
    for row in rows:
        if row.start_time.date() == target_day:
            by_hour[row.start_time.replace(minute=0, second=0, microsecond=0)].append(row)
    trend = []
    for hour in range(7, 20):
        bucket = datetime.combine(target_day, datetime.min.time()).replace(hour=hour)
        summary = build_summary_from_records(by_hour.get(bucket, []))
        trend.append(
            {
                "hour": bucket.isoformat(),
                "total": summary["total"],
                "passed": summary["pass"],
                "yield_pct": summary["yield_pct"],
            }
        )
    return trend


def build_fail_analysis_from_records(records) -> dict:
    fail_steps = Counter()
    categories = Counter()
    for row in records:
        if row.result == "FAIL":
            fail_steps[row.fail_step_name or "Unknown"] += 1
            categories[row.fail_category or "Unknown"] += 1
        elif row.result == "STOP":
            categories["STOP"] += 1
    return {
        "fail_steps": _counter_rows(fail_steps, "step"),
        "categories": _counter_rows(categories, "category"),
    }


def build_hourly_throughput_from_records(records, hours: int = 24) -> dict:
    rows = [row for row in records if row.start_time]
    if not rows:
        return {"hours": hours, "buckets": [], "current_hour": None}

    latest_hour = max(row.start_time for row in rows).replace(minute=0, second=0, microsecond=0)
    start_hour = latest_hour - timedelta(hours=hours - 1)

    by_hour = defaultdict(list)
    for row in rows:
        bucket = row.start_time.replace(minute=0, second=0, microsecond=0)
        if start_hour <= bucket <= latest_hour:
            by_hour[bucket].append(row)

    buckets = []
    cursor = start_hour
    while cursor <= latest_hour:
        bucket_rows = by_hour.get(cursor, [])
        counts = Counter(row.result for row in bucket_rows)
        total = len(bucket_rows)
        buckets.append(
            {
                "hour": cursor.isoformat(),
                "total": total,
                "PASS": counts.get("PASS", 0),
                "FAIL": counts.get("FAIL", 0),
                "STOP": counts.get("STOP", 0),
                "yield_pct": _pct(counts.get("PASS", 0), total),
            }
        )
        cursor += timedelta(hours=1)

    return {"hours": hours, "buckets": buckets, "current_hour": buckets[-1]}


def build_ai_stats_from_records(records) -> dict:
    rows = list(records)
    summary = build_summary_from_records(rows)
    unit_stats = summary["unit_yield"]["latest_result"]
    unit_rows = [row for row in rows if row.mac1 and row.mac1_source != "unknown"]
    grouped = _group_units(unit_rows)
    total_units = len(grouped)
    retry_units = sum(1 for items in grouped.values() if len(items) > 1)
    fail_steps = build_fail_analysis_from_records(rows)["fail_steps"][:3]
    return {
        "total": unit_stats["total"],
        "passed": unit_stats["PASS"],
        "failed": unit_stats["FAIL"],
        "stopped": unit_stats["STOP"],
        "yield_pct": unit_stats["yield_pct"],
        "retry_rate": _pct(retry_units, total_units),
        "top_fails": [{"step": row["step"], "count": row["count"]} for row in fail_steps],
    }


def build_metric_summary_from_records(records, technology: str | None = None) -> dict:
    metric_rows = []
    for row in records:
        for metric in row.measurements:
            if technology and metric.technology != technology:
                continue
            metric_rows.append(metric)

    by_standard = Counter(metric.standard or "Unknown" for metric in metric_rows)
    by_metric = defaultdict(list)
    for metric in metric_rows:
        by_metric[metric.metric_name].append(metric.value)

    top_metrics = []
    for metric_name, values in sorted(by_metric.items(), key=lambda item: len(item[1]), reverse=True)[:30]:
        top_metrics.append(
            {
                "metric_name": metric_name,
                "count": len(values),
                "avg": round(mean(values), 4),
                "min": min(values),
                "max": max(values),
            }
        )

    return {
        "total_measurements": len(metric_rows),
        "by_standard": _counter_rows(by_standard, "standard"),
        "top_metrics": top_metrics,
    }


def rows_to_parsed_like(rows: list[dict]):
    return [_DictRow(row) for row in rows]


def _group_units(rows) -> dict[tuple[str, str], list]:
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row.work_order, row.mac1)].append(row)
    return grouped


def _yield_for(rows) -> dict:
    total = len(rows)
    passed = sum(1 for row in rows if row.result == "PASS")
    failed = sum(1 for row in rows if row.result == "FAIL")
    stopped = sum(1 for row in rows if row.result == "STOP")
    return {"total": total, "PASS": passed, "FAIL": failed, "STOP": stopped, "yield_pct": _pct(passed, total)}


def _any_pass_yield(grouped) -> dict:
    total = len(grouped)
    passed = 0
    failed = 0
    stopped = 0
    for items in grouped.values():
        results = {row.result for row in items}
        if "PASS" in results:
            passed += 1
        elif "FAIL" in results:
            failed += 1
        else:
            stopped += 1
    return {"total": total, "PASS": passed, "FAIL": failed, "STOP": stopped, "yield_pct": _pct(passed, total)}


def _pct(numerator: int, denominator: int) -> float:
    return round(numerator * 100.0 / denominator, 2) if denominator else 0.0


def _counter_rows(counter: Counter, key_name: str) -> list[dict]:
    return [{key_name: key, "count": value} for key, value in counter.most_common()]


class _DictRow:
    def __init__(self, row: dict):
        self._row = row
        for key, value in row.items():
            setattr(self, key, value)
        self.measurements = row.get("measurements", [])


RECORD_COLUMNS = """
    id, work_order, product, mac1, mac2, mac1_source, start_time,
    end_time, test_duration_sec, result, flow_file, package_name,
    tester_sn, fw_version, fail_step_num, fail_step_name,
    fail_message, fail_category, source_file, created_at
"""


def sql_filters(
    work_order: str | None = None,
    year: int | None = None,
    month: int | None = None,
    week: int | None = None,
    day=None,
    alias: str = "",
    iso_year: bool | None = None,
) -> tuple[str, list]:
    """
    The one scope filter every endpoint uses → ("WHERE ...", params).

    `week` is an ISO week, so together with a week the year is the ISO year
    (2024-12-30 belongs to 2025-W01, not to 2024). `day` is a date or 'YYYY-MM-DD'.
    """
    col = (lambda name: f"{alias}.{name}") if alias else (lambda name: name)
    clauses = []
    params: list = []
    if work_order:
        clauses.append(f"{col('work_order')} = %s")
        params.append(work_order)
    if year is not None:
        if iso_year if iso_year is not None else week is not None:
            clauses.append(f"EXTRACT(ISOYEAR FROM {col('start_time')}) = %s")
            params.append(year)
        else:
            clauses.append(f"{col('start_time')} >= make_date(%s, 1, 1) AND {col('start_time')} < make_date(%s, 1, 1)")
            params.extend([year, year + 1])
    if month is not None:
        clauses.append(f"EXTRACT(MONTH FROM {col('start_time')}) = %s")
        params.append(month)
    if week is not None:
        clauses.append(f"EXTRACT(WEEK FROM {col('start_time')}) = %s")
        params.append(week)
    if day:
        clauses.append(f"{col('start_time')} >= %s::date AND {col('start_time')} < %s::date + 1")
        params.extend([str(day), str(day)])
    return ("WHERE " + " AND ".join(clauses) if clauses else ""), params


def fetch_records(
    conn,
    work_order: str | None = None,
    result: str | None = None,
    limit: int | None = None,
    year: int | None = None,
    month: int | None = None,
    week: int | None = None,
    day=None,
    results: tuple[str, ...] | None = None,
    offset: int = 0,
):
    """Run rows (no raw_log), newest first. No limit unless the caller pages."""
    where, params = sql_filters(work_order, year, month, week, day)
    wanted = tuple(results or ((result,) if result else ()))
    if wanted:
        where = f"{where} AND result = ANY(%s)" if where else "WHERE result = ANY(%s)"
        params.append(list(wanted))
    paging = ""
    if limit is not None:
        paging = "LIMIT %s OFFSET %s"
        params.extend([limit, offset])
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT {RECORD_COLUMNS}
            FROM test_results
            {where}
            ORDER BY start_time DESC, id DESC
            {paging}
            """,
            params,
        )
        columns = [desc[0] for desc in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    return rows_to_parsed_like(rows)


def count_records(conn, results: tuple[str, ...] | None = None, **scope) -> int:
    where, params = sql_filters(**scope)
    if results:
        where = f"{where} AND result = ANY(%s)" if where else "WHERE result = ANY(%s)"
        params.append(list(results))
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM test_results {where}", params)
        return cur.fetchone()[0]


def fetch_filter_options(
    conn,
    work_order: str | None = None,
    year: int | None = None,
    month: int | None = None,
    week: int | None = None,
    day=None,
) -> dict:
    where, params = sql_filters(work_order, year, month, week, day)
    week_where, week_params = sql_filters(work_order, year, month, week, day, iso_year=True)
    with conn.cursor() as cur:
        cur.execute(f"SELECT DISTINCT work_order FROM test_results {where} ORDER BY work_order", params)
        work_orders = [row[0] for row in cur.fetchall()]
        cur.execute(f"SELECT DISTINCT DATE(start_time) FROM test_results {where} ORDER BY DATE(start_time) DESC", params)
        dates = [str(row[0]) for row in cur.fetchall()]
        cur.execute(f"SELECT DISTINCT EXTRACT(YEAR FROM start_time)::int FROM test_results {where} ORDER BY 1 DESC", params)
        years = [row[0] for row in cur.fetchall()]
        cur.execute(f"SELECT DISTINCT EXTRACT(MONTH FROM start_time)::int FROM test_results {where} ORDER BY 1", params)
        months = [row[0] for row in cur.fetchall()]
        cur.execute(f"SELECT DISTINCT EXTRACT(WEEK FROM start_time)::int FROM test_results {week_where} ORDER BY 1", week_params)
        weeks = [row[0] for row in cur.fetchall()]
    return {
        "work_orders": work_orders,
        "dates": dates,
        "days": dates,
        "years": years,
        "months": months,
        "weeks": weeks,
    }


def fetch_work_order_summary(
    conn,
    work_order: str | None = None,
    year: int | None = None,
    month: int | None = None,
    week: int | None = None,
    day: str | None = None,
) -> list[dict]:
    where, params = sql_filters(work_order, year, month, week, day)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            WITH filtered AS (
                SELECT * FROM test_results {where}
            ),
            unit_results AS (
                SELECT DISTINCT ON (work_order, mac1)
                       work_order, mac1, result, start_time
                FROM filtered
                WHERE mac1 IS NOT NULL AND mac1_source != 'unknown'
                ORDER BY work_order, mac1,
                         CASE WHEN result = 'PASS' THEN 0 ELSE 1 END,
                         start_time DESC
            ),
            unit_agg AS (
                SELECT work_order,
                       COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE result = 'PASS') AS passed,
                       COUNT(*) FILTER (WHERE result = 'FAIL') AS failed,
                       COUNT(*) FILTER (WHERE result = 'STOP') AS stopped,
                       ROUND(COUNT(*) FILTER (WHERE result = 'PASS') * 100.0
                             / NULLIF(COUNT(*), 0), 2) AS yield_pct
                FROM unit_results
                GROUP BY work_order
            ),
            attempts AS (
                SELECT work_order,
                       COUNT(*) AS test_attempts,
                       COUNT(*) FILTER (WHERE mac1 IS NULL OR mac1_source = 'unknown') AS unknown_mac,
                       AVG(test_duration_sec) AS avg_duration_sec,
                       MIN(start_time) AS first_test,
                       MAX(start_time) AS last_test
                FROM filtered
                GROUP BY work_order
            ),
            retries AS (
                SELECT work_order, COUNT(*) AS retry_units
                FROM (
                    SELECT work_order, mac1
                    FROM filtered
                    WHERE mac1 IS NOT NULL AND mac1_source != 'unknown'
                    GROUP BY work_order, mac1
                    HAVING COUNT(*) > 1
                ) retry_pairs
                GROUP BY work_order
            )
            SELECT u.work_order, u.total, a.test_attempts,
                   u.passed, u.failed, u.stopped, u.yield_pct,
                   ROUND(COALESCE(r.retry_units, 0) * 100.0 / NULLIF(u.total, 0), 2) AS retry_rate,
                   a.avg_duration_sec, a.first_test, a.last_test, a.unknown_mac,
                   u.passed AS pass, u.failed AS fail, u.stopped AS stop
            FROM unit_agg u
            JOIN attempts a USING (work_order)
            LEFT JOIN retries r USING (work_order)
            ORDER BY a.last_test DESC
            """,
            params,
        )
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def fetch_retry_summary(
    conn,
    work_order: str | None = None,
    year: int | None = None,
    month: int | None = None,
    week: int | None = None,
    day: str | None = None,
) -> list[dict]:
    where, params = sql_filters(work_order, year, month, week, day)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT work_order, mac1, COUNT(*) AS attempt_count, COUNT(*) AS attempts,
                   COUNT(*) FILTER (WHERE result = 'PASS') AS pass_count,
                   COUNT(*) FILTER (WHERE result = 'FAIL') AS fail_count,
                   COUNT(*) FILTER (WHERE result = 'STOP') AS stop_count,
                   MIN(start_time) AS first_attempt, MAX(start_time) AS last_attempt,
                   CASE
                       WHEN COUNT(*) >= 4 THEN 'high'
                       WHEN COUNT(*) >= 3 THEN 'medium'
                       ELSE 'low'
                   END AS retry_risk
            FROM test_results {where}
            AND_OR_WHERE
            GROUP BY work_order, mac1
            HAVING COUNT(*) > 1
            ORDER BY COUNT(*) DESC, MAX(start_time) DESC, work_order, mac1
            LIMIT 500
            """.replace(
                "AND_OR_WHERE",
                "AND mac1 IS NOT NULL AND mac1_source != 'unknown'"
                if where
                else "WHERE mac1 IS NOT NULL AND mac1_source != 'unknown'",
            ),
            params,
        )
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def fetch_metric_summary(conn, technology: str | None = None) -> dict:
    params = []
    where = ""
    if technology:
        where = "WHERE technology = %s"
        params.append(technology)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT COALESCE(standard, 'Unknown') AS standard, COUNT(*)
            FROM test_measurements
            {where}
            GROUP BY COALESCE(standard, 'Unknown')
            ORDER BY COUNT(*) DESC, 1
            """,
            params,
        )
        by_standard = [{"standard": row[0], "count": row[1]} for row in cur.fetchall()]
        cur.execute(
            f"""
            SELECT metric_name, COUNT(*), AVG(value), MIN(value), MAX(value)
            FROM test_measurements
            {where}
            GROUP BY metric_name
            ORDER BY COUNT(*) DESC, metric_name
            LIMIT 30
            """,
            params,
        )
        top_metrics = [
            {"metric_name": row[0], "count": row[1], "avg": round(float(row[2]), 4), "min": row[3], "max": row[4]}
            for row in cur.fetchall()
        ]
        cur.execute(f"SELECT COUNT(*) FROM test_measurements {where}", params)
        total = cur.fetchone()[0]
    return {"total_measurements": total, "by_standard": by_standard, "top_metrics": top_metrics}
