"""IW611 measurement analysis (decision Q8): how far each test item is from its spec.

Every IQfact measurement is a row in test_measurements with its own limits. metric_day
keeps, per day, work order and test item, n / sum / sum² / min / max / fails, so any scope
combines a few hundred rows instead of scanning every measurement. For each item:

    mean, sd             over all attempts in scope
    margin               distance from the worst value to the nearest limit (negative = out of spec)
    margin_pct           margin as % of the spec width (two-sided) or of |limit| (one-sided)
    cpk                  min(USL - mean, mean - LSL) / 3·sd  (None when sd = 0 or no limit)

Histograms read test_measurements directly for one item and are binned in SQL.
"""
import math

import config
import db
from services.filters import Scope, mv_where, raw_where

KEY_COLS = ("technology", "direction", "standard", "band", "frequency_mhz", "bandwidth", "rate", "antenna",
            "metric_name")

# Same rule as metric_day: legacy Wi-Fi rates report 11b / 11g / 11a whatever was stored.
STD_EXPR = """CASE WHEN tm.technology = 'WIFI' AND tm.rate LIKE 'CCK%%' THEN '11b'
                   WHEN tm.technology = 'WIFI' AND tm.rate LIKE 'OFDM%%'
                        THEN CASE WHEN tm.band = '2.4GHz' THEN '11g' ELSE '11a' END
                   ELSE COALESCE(tm.standard, '') END"""

RAW_KEY_EXPR = {
    "technology": "tm.technology",
    "direction": "COALESCE(tm.direction, '')",
    "standard": STD_EXPR,
    "band": "COALESCE(tm.band, '')",
    "frequency_mhz": "COALESCE(tm.frequency_mhz, 0)",
    "bandwidth": "COALESCE(tm.bandwidth, '')",
    "rate": "COALESCE(tm.rate, '')",
    "antenna": "COALESCE(tm.antenna, '')",
    "metric_name": "tm.metric_name",
}

MAX_BINS = 40
TARGET_BINS = 24


def _num(v, digits=4):
    return round(float(v), digits) if v is not None and not (isinstance(v, float) and math.isnan(v)) else None


def _stats(n, s, ss, vmin, vmax, lo, hi):
    """mean, sd, cpk, margin, margin_pct from the running sums."""
    mean = s / n if n else None
    sd = None
    if n and n > 1:
        var = max(0.0, (ss - n * mean * mean) / (n - 1))
        sd = math.sqrt(var)
    cpk = None
    if sd and sd > 1e-12 and (lo is not None or hi is not None):
        sides = []
        if hi is not None:
            sides.append((hi - mean) / (3 * sd))
        if lo is not None:
            sides.append((mean - lo) / (3 * sd))
        cpk = min(sides)
    margins = []
    if hi is not None and vmax is not None:
        margins.append(hi - vmax)
    if lo is not None and vmin is not None:
        margins.append(vmin - lo)
    margin = min(margins) if margins else None
    width = (hi - lo) if (hi is not None and lo is not None and hi > lo) else \
        abs(hi if hi is not None else lo) if (hi is not None or lo is not None) else None
    margin_pct = margin * 100.0 / width if margin is not None and width else None
    return {"mean": _num(mean), "sd": _num(sd), "cpk": _num(cpk, 3), "margin": _num(margin),
            "margin_pct": _num(margin_pct, 1)}


def _status(item):
    if item["n_fail"] or (item["margin"] is not None and item["margin"] < 0):
        return "fail"
    cpk = item["cpk"]
    if cpk is None:
        return "ok" if item["margin"] is None or item["margin"] > 0 else "warn"
    return "ok" if cpk >= config.CPK_GOOD else ("warn" if cpk >= config.CPK_WARN else "bad")


def spec_items(conn, scope: Scope, technology=None, band=None, standard=None, rate=None, search=None,
               only_limited=True):
    """Test items in scope with their spec statistics, worst first (lowest Cpk, then smallest margin)."""
    extra, params2 = [], []
    for col, value in (("technology", technology), ("band", band), ("standard", standard), ("rate", rate)):
        if value:
            extra.append(f"{col} = %s")
            params2.append(value)
    if search:
        extra.append("metric_name ILIKE %s")
        params2.append(f"%{search}%")
    if only_limited:
        extra.append("(limit_low IS NOT NULL OR limit_high IS NOT NULL)")
    where, params = mv_where(scope, extra)
    rows = db.fetch_all(conn, f"""
        SELECT {', '.join(KEY_COLS)}, MAX(unit) AS unit, MIN(limit_low) AS limit_low, MAX(limit_high) AS limit_high,
               SUM(n)::int AS n, SUM(s) AS s, SUM(ss) AS ss, MIN(vmin) AS vmin, MAX(vmax) AS vmax,
               SUM(n_fail)::int AS n_fail
        FROM metric_day {where}
        GROUP BY {', '.join(KEY_COLS)}
    """, params + params2)
    items = []
    for r in rows:
        st = _stats(r["n"], r["s"], r["ss"], r["vmin"], r["vmax"], r["limit_low"], r["limit_high"])
        item = {k: r[k] for k in KEY_COLS}
        item.update(unit=r["unit"], limit_low=_num(r["limit_low"]), limit_high=_num(r["limit_high"]),
                    n=r["n"], n_fail=r["n_fail"], min=_num(r["vmin"]), max=_num(r["vmax"]), **st)
        item["status"] = _status(item)
        items.append(item)
    order = {"fail": 0, "bad": 1, "warn": 2, "ok": 3}
    items.sort(key=lambda i: (order[i["status"]],
                              i["cpk"] if i["cpk"] is not None else math.inf,
                              i["margin_pct"] if i["margin_pct"] is not None else math.inf,
                              i["metric_name"]))
    return items


def status_counts(items):
    out = {"fail": 0, "bad": 0, "warn": 0, "ok": 0}
    for i in items:
        out[i["status"]] += 1
    return out


def _pivot(conn, scope, technology, group_cols, metrics):
    """Per group (e.g. band/standard/bandwidth/rate): mean, worst and fails of a few metrics."""
    where, params = mv_where(scope, ["technology = %s"])
    pieces = []
    for alias, (metric, extra) in metrics.items():
        cond = f"metric_name = '{metric}'" + (f" AND {extra}" if extra else "")
        pieces.append(f"SUM(s) FILTER (WHERE {cond}) / NULLIF(SUM(n) FILTER (WHERE {cond}), 0) AS {alias}_mean")
        pieces.append(f"MIN(vmin) FILTER (WHERE {cond}) AS {alias}_min")
        pieces.append(f"MAX(vmax) FILTER (WHERE {cond}) AS {alias}_max")
        pieces.append(f"MIN(limit_low) FILTER (WHERE {cond}) AS {alias}_lo")
        pieces.append(f"MAX(limit_high) FILTER (WHERE {cond}) AS {alias}_hi")
        pieces.append(f"SUM(n_fail) FILTER (WHERE {cond}) AS {alias}_fail")
        pieces.append(f"SUM(n) FILTER (WHERE {cond}) AS {alias}_n")
    rows = db.fetch_all(conn, f"""
        SELECT {', '.join(group_cols)}, {', '.join(pieces)}, SUM(n_fail) AS all_fail
        FROM metric_day {where}
        GROUP BY {', '.join(group_cols)} ORDER BY {', '.join(group_cols)}
    """, params + [technology])
    out = []
    for r in rows:
        item = {k: r[k] for k in group_cols}
        for alias in metrics:
            if r[f"{alias}_n"]:
                item[alias] = {"mean": _num(r[f"{alias}_mean"]), "min": _num(r[f"{alias}_min"]),
                               "max": _num(r[f"{alias}_max"]), "limit_low": _num(r[f"{alias}_lo"]),
                               "limit_high": _num(r[f"{alias}_hi"]), "fails": int(r[f"{alias}_fail"] or 0),
                               "n": int(r[f"{alias}_n"])}
        item["fails"] = int(r["all_fail"] or 0)
        out.append(item)
    return out


WIFI_TX = {
    "evm": ("EVM_DB_ALL", None),
    "power": ("POWER_AVG_DBM", None),
    "freq_error": ("FREQ_ERROR_AVG", None),
    "lo_leakage": ("LO_LEAKAGE_DBC_VSA1", None),
    "mask_margin": ("MARGIN_DB_LO_A_VSA1", None),
}
WIFI_RX = {"per": ("PER", None), "rssi": ("RSSI_ALL", None)}
BT_TX = {
    "power": ("POWER_AVERAGE_DBM", None),
    "edr_evm": ("EDR_EVM_AV", None),
    "edr_evm_pk": ("EDR_EVM_PK", None),
    "freq_drift": ("FREQ_DRIFT", None),
    "delta_f1": ("DELTA_F1_AVERAGE", None),
}
BT_RX = {"ber": ("BER", None), "per": ("PER", None), "rssi": ("RSSI", None)}


def wifi(conn, scope: Scope):
    tx = _pivot(conn, scope, "WIFI", ["band", "standard", "bandwidth", "rate"],
                {**WIFI_TX})
    tx = [r for r in tx if any(k in r for k in WIFI_TX)]
    rx = _pivot(conn, scope, "WIFI", ["band", "standard", "rate"], WIFI_RX)
    rx = [r for r in rx if "per" in r]
    return {"tx": tx, "rx": rx}


def bt(conn, scope: Scope):
    tx = _pivot(conn, scope, "BT", ["standard", "frequency_mhz"], BT_TX)
    tx = [r for r in tx if "power" in r or "edr_evm" in r]
    rx = _pivot(conn, scope, "BT", ["standard", "frequency_mhz"], BT_RX)
    rx = [r for r in rx if "ber" in r or "per" in r]
    return {"tx": tx, "rx": rx}


def histogram(conn, scope: Scope, key: dict, bin_width=None):
    """{labels, counts, bin, n, limit_low, limit_high, clipped} for one test item in scope."""
    clauses, params = [], []
    for col in KEY_COLS:
        if key.get(col) not in (None, ""):
            clauses.append(f"{RAW_KEY_EXPR[col]} = %s")
            params.append(key[col])
    if not key.get("metric_name"):
        return {"labels": [], "counts": [], "n": 0, "bin": None, "clipped": 0}
    where, sparams = raw_where(scope, ["tm.value IS NOT NULL", "tm.value = tm.value"] + clauses, alias="tr")
    base = f"FROM test_measurements tm JOIN test_results tr ON tr.id = tm.test_result_id {where}"
    p = db.fetch_one(conn, f"""
        SELECT COUNT(*) AS n, MIN(tm.value) AS lo, MAX(tm.value) AS hi,
               percentile_disc(0.01) WITHIN GROUP (ORDER BY tm.value) AS p01,
               percentile_disc(0.99) WITHIN GROUP (ORDER BY tm.value) AS p99,
               MIN(tm.limit_low) AS limit_low, MAX(tm.limit_high) AS limit_high, MAX(tm.unit) AS unit
        {base}
    """, sparams + params)
    if not p or not p["n"]:
        return {"labels": [], "counts": [], "n": 0, "bin": None, "clipped": 0}
    lo, hi = p["p01"], p["p99"]
    if hi <= lo:
        lo, hi = p["lo"], p["hi"]
    if bin_width is None:
        bin_width = _nice((hi - lo) / TARGET_BINS) if hi > lo else 1.0
    b_lo, b_hi = math.floor(lo / bin_width), math.floor(hi / bin_width)
    if b_hi - b_lo + 1 > MAX_BINS:
        b_hi = b_lo + MAX_BINS - 1
    rows = db.fetch_all(conn, f"""
        SELECT LEAST(GREATEST(FLOOR(tm.value / %s)::int, %s), %s) AS b, COUNT(*)::int AS n,
               COUNT(*) FILTER (WHERE FLOOR(tm.value / %s)::int NOT BETWEEN %s AND %s)::int AS clipped
        {base} GROUP BY 1 ORDER BY 1
    """, [bin_width, b_lo, b_hi, bin_width, b_lo, b_hi] + sparams + params)
    counts = {r["b"]: r["n"] for r in rows}
    labels = [_fmt(b * bin_width, bin_width) for b in range(b_lo, b_hi + 1)]
    return {"labels": labels, "counts": [counts.get(b, 0) for b in range(b_lo, b_hi + 1)], "bin": bin_width,
            "n": p["n"], "clipped": sum(r["clipped"] for r in rows), "unit": p["unit"],
            "limit_low": _num(p["limit_low"]), "limit_high": _num(p["limit_high"]),
            "min": _num(p["lo"]), "max": _num(p["hi"])}


def _nice(raw):
    if raw <= 0:
        return 1.0
    exp = math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * 10 ** exp:
            return m * 10 ** exp
    return 10 ** (exp + 1)


def _fmt(value, width):
    digits = max(0, -int(math.floor(math.log10(width)))) if width < 1 else 0
    return f"{value:.{digits}f}"


def other(conn, scope: Scope):
    """Test time, crystal calibration and frequency error over time."""
    where, params = raw_where(scope, ["test_duration_sec IS NOT NULL", "test_duration_sec > 0"])
    by_result = db.fetch_all(conn, f"""
        SELECT result, COUNT(*) AS count, AVG(test_duration_sec) AS avg,
               percentile_disc(0.5) WITHIN GROUP (ORDER BY test_duration_sec)  AS p50,
               percentile_disc(0.9) WITHIN GROUP (ORDER BY test_duration_sec)  AS p90,
               percentile_disc(0.99) WITHIN GROUP (ORDER BY test_duration_sec) AS p99
        FROM test_results {where} GROUP BY result ORDER BY result
    """, params)
    for r in by_result:
        for k in ("avg", "p50", "p90", "p99"):
            r[k] = _num(r[k], 1)
    w, p = mv_where(scope, ["technology = 'WIFI'", "metric_name = 'FREQ_ERROR_AVG'"])
    ppm = db.fetch_all(conn, f"""
        SELECT eff_date AS day, SUM(n)::int AS n, SUM(s) / NULLIF(SUM(n), 0) AS avg, MIN(vmin) AS min, MAX(vmax) AS max
        FROM metric_day {w} GROUP BY eff_date ORDER BY eff_date
    """, p)
    for r in ppm:
        for k in ("avg", "min", "max"):
            r[k] = _num(r[k], 3)
    return {
        "duration": by_result,
        "duration_hist": _duration_hist(conn, scope),
        "xtal_reg": histogram(conn, scope, {"technology": "CALIBRATION", "metric_name": "XTAL_REG"}, 1.0),
        "xtal_margin": histogram(conn, scope, {"technology": "CALIBRATION", "metric_name": "XTAL_MARGIN"}),
        "freq_error_trend": ppm,
    }


def _duration_hist(conn, scope):
    """Test time histogram (seconds), bins sized to the 1st–99th percentile range."""
    where, params = raw_where(scope, ["test_duration_sec IS NOT NULL", "test_duration_sec > 0"])
    p = db.fetch_one(conn, f"""
        SELECT COUNT(*) AS n, MIN(test_duration_sec) AS lo, MAX(test_duration_sec) AS hi,
               percentile_disc(0.01) WITHIN GROUP (ORDER BY test_duration_sec) AS p01,
               percentile_disc(0.99) WITHIN GROUP (ORDER BY test_duration_sec) AS p99
        FROM test_results {where}
    """, params)
    if not p or not p["n"]:
        return {"labels": [], "counts": [], "bin": None, "n": 0, "unit": "s"}
    lo, hi = (p["p01"], p["p99"]) if p["p99"] > p["p01"] else (p["lo"], p["hi"])
    width = _nice((hi - lo) / TARGET_BINS) if hi > lo else 1.0
    b_lo, b_hi = math.floor(lo / width), math.floor(hi / width)
    b_hi = min(b_hi, b_lo + MAX_BINS - 1)
    rows = db.fetch_all(conn, f"""
        SELECT LEAST(GREATEST(FLOOR(test_duration_sec / %s)::int, %s), %s) AS b, COUNT(*)::int AS n,
               COUNT(*) FILTER (WHERE FLOOR(test_duration_sec / %s)::int NOT BETWEEN %s AND %s)::int AS clipped
        FROM test_results {where} GROUP BY 1 ORDER BY 1
    """, [width, b_lo, b_hi, width, b_lo, b_hi] + params)
    counts = {r["b"]: r["n"] for r in rows}
    return {"labels": [_fmt(b * width, width) for b in range(b_lo, b_hi + 1)],
            "counts": [counts.get(b, 0) for b in range(b_lo, b_hi + 1)], "bin": width, "n": p["n"],
            "clipped": sum(r["clipped"] for r in rows), "unit": "s", "min": _num(p["lo"], 1), "max": _num(p["hi"], 1)}


def overview(conn, scope: Scope, top=10):
    items = spec_items(conn, scope)
    return {"status": status_counts(items), "worst": items[:top]}
