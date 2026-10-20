"""Web Log Uploader: zip / txt → split per station → parse → test_results, with an AI assistant.

Administrators and uploaders can upload; uploaders only reach the batches they analyzed (Q25).
"""
import json
import os
import queue
import shutil
import threading
import uuid
import zipfile
from typing import List

import psycopg2.extras
from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import StreamingResponse

import config
import db
from errors import ApiError, Msg, bad_request
from schemas import ChatBody, CommitBody, ValidateBody
from services import auth, ingest, llm, upload_chat

router = APIRouter(prefix="/api/log-upload", tags=["log-upload"])

NOT_FOUND_RETRY = Msg("Upload batch not found. Please choose the files again.", "找不到這個上傳批次，請重新選擇檔案")
MAX_FILES = 2000


def _conflict(message, code):
    return ApiError(409, message, code)


@router.post("/analyze")
def analyze(request: Request, files: List[UploadFile] = File(...), user=Depends(auth.current_user)):
    """Store the files, split / parse every run in memory and report the facts. No test_results writes."""
    if not files or len(files) > MAX_FILES:
        raise bad_request(Msg(f"Choose 1 to {MAX_FILES} files.", f"請選擇 1 到 {MAX_FILES} 個檔案。"), "bad_files")
    ingest.cleanup_stale_uploads()
    upload_id = uuid.uuid4().hex
    base = ingest.upload_dir(upload_id)
    os.makedirs(base, exist_ok=True)
    names, size = [], 0
    try:
        for index, upload in enumerate(files):
            name = os.path.basename((upload.filename or "").replace("\\", "/")) or f"file{index}"
            if not name.lower().endswith((".zip", ".txt")):
                raise bad_request(Msg(f"{name}: only .zip and .txt files are accepted.",
                                      f"{name}：只接受 .zip 和 .txt 檔案。"), "bad_type")
            stored = f"{index:04d}_{name}"
            with open(os.path.join(base, stored), "wb") as out:
                shutil.copyfileobj(upload.file, out, 1024 * 1024)
            size += os.path.getsize(os.path.join(base, stored))
            if size > config.UPLOAD_MAX_BYTES:
                raise ApiError(413, Msg("The upload is larger than 512 MB.", "上傳檔案超過 512 MB。"), "too_large")
            if stored.lower().endswith(".zip"):
                with zipfile.ZipFile(os.path.join(base, stored)) as zf:
                    if sum(i.file_size for i in zf.infolist()) > config.UPLOAD_MAX_BYTES:
                        raise ApiError(413, Msg("The zip expands to more than 512 MB.", "zip 解壓後超過 512 MB。"),
                                       "too_large")
            names.append(stored)
        facts, hashes = ingest.analyze(upload_id, names)
    except zipfile.BadZipFile:
        ingest.remove_upload_files(upload_id)
        raise bad_request(Msg("A file is not a valid zip.", "檔案不是有效的 zip 壓縮檔。"), "bad_zip")
    except ApiError:
        ingest.remove_upload_files(upload_id)
        raise
    except Exception:
        ingest.remove_upload_files(upload_id)
        raise bad_request(Msg("Cannot read the upload; a file may be damaged.", "無法讀取上傳檔案，檔案可能已損壞。"),
                          "bad_upload")
    facts["file_names"] = [n[5:] for n in names]
    with open(os.path.join(base, "hashes.json"), "w") as f:
        json.dump(hashes, f)
    with db.db() as conn:
        facts["existing_by_wo"] = ingest.existing_by_wo(conn, hashes)
        db.execute(conn, """
            INSERT INTO upload_log (id, client_ip, username, user_role, file_name, file_size, inferred_wo,
                                    unique_mac1, total_records, facts, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'analyzed')
        """, (upload_id, auth.client_ip(request), user["username"], user["role"], ", ".join(facts["file_names"])[:500],
              size, facts["inferred_wo"], facts["unique_mac1"], facts["total_records"], psycopg2.extras.Json(facts)))
    return {"upload_id": upload_id, "facts": facts,
            "chat_opening": upload_chat.opening_message(facts, "en"),
            "chat_opening_zh": upload_chat.opening_message(facts, "zh"),
            "llm": {k: v for k, v in llm.status().items() if k in ("enabled", "connected")}}


@router.post("/validate")
def validate(body: ValidateBody, user=Depends(auth.current_user)):
    """Run the upload rules (R1–R9) for the entered work order / planned quantity."""
    work_order = (body.work_order or "").strip()
    with db.db() as conn:
        row = ingest.owned_upload(conn, body.upload_id, user, NOT_FOUND_RETRY)
        facts = row["facts"]
        issues = ingest.validate_upload(facts, work_order, body.planned_qty, facts.get("existing_by_wo"),
                                        ingest.current_target(conn, work_order))
    return ingest.validation_payload(issues)


def _chat_extract(messages):
    """(work_order, planned_qty, status) from the latest user message; status: ok | bad_output | unreachable."""
    try:
        data = llm.complete_json(upload_chat.build_extract_messages(messages), upload_chat.EXTRACT_SCHEMA,
                                 name="upload_fields", temperature=0, max_tokens=upload_chat.EXTRACT_MAX_TOKENS)
        wo, qty = upload_chat.parse_extract(json.dumps(data))
    except (ValueError, TypeError):
        return None, None, "bad_output"
    except Exception:
        return None, None, "unreachable"
    return wo, qty, "ok"


@router.post("/chat")
def chat(body: ChatBody, user=Depends(auth.current_user)):
    """One AI-assistant turn → NDJSON: extract, validation, delta…, done | fallback. Replies in `lang` (en | zh).

    The rules decide; the LLM only phrases their result. `known` carries the values already
    confirmed in the form or earlier turns, so switching modes keeps them."""
    try:
        messages = upload_chat.normalize_messages(body.messages)
    except ValueError as exc:
        raise bad_request(exc.args[0], "bad_messages")
    upload_id = body.upload_id
    lang = upload_chat.ui_lang(body.lang)
    with db.db() as conn:
        row = ingest.owned_upload(conn, upload_id, user, NOT_FOUND_RETRY)
        if row["status"] not in ("analyzed", "failed"):
            raise _conflict(Msg(f"This batch has already been processed (status: {row['status']}).",
                                f"這個批次已經處理過（狀態：{row['status']}）"), "already_processed")
        facts = row["facts"]
        llm_wo, llm_qty, status = _chat_extract(messages)
        fields = upload_chat.merge_fields(body.known, messages[-1]["content"], llm_wo, llm_qty, llm_ok=(status == "ok"))
        issues = ingest.validate_upload(facts, fields["work_order"] or "", fields["planned_qty"],
                                        facts.get("existing_by_wo"), ingest.current_target(conn, fields["work_order"]))

    def events():
        yield ingest.ndjson({"type": "extract", **fields, "extract_ok": status == "ok"})
        yield ingest.ndjson({"type": "validation", **ingest.validation_payload(issues)})
        if status == "unreachable":
            yield ingest.ndjson({"type": "fallback", "reason": "The AI assistant is unreachable",
                                 "reason_zh": "AI 助理目前無法連線"})
            return
        parts = []
        try:
            for text in llm.stream_text(upload_chat.build_reply_messages(facts, fields, issues, messages, lang),
                                        temperature=0.3, max_tokens=upload_chat.REPLY_MAX_TOKENS):
                parts.append(text)
                yield ingest.ndjson({"type": "delta", "text": text})
        except Exception:
            yield ingest.ndjson({"type": "fallback", "reason": "The AI assistant timed out",
                                 "reason_zh": "AI 助理回覆逾時或中斷"})
            return
        reply = "".join(parts).strip()
        if not reply:
            reply = upload_chat.fallback_reply(fields, lang)
            yield ingest.ndjson({"type": "delta", "text": reply})
        _save_chat_transcript(upload_id, messages + [{"role": "assistant", "content": reply}])
        yield ingest.ndjson({"type": "done", "reply": reply})

    return StreamingResponse(events(), media_type="application/x-ndjson")


def _save_chat_transcript(upload_id, transcript):
    """Keep the server's copy of the conversation even if the batch is never committed."""
    try:
        with db.db() as conn:
            db.execute(conn, "UPDATE upload_log SET chat_transcript = %s WHERE id = %s AND status IN ('analyzed', 'failed')",
                       (psycopg2.extras.Json(transcript), upload_id))
    except Exception:
        pass


@router.post("/commit")
def commit(body: CommitBody, user=Depends(auth.current_user)):
    """Re-validate against the current DB, then ingest in a background thread and stream progress as NDJSON.
    The ingest keeps running if the browser disconnects; the outcome is recorded in upload_log."""
    upload_id = body.upload_id
    work_order = (body.work_order or "").strip()
    conn = db.connect()
    handed_off = False
    try:
        row = ingest.owned_upload(conn, upload_id, user, NOT_FOUND_RETRY)
        if row["status"] not in ("analyzed", "failed"):
            raise _conflict(Msg(f"This batch has already been processed (status: {row['status']}).",
                                f"這個批次已經處理過（狀態：{row['status']}）"), "already_processed")
        hashes_path = os.path.join(ingest.upload_dir(upload_id), "hashes.json")
        if not os.path.exists(hashes_path):
            raise ApiError(410, Msg("The uploaded files have expired. Please choose them again.",
                                    "暫存檔已過期，請重新選擇檔案"), "expired")
        # Session-level lock; released when this connection closes (early return or ingest end).
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (config.UPLOAD_LOCK_KEY,))
            locked = cur.fetchone()[0]
        if not locked:
            raise _conflict(Msg("Another upload is in progress. Please try again later.", "已有其他上傳正在進行，請稍後再試"),
                            "upload_running")
        with open(hashes_path) as f:
            hashes = json.load(f)
        facts = row["facts"]
        facts["existing_by_wo"] = ingest.existing_by_wo(conn, hashes)
        prev_target = ingest.current_target(conn, work_order)
        issues = ingest.validate_upload(facts, work_order, body.planned_qty, facts["existing_by_wo"], prev_target)
        payload = ingest.validation_payload(issues)
        if not payload["can_upload"]:
            raise bad_request(Msg("Please fix the errors before uploading.", "輸入資料有誤，請修正後再上傳"), "invalid", **payload)
        if payload["needs_ack"] and not body.ack_warnings:
            raise bad_request(Msg("Please confirm the warnings first.", "有警告尚未確認"), "needs_ack", **payload)
        qty = ingest.parse_quantity(body.planned_qty)
        db.execute(conn, """
            UPDATE upload_log SET work_order = %s, planned_qty = %s, prev_target = %s, facts = %s, validation = %s,
                   mode = %s, status = 'running', error = NULL
            WHERE id = %s
        """, (work_order, qty, prev_target, psycopg2.extras.Json(facts), psycopg2.extras.Json(issues), body.mode,
              upload_id))
        conn.commit()
        events = queue.Queue()
        threading.Thread(target=ingest.run_ingest,
                         args=(conn, events, upload_id, work_order, qty, facts["total_records"], user["username"]),
                         daemon=True, name=f"ingest-{upload_id[:8]}").start()
        handed_off = True
        return StreamingResponse(ingest.ndjson_events(events), media_type="application/x-ndjson")
    finally:
        if not handed_off:
            conn.close()


@router.get("/history")
def history(limit: int = Query(20, ge=1, le=200), username: str = Query(None, max_length=64),
            user=Depends(auth.current_user)):
    """Recent batches. Uploaders always get only their own; the administrator may filter by uploader."""
    if user["role"] != "admin":
        username = user["username"]
    where, params = "WHERE l.status <> 'analyzed'", []
    if username:
        where += " AND l.username = %s"
        params.append(username)
    with db.db() as conn:
        items = db.fetch_all(conn, f"""
            SELECT l.id, l.created_at, l.finished_at, l.username, l.user_role,
                   COALESCE(u.display_name, l.username) AS display_name, l.client_ip, l.file_name,
                   l.work_order, l.planned_qty, l.unique_mac1, l.total_records, l.mode, l.status,
                   l.uploaded, l.skipped, l.failed, l.reverted, l.reverted_by, l.reverted_at, l.elapsed_sec, l.error
            FROM upload_log l LEFT JOIN app_users u ON u.username = l.username
            {where} ORDER BY l.created_at DESC LIMIT %s
        """, params + [limit])
    return {"items": items}


@router.get("/{upload_id}")
def detail(upload_id: str, user=Depends(auth.current_user)):
    with db.db() as conn:
        return ingest.owned_upload(conn, upload_id, user)


@router.delete("/{upload_id}")
def revert(upload_id: str, confirm: str = Query(""), user=Depends(auth.current_user)):
    """Undo one web upload: delete only the rows it inserted and restore the alignment target it set."""
    auth.require_delete_confirmation(confirm)
    with db.db() as conn:
        row = ingest.owned_upload(conn, upload_id, user)
        if row["status"] not in ("done", "failed"):
            raise bad_request(Msg(f"Only finished or failed batches can be undone (current status: {row['status']}).",
                                  f"只有已完成或失敗的批次可以撤銷（目前狀態：{row['status']}）"), "not_revertible")
        reverted = ingest.revert(conn, row, user["username"])
    ingest.after_change(row["work_order"], queue_report=False)
    return {"reverted": reverted, "upload_id": upload_id}
