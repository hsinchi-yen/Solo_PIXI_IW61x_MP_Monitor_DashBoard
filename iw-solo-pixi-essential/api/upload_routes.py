from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from db import get_connection
from ingestion.postgres import PostgresRunRepository
from ingestion.service import ingest_paths

router = APIRouter(prefix="/api/upload", tags=["upload"])

@router.post("/")
async def upload_logs(
    files: list[UploadFile] = File(...),
    work_order: str | None = Form(default=None),
):
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    if not (work_order or "").strip() and any(
        Path(upload.filename or "").suffix.lower() == ".txt" for upload in files
    ):
        raise HTTPException(
            status_code=400,
            detail="Work Order is required when uploading individual .txt files",
        )

    with tempfile.TemporaryDirectory(prefix="iw-upload-") as temp_dir:
        temp_root = Path(temp_dir)
        collected = []
        for upload in files:
            name = Path(upload.filename or "").name
            if not name:
                continue
            payload = await upload.read()
            if len(payload) > 512 * 1024 * 1024:
                raise HTTPException(status_code=413, detail=f"{name} exceeds upload size limit")
            target = temp_root / name
            target.write_bytes(payload)
            if target.suffix.lower() == ".zip":
                collected.extend(_extract_safe_zip(target, temp_root / f"{target.stem}_unzipped"))
            elif target.suffix.lower() == ".txt":
                collected.append(target)

        with get_connection() as conn:
            repo = PostgresRunRepository(conn)
            report = ingest_paths(
                collected,
                repo=repo,
                work_order_override=(work_order or "").strip() or None,
                source="api",
            )
            _save_batch(conn, report, work_order, "api")
            conn.commit()
        return report.as_dict()


def _extract_safe_zip(zip_path: Path, dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    extracted = []
    with zipfile.ZipFile(zip_path) as archive:
        infos = archive.infolist()
        total_size = sum(info.file_size for info in infos)
        if total_size > 512 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="ZIP expands beyond upload size limit")
        for info in infos:
            if info.is_dir():
                continue
            if Path(info.filename).is_absolute() or ".." in Path(info.filename).parts:
                raise HTTPException(status_code=400, detail="Unsafe ZIP path")
            target = (dest / info.filename).resolve()
            if not str(target).startswith(str(dest.resolve())):
                raise HTTPException(status_code=400, detail="Unsafe ZIP path")
            archive.extract(info, dest)
            if target.suffix.lower() == ".txt":
                extracted.append(target)
    return extracted


def _save_batch(conn, report, work_order: str | None, source: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO upload_batches (total_files, uploaded, duplicates, rejected, warnings, work_order, upload_source)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (report.total_files, report.uploaded, report.duplicates, report.rejected, report.warnings, work_order, source),
        )
