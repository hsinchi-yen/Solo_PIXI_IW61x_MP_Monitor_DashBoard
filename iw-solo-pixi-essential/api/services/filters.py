"""Query scope: a date range on the test date plus an optional work order.

Filters are half-open ranges on start_time (`>= from AND < to`), so the existing
idx_tr_start_time / idx_tr_wo_time indexes apply.

API parameters:
    date_from / date_to   ISO dates, both inclusive (the UI's Today / This week / This month / Custom)
    work_order
Older parameters still accepted (old links, scripts): day, year, month, week.
`week` is an ISO week and needs `year`; year=2024&week=1 is 2024-01-01..2024-01-07.
"""
import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from errors import Msg, bad_request


@dataclass(frozen=True)
class Scope:
    date_from: Optional[date] = None   # inclusive
    date_to: Optional[date] = None     # exclusive
    work_order: Optional[str] = None

    @property
    def is_empty(self):
        return self.date_from is None and self.date_to is None and not self.work_order

    @property
    def has_dates(self):
        return self.date_from is not None or self.date_to is not None

    def describe(self, lang="en"):
        parts = []
        if self.work_order:
            parts.append(f"WO {self.work_order}" if lang == "en" else f"工單 {self.work_order}")
        if self.has_dates:
            last = (self.date_to - timedelta(days=1)) if self.date_to else None
            if self.date_from and last and self.date_from == last:
                parts.append(self.date_from.isoformat())
            else:
                parts.append(f"{self.date_from.isoformat() if self.date_from else '…'} ~ {last.isoformat() if last else '…'}")
        return ", ".join(parts) or ("All data" if lang == "en" else "全部資料")

    def as_params(self):
        out = {}
        if self.work_order:
            out["work_order"] = self.work_order
        if self.date_from:
            out["date_from"] = self.date_from.isoformat()
        if self.date_to:
            out["date_to"] = (self.date_to - timedelta(days=1)).isoformat()
        return out


def _parse_date(value, name):
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except (TypeError, ValueError):
        raise bad_request(Msg(f"Invalid {name} date (use YYYY-MM-DD).", f"{name} 日期格式不正確（應為 YYYY-MM-DD）"),
                          "bad_date")


def _intersect(a_from, a_to, b_from, b_to):
    lo = max([d for d in (a_from, b_from) if d], default=None)
    hi = min([d for d in (a_to, b_to) if d], default=None)
    return lo, hi


def parse_scope(date_from=None, date_to=None, work_order=None, year=None, month=None, week=None, day=None):
    """Build a Scope from request parameters. Raises ApiError(400) on bad input."""
    lo = _parse_date(date_from, "date_from") if date_from else None
    hi = _parse_date(date_to, "date_to") + timedelta(days=1) if date_to else None
    if lo and hi and lo >= hi:
        raise bad_request(Msg("The start date is after the end date.", "起始日期不可晚於結束日期"), "bad_range")

    if day:
        d = _parse_date(day, "day")
        lo, hi = _intersect(lo, hi, d, d + timedelta(days=1))
    if year or month or week:
        if not year:
            raise bad_request(Msg("month / week need year as well.", "month / week 需要同時指定 year"), "bad_range")
        y = int(year)
        if week:
            try:
                start = date.fromisocalendar(y, int(week), 1)
            except ValueError:
                raise bad_request(Msg("week is out of range.", "week 超出範圍"), "bad_range")
            r_lo, r_hi = start, start + timedelta(days=7)
        elif month:
            m = int(month)
            r_lo, r_hi = date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1]) + timedelta(days=1)
        else:
            r_lo, r_hi = date(y, 1, 1), date(y + 1, 1, 1)
        lo, hi = _intersect(lo, hi, r_lo, r_hi)

    wo = (work_order or "").strip() or None
    if wo and len(wo) > 64:
        raise bad_request(Msg("The work order is too long.", "工單號太長"), "bad_work_order")
    if lo and hi and lo >= hi:
        hi = lo   # empty intersection stays empty rather than widening
    return Scope(lo, hi, wo)


def raw_where(scope: Scope, extra=None, alias=""):
    """WHERE clause + params for test_results rows."""
    col = (lambda c: f"{alias}.{c}") if alias else (lambda c: c)
    clauses, params = [], []
    if scope.date_from:
        clauses.append(f"{col('start_time')} >= %s")
        params.append(scope.date_from)
    if scope.date_to:
        clauses.append(f"{col('start_time')} < %s")
        params.append(scope.date_to)
    if scope.work_order:
        clauses.append(f"{col('work_order')} = %s")
        params.append(scope.work_order)
    clauses.extend(extra or [])
    return ("WHERE " + " AND ".join(clauses)) if clauses else "", params


def mv_where(scope: Scope, extra=None):
    """WHERE clause + params for the per-day tables (mv_unit_day, metric_day)."""
    clauses, params = [], []
    if scope.date_from:
        clauses.append("eff_date >= %s")
        params.append(scope.date_from)
    if scope.date_to:
        clauses.append("eff_date < %s")
        params.append(scope.date_to)
    if scope.work_order:
        clauses.append("wo_key = %s")
        params.append(scope.work_order)
    clauses.extend(extra or [])
    return ("WHERE " + " AND ".join(clauses)) if clauses else "", params
