from __future__ import annotations

import asyncio
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

import auth
from db import get_connection
from ingestion.postgres import PostgresRunRepository
from ingestion.service import ingest_paths

router = APIRouter(prefix="/api/upload", tags=["upload"])

@router.post("/")
async def upload_logs(
    request: Request,
    files: list[UploadFile] = File(...),
    work_order: str | None = Form(default=None, max_length=64),
    user=Depends(auth.current_user),
):
    """Upload .txt logs or .zip archives. Any signed-in account (administrator or uploader)."""
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

        override = (work_order or "").strip() or None
        # Parsing and DB writes are blocking work; keep them off the event loop.
        batch_id, report = await asyncio.to_thread(_ingest, request, user, collected, override)
        result = report.as_dict()
        result["batch_id"] = batch_id
        for item in result.get("files", []):
            if item.get("source_file"):
                item["source_file"] = Path(item["source_file"]).name
        return result



def _ingest(request: Request, user: dict, collected: list[Path], override: str | None):
    conn = get_connection()
    try:
        repo = PostgresRunRepository(conn)
        batch_id = repo.begin_batch(override, "api")
        report = ingest_paths(collected, repo=repo, work_order_override=override, source="api")
        repo.finish_batch(report)
        auth.audit(conn, request, user["username"], "upload", override,
                   {"batch_id": batch_id, "files": report.total_files, "uploaded": report.uploaded,
                    "duplicates": report.duplicates, "rejected": report.rejected})
        conn.commit()
        return batch_id, report
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


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
