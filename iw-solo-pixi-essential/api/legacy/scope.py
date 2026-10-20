"""The dashboard's scope filter (work order / year / month / ISO week / day) as one validated dependency.

    def endpoint(scope: Scope = Depends()):
        fetch_records(conn, **scope.kw())

Out-of-range values (month=13, week=99, day=foo) are rejected with 400 instead of
reaching SQL.
"""
from datetime import date
from pathlib import PureWindowsPath

from fastapi import Query


class Scope:
    def __init__(
        self,
        work_order: str | None = Query(None, max_length=64),
        year: int | None = Query(None, ge=2000, le=2100),
        month: int | None = Query(None, ge=1, le=12),
        week: int | None = Query(None, ge=1, le=53, description="ISO week; with a week, year is the ISO year"),
        day: date | None = Query(None, description="YYYY-MM-DD"),
    ):
        self.work_order = work_order or None
        self.year = year
        self.month = month
        self.week = week
        self.day = day

    def kw(self) -> dict:
        return {"work_order": self.work_order, "year": self.year, "month": self.month,
                "week": self.week, "day": self.day}

    def any(self) -> bool:
        return any(v is not None for v in self.kw().values())


def file_name(source_file: str | None) -> str | None:
    """Only the file name of an uploaded log; the uploading PC's folders are not shown."""
    if not source_file:
        return source_file
    return PureWindowsPath(source_file).name
