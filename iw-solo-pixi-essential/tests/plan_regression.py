"""Regression check of every fix and feature in improve_plan.md against a running stack.

Standard library only, so it also runs on the server after a deployment. Each check is tagged
with its improve_plan.md item (B1…B7, S1…S4, R1…R4, U2…U5, C1, A1…A6, B2…B5, C1…C4, D, E1…E4).

    python tests/plan_regression.py                                  # read-only, http://localhost:8003
    python tests/plan_regression.py --base http://host:8003 \\
        --db-container iw-solo-pixi-essential-postgres-1 \\            # + database checks (docker exec psql)
        --admin pixi:pixipass \\                                      # + checks that need the administrator
        --write \\                                                    # + creates the uploader reg.uploader
        --llm \\                                                      # + generates an AI report (≈ 1 min)
        --json out.json

Read-only mode makes GET requests and a few rejected writes (401/400) only. --admin signs in
(adds a session row and a login-log row). --write creates or resets the uploader account
reg.uploader. --llm stores a report in ai_reports. None of them changes test data.
"""
import argparse
import base64
import hashlib
import hmac
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE_WO = "5101-260715003"
RESULTS = []


def check(item, title, ok, detail=""):
    status = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
    RESULTS.append({"item": item, "title": title, "status": status, "detail": str(detail)[:300]})
    print(f"{status:4}  {item:4}  {title}" + (f"  — {detail}" if detail and status != "PASS" else ""))


class Http:
    def __init__(self, base):
        self.base = base.rstrip("/")

    def call(self, method, path, body=None, headers=None, raw=False):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=dict(headers or {}))
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=180) as res:
                status, hdrs, content = res.status, dict(res.headers), res.read()
        except urllib.error.HTTPError as err:
            status, hdrs, content = err.code, dict(err.headers), err.read()
        if raw:
            return status, hdrs, content
        try:
            return status, json.loads(content) if content else None
        except ValueError:
            return status, content.decode(errors="replace")

    def get(self, path, **kw):
        return self.call("GET", path, **kw)

    def json(self, path, **kw):
        status, body = self.get(path, **kw)
        if status != 200:
            raise RuntimeError(f"GET {path} → {status}: {str(body)[:200]}")
        return body


def ndjson(text):
    return [json.loads(line) for line in (text or "").splitlines() if line.strip()]


class Db:
    def __init__(self, container):
        self.container = container

    def scalar(self, sql):
        out = subprocess.run(["docker", "exec", self.container, "psql", "-U", "pixi", "-d", "pixi_test", "-Atc", sql],
                             capture_output=True, text=True, check=True).stdout.strip()
        return out


def run(args):
    http = Http(args.base)
    db = Db(args.db_container) if args.db_container else None
    s = http.json("/api/summary")
    att = s["attempts"]
    acceptance = any(w["work_order"] == ACCEPTANCE_WO and w["attempts"] == 180
                     for w in http.json("/api/filter-options")["work_orders"])

    # ── 數字 (B1–B7) ──────────────────────────────────────────────────────────
    pages, items, page = None, [], 1
    while pages is None or page <= pages:
        d = http.json(f"/api/fails?page_size=50&page={page}")
        pages, total = d["pages"], d["total"]
        items += d["items"]
        page += 1
    ids = [i["id"] for i in items]
    check("B1", "Fail list is complete over all pages (no 5000/10000 cut-off)",
          total == att["fail"] + att["stop"] == len(items) == len(set(ids)),
          f"total={total} fail+stop={att['fail'] + att['stop']} paged={len(items)}")
    check("B1", "No LIMIT 5000 left in the API code",
          not any("LIMIT 5000" in p.read_text() for p in (ROOT / "api").rglob("*.py")) if (ROOT / "api").is_dir() else None)

    y = s["yield"]
    sums_ok = all(y[k]["pass"] + y[k]["fail"] + y[k]["stop"] == s["units"] for k in ("latest", "first_pass", "any_pass"))
    order_ok = y["first_pass"]["pass"] <= y["latest"]["pass"] <= y["any_pass"]["pass"]
    check("B2", "Three yields over the same units (latest / first-pass / any-pass)", sums_ok and order_ok, y)
    check("B2", "Runs kept apart from units (PASS+FAIL+STOP = runs)", att["pass"] + att["fail"] + att["stop"] == att["total"], att)
    if acceptance:
        got = (s["units"], y["latest"]["yield_pct"], y["first_pass"]["yield_pct"], y["any_pass"]["yield_pct"],
               att["total"], att["pass"], att["fail"], att["stop"], att["unknown_mac"])
        check("B2", "Acceptance numbers 120 / 99.17 / 72.5 / 99.17 / 180 / 121 / 13 / 46 / 7",
              got == (120, 99.17, 72.5, 99.17, 180, 121, 13, 46, 7), got)

    fa = http.json("/api/fail-analysis")
    nv = next((st for st in fa["top_steps"] if st["step"] == "SAVE_NVRAM"), None)
    check("B3", "Fail steps report runs and units separately (SAVE_NVRAM)",
          nv is not None and "attempts" in nv and "units" in nv and (not acceptance or (nv["attempts"], nv["units"]) == (13, 11)),
          nv)

    m = http.json("/api/measurements")
    ber = {r["frequency_mhz"]: r["ber"] for r in m["bt"]["rx"] if r.get("ber")}
    check("B4", "BT BER reported at the IW611 channels 2403 / 2478 MHz",
          all(f in ber and ber[f]["n"] > 0 and ber[f]["mean"] is not None for f in (2403, 2478)), sorted(ber))
    bdr = next((r for r in m["bt"]["tx"] if r["standard"] == "BDR" and r.get("power")), None)
    check("B5", "BDR power counted once per run (n ≤ runs" + (", = 136 on the acceptance data)" if acceptance else ")"),
          bdr is not None and bdr["power"]["n"] <= att["total"] and (not acceptance or bdr["power"]["n"] == 136),
          bdr and bdr["power"]["n"])
    labels = [(r["standard"], r["rate"], r["band"]) for r in m["wifi"]["tx"] + m["wifi"]["rx"]
              if str(r.get("rate", "")).startswith(("CCK", "OFDM"))]
    expect = lambda rate, band: "11b" if rate.startswith("CCK") else ("11g" if band == "2.4GHz" else "11a")
    check("B5", "CCK / OFDM rates labelled 11b / 11g / 11a (not 11n)",
          bool(labels) and all(st == expect(rate, band) for st, rate, band in labels), labels[:6])

    wk = http.json("/api/measurements?year=2025&week=1")["scope"]["params"]
    check("B6", "ISO week: 2025 week 1 = 2024-12-30 … 2025-01-05",
          (wk.get("date_from"), wk.get("date_to")) == ("2024-12-30", "2025-01-05"), wk)
    hourly = http.json("/api/hourly")["points"]
    check("B6", "Hourly chart covers 24 hours (no 07:00–19:59 window)", len(hourly) == 24, len(hourly))
    trend = http.json("/api/trend")["points"]
    check("B6", "Yield trend has the three yields per point",
          bool(trend) and all({"latest", "first_pass", "any_pass"} <= set(p) for p in trend), trend[:1])

    same = all(http.json(p) == http.json(p) for p in ("/api/measurements/items?limit=2000", "/api/fail-analysis",
                                                       "/api/retries?limit=1000"))
    check("B7", "Lists are deterministic (two calls give the same order)", same)

    # ── 安全 (S1–S4) ──────────────────────────────────────────────────────────
    b64 = lambda v: base64.urlsafe_b64encode(v).decode().rstrip("=")
    body = b64(json.dumps({"sub": "pixi", "exp": int(time.time()) + 3600}).encode())
    forged = body + "." + b64(hmac.new(b"change-me-local-secret", body.encode(), hashlib.sha256).digest())
    check("S1", "Token signed with the old default secret is rejected",
          http.get("/api/tweak/records", headers={"X-Tweak-Token": forged})[0] == 401)

    first_id = ids[0] if ids else 1
    anon = [("POST", "/api/alignment-targets", {ACCEPTANCE_WO: 1}), ("POST", "/api/log-upload/analyze", None),
            ("GET", f"/api/raw-log/{first_id}", None), ("GET", "/api/tweak/records", None),
            ("DELETE", f"/api/tweak/delete-scope?work_order=NOPE&confirm=DELETE", None), ("POST", "/api/reparse", None),
            ("GET", "/api/admin/users", None), ("GET", "/api/log-upload/history", None),
            ("POST", f"/api/reports/{ACCEPTANCE_WO}/ask", {"messages": [{"role": "user", "content": "x"}]})]
    codes = {f"{mth} {p.split('?')[0]}": http.call(mth, p, body=b)[0] for mth, p, b in anon}
    protected = {k: v for k, v in codes.items() if not k.endswith("/ask")}
    check("S2", "Writes, uploads, raw logs and admin pages need a session (401)",
          all(v == 401 for v in protected.values()), protected)

    status, hdrs, _ = http.call("GET", "/api/summary", headers={"Origin": "http://evil.example"}, raw=True)
    check("S3", "CORS: other origins get no Access-Control-Allow-Origin",
          status == 200 and not any(k.lower() == "access-control-allow-origin" for k in hdrs))

    bad = http.get("/api/summary?day=foo")[1]
    leaky = re.compile(r"Traceback|psycopg2|File \"|SELECT ", re.I)
    check("S4", "Error body is {error, error_zh, code} without internals",
          isinstance(bad, dict) and {"error", "error_zh", "code"} <= set(bad) and not leaky.search(json.dumps(bad)), bad)
    check("S4", "Only file names (no client paths) in the fail list",
          all(not re.search(r"[\\/]", i["source_file"] or "") for i in items), [i["source_file"] for i in items[:2]])

    # ── 穩定性 (R1–R4) ────────────────────────────────────────────────────────
    bad_params = ["day=foo", "day=2026-13-45", "week=99&year=2026", "month=13&year=2026", "week=3",
                  "date_from=2026-07-22&date_to=2026-07-21", "year=1999", "page_size=abc"]
    r2 = {p: http.get(("/api/fails?" if "page_size" in p else "/api/summary?") + p)[0] for p in bad_params}
    check("R2", "Bad parameters are 400, never 500", all(v == 400 for v in r2.values()), r2)
    if db:
        conns = "SELECT COUNT(*) FROM pg_stat_activity WHERE datname = 'pixi_test'"
        before = int(db.scalar(conns))
        for _ in range(40):
            http.get("/api/summary?day=foo")
            http.get("/api/fails?page_size=abc")
        for _ in range(10):
            http.get("/api/summary")
        after = int(db.scalar(conns))
        check("R1", "80 failed + 10 normal requests do not leak connections", after <= before + 2, f"{before} → {after}")
        batches = db.scalar("SELECT COUNT(*) FROM upload_batches")
        # Runs stored since batches are recorded (2.0) must have their data-quality issues tied to a batch.
        orphan = db.scalar("SELECT COUNT(*) FROM data_quality_issues q JOIN test_results t ON t.id = q.test_result_id "
                           "WHERE q.batch_id IS NULL AND t.created_at >= (SELECT MIN(uploaded_at) FROM upload_batches)")
        check("R3", "Upload batches are recorded and their warnings carry the batch id",
              None if batches == "0" else orphan == "0", f"batches={batches} issues without batch={orphan}")
        blank = db.scalar("SELECT COUNT(*) FROM upload_batches WHERE work_order IS NULL AND uploaded > 0 "
                          "AND uploaded_at > NOW() - INTERVAL '1 day'")
        check("R3", "Recent upload batches name their work order", None if batches == "0" else blank == "0",
              f"blank={blank}")
    else:
        check("R1", "Connection leak (needs --db-container)", None)

    # ── 介面與設定 (U2–U5, C1, A3) — repository files ─────────────────────────
    web = ROOT / "web"
    if web.is_dir():
        js = {p: p.read_text(errors="replace") for p in web.rglob("*.js") if "vendor" not in p.parts}
        html = (web / "index.html").read_text()
        text = html + "".join(js.values())
        check("U2", "No QCA product name (PIXI-9377 / 9377) in the web app", "9377" not in text)
        check("U4", "No page rotation code", not re.search(r"rotat|carousel", text.replace("No page rotation", ""), re.I))
        check("U5", "No QCA leftovers (2441 / 2480 MHz, Config Analysis)",
              not re.search(r"\b2441\b|\b2480\b|config-analysis|Config Analysis", text))
        check("E1", "Legacy single-file page and API removed",
              not (web / "legacy.html").exists() and not (ROOT / "api" / "legacy").exists() and not (ROOT / "static").exists())
        keys = lambda f: set(re.findall(r"^\s*'([\w.]+)':", (web / "js" / "core" / f).read_text(), re.M)) \
            if (web / "js" / "core" / f).exists() else None
        en, zh = keys("strings.en.js"), keys("strings.zh.js")
        check("A3", "English and Chinese strings have the same keys",
              None if en is None else (en == zh and len(en) > 100), None if en is None else f"en={len(en)} zh={len(zh)} "
              f"missing_zh={sorted(en - zh)[:5]} missing_en={sorted(zh - en)[:5]}")
        cfg = (ROOT / "api" / "config.py").read_text()
        check("C1", "AI off by default, no internal LLM address in code",
              re.search(r'LLM_ENABLED.*"false"', cfg) is not None and "10.20.30" not in cfg
              and "10.20.30" not in (ROOT / "docker-compose.yml").read_text() and "10.20.30" not in text)
        compose = (ROOT / "docker-compose.yml").read_text()
        check("E2", "Code is built into the image (no ./api bind mounts in docker-compose.yml)",
              "./api/" not in compose and "COPY" in (ROOT / "api" / "Dockerfile").read_text())
        check("E4", "deploy.sh and backup_db.sh present and executable",
              all((ROOT / "scripts" / n).exists() and (ROOT / "scripts" / n).stat().st_mode & 0o111
                  for n in ("deploy.sh", "backup_db.sh")))

    # ── 功能 (A1, A6, B2–B5, C4, E2) ───────────────────────────────────────────
    latest = max(http.json("/api/filter-options")["work_orders"], key=lambda w: w["last_test"])["work_order"]
    ov = http.json("/api/overview?latest=true")
    check("A1", "Dashboard defaults to the latest work order", ov["scope"]["params"].get("work_order") == latest,
          ov["scope"]["params"])
    it = http.json("/api/measurements/items?limit=2000")
    rank = {"fail": 0, "bad": 1, "warn": 2, "ok": 3}
    ranks = [rank[i["status"]] for i in it["items"]]
    check("A6", "Spec check: every limited item listed, worst first",
          it["total"] == len(it["items"]) == sum(it["status"].values()) and ranks == sorted(ranks),
          f"total={it['total']} status={it['status']}")
    top = it["items"][0]
    q = "&".join(f"{k}={urllib.request.quote(str(top[k]))}" for k in
                 ("technology", "metric_name", "direction", "standard", "band", "frequency_mhz", "bandwidth", "rate",
                  "antenna"))
    h = http.json(f"/api/measurements/histogram?{q}")
    check("B3", "Histogram binned on the server, with the spec limits",
          sum(h["counts"]) + h.get("clipped", 0) == h["n"] == top["n"] and len(h["counts"]) <= 80
          and (h["limit_low"], h["limit_high"]) == (top["limit_low"], top["limit_high"]),
          f"n={h['n']} bins={len(h['counts'])}")
    al = http.json("/api/alignment")["items"]
    check("A1", "Aligned yield = PASS ÷ max(units, planned)",
          all(abs(a["aligned_yield"] - round(100 * a["pass"] / max(a["units"], a["target_total"] or 0, 1), 2)) < 0.01
              for a in al), al[:1])
    if db:
        units = int(db.scalar("SELECT COUNT(*) FROM mv_unit"))
        rel = db.scalar("SELECT string_agg(relname, ',' ORDER BY relname) FROM pg_class WHERE relname IN "
                        "('mv_unit','mv_unit_day','metric_day','metric_wo_state','ai_reports','upload_log','upload_runs')")
        check("B2", "Summary objects exist (mv_unit, mv_unit_day, metric_day …) and mv_unit = units",
              rel.count(",") == 6 and units == s["units"], f"{rel}; mv_unit={units}")
        n_raw = int(db.scalar("SELECT COUNT(*) FROM test_measurements"))
        n_day = int(db.scalar("SELECT COALESCE(SUM(n),0) FROM metric_day"))
        nan = int(db.scalar("SELECT COUNT(*) FROM test_measurements WHERE value IS NULL OR value <> value"))
        check("B2", "metric_day holds every measurement value", n_day == n_raw - nan, f"{n_day} vs {n_raw - nan}")

    status, hdrs, _ = http.call("GET", "/js/main.js", headers={"Accept-Encoding": "gzip"}, raw=True)
    h_lower = {k.lower(): v for k, v in hdrs.items()}
    check("B4", "nginx gzip and no-cache on app code",
          None if "nginx" not in h_lower.get("server", "") else
          status == 200 and h_lower.get("content-encoding") == "gzip" and "no-cache" in h_lower.get("cache-control", ""),
          h_lower.get("cache-control"))
    health = http.json("/health")
    check("E2", "/health reports version, DB time, LLM and statistics time",
          health["status"] == "ok" and {"version", "db_ms", "llm", "stats_refreshed_at"} <= set(health), health)
    llm = http.json("/api/llm-status")
    check("C4", "LLM status endpoint (enabled / connected / model)", {"enabled", "connected", "model"} <= set(llm), llm)
    code_docs = http.get("/docs")[0]
    check("E2", "OpenAPI docs reachable (/docs)", None if code_docs is None else code_docs == 200)

    r1 = http.call("POST", "/api/stats/refresh")
    r2_ = http.call("POST", "/api/stats/refresh")
    check("B2", "Refresh now: public, at most once per 10 seconds",
          r1[0] in (200, 429) and r2_[0] == 429 and r2_[1].get("code") == "too_soon", (r1[0], r2_[0]))

    # ── 帳號 (D, S3, B5) ──────────────────────────────────────────────────────
    if args.admin:
        user, pw = args.admin.split(":", 1)
        st, login = http.call("POST", "/api/auth/login", body={"username": user, "password": pw})
        if st != 200:
            check("D", "Administrator sign-in", False, login)
        else:
            adm = {"X-Tweak-Token": login["token"]}
            check("D", "Administrator sign-in", login.get("role") == "admin", login.get("role"))
            check("S3", "Token in the URL (?token=) is not accepted",
                  http.get(f"/api/tweak/records?token={login['token']}")[0] == 401)
            st, body_ = http.call("DELETE", "/api/tweak/delete-scope?work_order=NOPE", headers=adm)
            check("S3", "Delete without confirm=DELETE is refused", st == 400 and body_.get("code") == "confirm_required",
                  body_)
            recs = http.json("/api/tweak/records?page_size=20", headers=adm)
            rows = recs.get("items") or recs.get("records") or []
            check("S4", "DB Tweak shows file names only",
                  bool(rows) and all(not re.search(r"[\\/]", r.get("source_file") or "") for r in rows))
            st, hd, content = http.call("GET", f"/api/raw-log/{first_id}", headers=adm, raw=True)
            disp = {k.lower(): v for k, v in hd.items()}.get("content-disposition", "")
            check("B5", "Raw log download for a signed-in account, safe file name",
                  st == 200 and len(content) > 1000 and re.search(r'filename="[\w.\-]+"', disp) is not None, disp)
            check("B5", "Reparse runs as a background job (status endpoint)", http.get("/api/reparse", headers=adm)[0] == 200)
            check("D", "Audit and login logs readable by the administrator",
                  http.get("/api/admin/audit", headers=adm)[0] == 200 and http.get("/api/admin/login-log", headers=adm)[0] == 200)
            if args.write:
                http.call("POST", "/api/admin/users", headers=adm,
                          body={"username": "reg.uploader", "display_name": "Regression", "password": "reg-uploader-1"})
                http.call("POST", "/api/admin/users/reg.uploader/reset-password", headers=adm,
                          body={"password": "reg-uploader-1"})
                http.call("PATCH", "/api/admin/users/reg.uploader", headers=adm, body={"enabled": True})
                st, up = http.call("POST", "/api/auth/login", body={"username": "reg.uploader", "password": "reg-uploader-1"})
                if st != 200:
                    check("D", "Uploader account sign-in", False, up)
                else:
                    uh = {"X-Tweak-Token": up["token"]}
                    denied = {p: http.call(mth, p, headers=uh, body=b)[0] for mth, p, b in
                              (("GET", "/api/admin/users", None), ("GET", "/api/tweak/records", None),
                               ("POST", "/api/alignment-targets", {ACCEPTANCE_WO: 1}),
                               ("DELETE", "/api/tweak/delete-scope?work_order=NOPE&confirm=DELETE", None))}
                    check("D", "Uploader cannot do administrator work (403)", all(v == 403 for v in denied.values()), denied)
                    check("D", "Uploader can download raw logs and see upload history",
                          http.get(f"/api/raw-log/{first_id}", headers=uh)[0] == 200
                          and http.get("/api/log-upload/history", headers=uh)[0] == 200)
                    http.call("POST", "/api/auth/logout", headers=uh)
                    check("D", "Signed-out token stops working", http.get("/api/log-upload/history", headers=uh)[0] == 401)
            http.call("POST", "/api/auth/logout", headers=adm)

    # ── AI 報告 (C1–C3, R4, B3) ────────────────────────────────────────────────
    wo = latest
    if args.llm:
        t0 = time.time()
        st, text = http.call("POST", f"/api/reports/{wo}/generate?force=true")
        events = ndjson(text if isinstance(text, str) else "")
        elapsed = time.time() - t0
        rep = http.json(f"/api/reports/{wo}")["report"] or {}
        checks_ = rep.get("checks") or {}
        check("C1", "Report in both languages; every number comes from the facts",
              rep.get("status") == "done" and all(checks_.get(l, {}).get("ok") for l in ("en", "zh")), checks_)
        facts = rep.get("facts") or {}
        check("C1", "Facts carry the three yields, units and runs",
              "120" in json.dumps(facts) if acceptance else bool(facts), list(facts)[:8])
        check("C2", "Generation time (both languages)", elapsed < 120, f"{elapsed:.0f} s (plan target 30 s; Q27 accepts ≈ 55 s)")
        st, text = http.call("POST", f"/api/reports/{wo}/generate?force=false")
        check("R4", "Unchanged data reuses the stored report", any(e.get("type") == "cached" for e in ndjson(text)),
              [e.get("type") for e in ndjson(text)])
        hist = http.json(f"/api/reports/{wo}/history")
        cmp_ = http.get(f"/api/reports/{wo}/compare")
        md = http.get(f"/api/reports/{wo}/markdown?lang=zh")
        check("C3", "History, compare and Markdown download",
              cmp_[0] == 200 and md[0] == 200 and wo in str(md[1]) and bool(hist), md[0])
        st, text = http.call("POST", f"/api/reports/{wo}/ask",
                             body={"messages": [{"role": "user", "content": "Which test step failed most often, and how many runs?"}],
                                   "lang": "en"})
        reply = next((e.get("reply") for e in ndjson(text if isinstance(text, str) else "") if e.get("type") == "done"), "")
        check("C3", "Follow-up question answered from the facts",
              bool(reply) and (not acceptance or ("SAVE_NVRAM" in reply and "13" in reply)), reply[:160])
    else:
        check("C1", "AI report checks (needs --llm)", None)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://localhost:8003")
    ap.add_argument("--db-container")
    ap.add_argument("--admin", help="user:password")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--json")
    args = ap.parse_args()
    try:
        run(args)
    except Exception as exc:          # a crash is a failed check, not a silent pass
        check("--", "regression run aborted", False, repr(exc))
    counts = {k: sum(r["status"] == k for r in RESULTS) for k in ("PASS", "FAIL", "SKIP")}
    print(f"\n{counts['PASS']} passed, {counts['FAIL']} failed, {counts['SKIP']} skipped")
    if args.json:
        Path(args.json).write_text(json.dumps({"base": args.base, "counts": counts, "results": RESULTS},
                                              ensure_ascii=False, indent=1))
    sys.exit(1 if counts["FAIL"] else 0)


if __name__ == "__main__":
    main()
