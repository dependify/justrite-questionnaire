#!/usr/bin/env python3
"""Justrite Stakeholder Questionnaire server.

Serves the questionnaire, stores every submitted response in SQLite and
provides a token-protected admin API for the coordinator.

No third-party packages. Python 3.11+.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import secrets
import sqlite3
import sys
import tempfile
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE_DIR / "data"))).resolve()
DB_PATH = DATA_DIR / "questionnaire.db"
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "").strip()
PORT = int(os.environ.get("PORT", "8000"))
MAX_BODY = int(os.environ.get("MAX_BODY_BYTES", "2000000"))
RATE_LIMIT = int(os.environ.get("RATE_LIMIT_PER_MIN", "20"))

if not ADMIN_TOKEN:
    print(
        "ADMIN_TOKEN is required. Set it to a long random value before starting.\n"
        "Example: ADMIN_TOKEN=$(openssl rand -hex 24) python server.py",
        file=sys.stderr,
    )
    raise SystemExit(1)

try:
    META = json.loads((BASE_DIR / "meta.json").read_text(encoding="utf-8"))
except Exception as exc:
    print(f"meta.json could not be loaded: {exc}", file=sys.stderr)
    raise SystemExit(1)

SECTIONS = META["sections"]
SECTION_BY_CODE = {s["code"]: s for s in SECTIONS}
SECTION_ORDER = [s["code"] for s in SECTIONS]
QUESTION_BY_ID = {q["id"]: q for s in SECTIONS for q in s["questions"]}
PRIORITY_BY_SECTION = {
    s["code"]: [q["id"] for q in s["questions"] if q.get("pri")] for s in SECTIONS
}
MARKER_LABELS = {"dont-know": "Don't know", "no-rule": "No rule yet", "na": "Not applicable"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("questionnaire")

DATA_DIR.mkdir(parents=True, exist_ok=True)
write_lock = threading.Lock()
rate_lock = threading.Lock()
post_hits: dict[str, deque] = defaultdict(deque)

CSP = (
    "default-src 'self' blob:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'self'; "
    "base-uri 'none'; "
    "object-src 'none'"
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS responses (
                id TEXT PRIMARY KEY,
                response_id TEXT NOT NULL UNIQUE,
                received_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                respondent_name TEXT NOT NULL DEFAULT '',
                respondent_role TEXT NOT NULL DEFAULT '',
                respondent_store TEXT NOT NULL DEFAULT '',
                respondent_phone TEXT NOT NULL DEFAULT '',
                respondent_email TEXT NOT NULL DEFAULT '',
                sections_json TEXT NOT NULL,
                answers_json TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                answered_count INTEGER NOT NULL DEFAULT 0,
                priority_missing INTEGER NOT NULL DEFAULT 0,
                signed INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_responses_received ON responses(received_at DESC);
            """
        )
    log.info("database ready at %s", DB_PATH)


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def clean_str(value, limit: int) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        value = "true" if value else "false"
    if not isinstance(value, (str, int, float)):
        return ""
    return str(value).strip()[:limit]


def validate_payload(payload) -> dict:
    if not isinstance(payload, dict):
        raise ApiError(400, "The request body must be a JSON object.")
    if payload.get("type") != "justrite-questionnaire-response":
        raise ApiError(400, "This is not a questionnaire response.")

    respondent = payload.get("respondent")
    if not isinstance(respondent, dict):
        raise ApiError(400, "Respondent details are missing.")
    name = clean_str(respondent.get("name"), 200)
    if not name:
        raise ApiError(400, "The respondent's name is required.")

    raw_sections = payload.get("sections")
    if not isinstance(raw_sections, list):
        raise ApiError(400, "Sections are missing.")
    sections: list[str] = []
    for item in raw_sections:
        code = clean_str(item, 10).upper()
        if code in SECTION_BY_CODE and code not in sections:
            sections.append(code)
    if not sections:
        raise ApiError(400, "At least one valid section is required.")

    raw_answers = payload.get("answers")
    if not isinstance(raw_answers, dict):
        raise ApiError(400, "Answers are missing.")
    answers: dict[str, dict] = {}
    for qid, answer in raw_answers.items():
        if not isinstance(qid, str) or qid not in QUESTION_BY_ID:
            continue
        q_section = qid.split("-", 1)[0]
        if q_section not in sections or not isinstance(answer, dict):
            continue
        value = answer.get("value", "")
        if isinstance(value, list):
            value = [clean_str(v, 5000) for v in value[:60] if clean_str(v, 5000)]
        else:
            value = clean_str(value, 20000)
        other = clean_str(answer.get("other"), 2000)
        note = clean_str(answer.get("note"), 5000)
        who = clean_str(answer.get("who"), 500)
        marker = clean_str(answer.get("marker"), 30)
        if marker not in MARKER_LABELS:
            marker = ""
        if not value and not other and not note and not who and not marker:
            continue
        answers[qid] = {"value": value, "other": other, "note": note, "who": who, "marker": marker}

    if not answers:
        raise ApiError(400, "At least one answered question is required.")

    signoff = payload.get("signoff") if isinstance(payload.get("signoff"), dict) else {}
    if not signoff.get("confirmed"):
        raise ApiError(400, "The sign-off confirmation is required before submitting.")
    signoff_name = clean_str(signoff.get("name"), 200) or name
    signoff_role = clean_str(signoff.get("role"), 200) or clean_str(respondent.get("role"), 200)
    signoff_date = clean_str(signoff.get("date"), 40)

    extras = payload.get("extras") if isinstance(payload.get("extras"), dict) else {}
    priority_missing = sum(
        1
        for code in sections
        for qid in PRIORITY_BY_SECTION.get(code, [])
        if qid not in answers
    )
    total_questions = sum(len(SECTION_BY_CODE[code]["questions"]) for code in sections)

    normalized = {
        "type": "justrite-questionnaire-response",
        "version": 1,
        "app": "Justrite Stakeholder Questionnaire",
        "responseId": clean_str(payload.get("responseId"), 100) or ("srv-" + uuid.uuid4().hex),
        "generatedAt": clean_str(payload.get("generatedAt"), 40),
        "submittedAt": clean_str(payload.get("submittedAt"), 40),
        "startedAt": clean_str(payload.get("startedAt"), 40),
        "respondent": {
            "name": name,
            "role": clean_str(respondent.get("role"), 200),
            "store": clean_str(respondent.get("store"), 200),
            "phone": clean_str(respondent.get("phone"), 100),
            "email": clean_str(respondent.get("email"), 300),
            "helpedBy": clean_str(respondent.get("helpedBy"), 500),
            "returnBy": clean_str(respondent.get("returnBy"), 40),
        },
        "sections": sections,
        "answers": answers,
        "extras": extras,
        "signoff": {
            "name": signoff_name,
            "role": signoff_role,
            "date": signoff_date,
            "confirmed": True,
        },
        "stats": {"answered": len(answers), "total": total_questions, "priorityMissing": priority_missing},
        "contact": {
            "name": "Tolulope Omotosho",
            "phone": "08060596697",
            "whatsapp": "2348060596697",
            "email": "OTolulope@dependifyllc.com",
        },
    }
    encoded = json.dumps(normalized, ensure_ascii=False).encode("utf-8")
    if len(encoded) > MAX_BODY:
        raise ApiError(413, "The response is too large.")
    return normalized


def save_response(payload: dict) -> dict:
    rid = payload["responseId"]
    sections_json = json.dumps(payload["sections"], ensure_ascii=False)
    answers_json = json.dumps(payload["answers"], ensure_ascii=False)
    payload_json = json.dumps(payload, ensure_ascii=False)
    respondent = payload["respondent"]
    answered = len(payload["answers"])
    priority_missing = payload["stats"]["priorityMissing"]
    signed = 1 if payload["signoff"]["confirmed"] else 0
    ts = now_iso()

    with write_lock:
        conn = connect()
        try:
            row = conn.execute(
                "SELECT id, version, received_at FROM responses WHERE response_id = ?", (rid,)
            ).fetchone()
            if row:
                response_id = row["id"]
                version = int(row["version"]) + 1
                received_at = row["received_at"]
                created = False
                conn.execute(
                    """
                    UPDATE responses SET
                        updated_at = ?, version = ?,
                        respondent_name = ?, respondent_role = ?, respondent_store = ?,
                        respondent_phone = ?, respondent_email = ?,
                        sections_json = ?, answers_json = ?, payload_json = ?,
                        answered_count = ?, priority_missing = ?, signed = ?
                    WHERE id = ?
                    """,
                    (
                        ts,
                        version,
                        respondent["name"],
                        respondent["role"],
                        respondent["store"],
                        respondent["phone"],
                        respondent["email"],
                        sections_json,
                        answers_json,
                        payload_json,
                        answered,
                        priority_missing,
                        signed,
                        response_id,
                    ),
                )
            else:
                response_id = uuid.uuid4().hex
                version = 1
                received_at = ts
                created = True
                conn.execute(
                    """
                    INSERT INTO responses (
                        id, response_id, received_at, updated_at, version,
                        respondent_name, respondent_role, respondent_store,
                        respondent_phone, respondent_email, sections_json,
                        answers_json, payload_json, answered_count,
                        priority_missing, signed
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        response_id,
                        rid,
                        received_at,
                        ts,
                        version,
                        respondent["name"],
                        respondent["role"],
                        respondent["store"],
                        respondent["phone"],
                        respondent["email"],
                        sections_json,
                        answers_json,
                        payload_json,
                        answered,
                        priority_missing,
                        signed,
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    return {
        "ok": True,
        "id": response_id,
        "response_id": rid,
        "created": created,
        "version": version,
        "saved_at": ts,
        "answered_count": answered,
        "priority_missing": priority_missing,
    }


def list_responses() -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute("SELECT * FROM responses ORDER BY received_at DESC").fetchall()
    finally:
        conn.close()
    result = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except Exception:
            payload = {}
        result.append(
            {
                "id": row["id"],
                "responseId": row["response_id"],
                "receivedAt": row["received_at"],
                "updatedAt": row["updated_at"],
                "version": row["version"],
                "answeredCount": row["answered_count"],
                "priorityMissing": row["priority_missing"],
                "signed": bool(row["signed"]),
                "payload": payload,
            }
        )
    return result


def delete_response(response_id: str) -> bool:
    with write_lock:
        conn = connect()
        try:
            cur = conn.execute("DELETE FROM responses WHERE id = ?", (response_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def answer_text(answer: dict) -> str:
    parts: list[str] = []
    marker = answer.get("marker")
    if marker:
        parts.append(f"[{MARKER_LABELS.get(marker, marker)}]")
    value = answer.get("value")
    if isinstance(value, list):
        value = "; ".join(str(v) for v in value if v)
    if value:
        parts.append(str(value))
    if answer.get("other"):
        parts.append(f"Other: {answer['other']}")
    if answer.get("note"):
        parts.append(f"Note: {answer['note']}")
    if answer.get("who"):
        parts.append(f"Who can answer: {answer['who']}")
    return " — ".join(parts)


def csv_safe(value) -> str:
    text = str(value if value is not None else "")
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def build_csv(responses: list[dict]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "Server ID",
            "Received",
            "Updated",
            "Version",
            "Respondent",
            "Role",
            "Store",
            "Phone",
            "Email",
            "Sections",
            "Signed",
            "Section",
            "Question ID",
            "Priority",
            "Question",
            "Answer",
            "Marker",
            "Who knows",
        ]
    )
    for response in responses:
        payload = response["payload"]
        respondent = payload.get("respondent", {})
        signoff = payload.get("signoff", {})
        answers = payload.get("answers", {})
        for code in payload.get("sections", []):
            section = SECTION_BY_CODE.get(code)
            if not section:
                continue
            for question in section["questions"]:
                qid = question["id"]
                if qid not in answers:
                    continue
                answer = answers[qid]
                row = [
                    response["id"],
                    response["receivedAt"],
                    response["updatedAt"],
                    response["version"],
                    respondent.get("name", ""),
                    respondent.get("role", ""),
                    respondent.get("store", ""),
                    respondent.get("phone", ""),
                    respondent.get("email", ""),
                    ", ".join(payload.get("sections", [])),
                    "Yes" if signoff.get("confirmed") else "No",
                    code,
                    qid,
                    "Priority" if question.get("pri") else "",
                    question.get("q", ""),
                    answer_text(answer),
                    answer.get("marker", ""),
                    answer.get("who", ""),
                ]
                writer.writerow([csv_safe(v) for v in row])
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def backup_db() -> bytes:
    fd, temp_path = tempfile.mkstemp(prefix="questionnaire-", suffix=".db", dir=str(DATA_DIR))
    os.close(fd)
    try:
        source = connect()
        target = sqlite3.connect(temp_path)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        return Path(temp_path).read_bytes()
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


def allow_post(ip: str) -> bool:
    if RATE_LIMIT <= 0:
        return True
    now = time.monotonic()
    with rate_lock:
        bucket = post_hits[ip]
        while bucket and now - bucket[0] > 60:
            bucket.popleft()
        if len(bucket) >= RATE_LIMIT:
            return False
        bucket.append(now)
        return True


class Handler(BaseHTTPRequestHandler):
    server_version = "JustriteQuestionnaire/1.0"
    protocol_version = "HTTP/1.1"

    # ---------- response helpers ----------
    def base_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "microphone=(self)")
        self.send_header("Content-Security-Policy", CSP)

    def send_json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.base_headers()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_bytes(self, body: bytes, content_type: str, filename: str | None = None) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.base_headers()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_html(self, filename: str) -> None:
        path = BASE_DIR / filename
        if not path.is_file():
            raise ApiError(500, "The application file is missing.")
        self.send_bytes(path.read_bytes(), "text/html; charset=utf-8")

    def require_admin(self) -> None:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise ApiError(401, "Unauthorized")
        token = header[7:].strip()
        if not secrets.compare_digest(token, ADMIN_TOKEN):
            raise ApiError(401, "Unauthorized")

    def read_json(self):
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError:
            length = 0
        if length <= 0:
            raise ApiError(411, "Content-Length is required.")
        if length > MAX_BODY:
            self.close_connection = True
            raise ApiError(413, "The request body is too large.")
        body = self.rfile.read(length)
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(400, "The request body is not valid JSON.")

    # ---------- routing ----------
    def do_GET(self):
        try:
            self.route_get()
        except ApiError as exc:
            self.send_json({"detail": exc.detail}, exc.status)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            log.exception("GET failed")
            try:
                self.send_json({"detail": "Server error"}, 500)
            except Exception:
                pass

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        try:
            self.route_post()
        except ApiError as exc:
            self.send_json({"detail": exc.detail}, exc.status)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            log.exception("POST failed")
            try:
                self.send_json({"detail": "Server error"}, 500)
            except Exception:
                pass

    def do_DELETE(self):
        try:
            self.route_delete()
        except ApiError as exc:
            self.send_json({"detail": exc.detail}, exc.status)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            log.exception("DELETE failed")
            try:
                self.send_json({"detail": "Server error"}, 500)
            except Exception:
                pass

    def route_get(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self.send_html("index.html")
            return
        if path in ("/admin", "/admin/", "/admin.html"):
            self.send_html("admin.html")
            return
        if path in ("/api/health", "/health", "/healthz"):
            try:
                conn = connect()
                try:
                    conn.execute("SELECT 1").fetchone()
                finally:
                    conn.close()
            except Exception:
                raise ApiError(503, "Database unavailable")
            self.send_json({"ok": True, "service": "justrite-questionnaire"})
            return
        if path == "/api/meta":
            self.send_json(META)
            return
        if path == "/api/admin/responses":
            self.require_admin()
            responses = list_responses()
            self.send_json({"ok": True, "count": len(responses), "responses": responses})
            return
        if path == "/api/admin/export.json":
            self.require_admin()
            responses = list_responses()
            exported = {
                "type": "justrite-questionnaire-batch",
                "version": 1,
                "exportedAt": now_iso(),
                "responses": [
                    dict(
                        r["payload"],
                        _server={
                            "id": r["id"],
                            "receivedAt": r["receivedAt"],
                            "updatedAt": r["updatedAt"],
                            "version": r["version"],
                        },
                    )
                    for r in responses
                ],
            }
            self.send_bytes(
                json.dumps(exported, ensure_ascii=False, indent=2).encode("utf-8"),
                "application/json; charset=utf-8",
                f"justrite-questionnaire_{datetime.now().strftime('%Y-%m-%d')}.json",
            )
            return
        if path == "/api/admin/export.csv":
            self.require_admin()
            body = build_csv(list_responses())
            self.send_bytes(
                body,
                "text/csv; charset=utf-8",
                f"justrite-questionnaire_{datetime.now().strftime('%Y-%m-%d')}.csv",
            )
            return
        if path == "/api/admin/backup.db":
            self.require_admin()
            body = backup_db()
            self.send_bytes(
                body,
                "application/vnd.sqlite3",
                f"questionnaire-backup_{datetime.now().strftime('%Y-%m-%d_%H%M')}.db",
            )
            return
        if path == "/robots.txt":
            self.send_bytes(b"User-agent: *\nDisallow: /\n", "text/plain; charset=utf-8")
            return
        if path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.base_headers()
            self.end_headers()
            return
        raise ApiError(404, "Not found")

    def route_post(self):
        path = urlparse(self.path).path
        if path != "/api/responses":
            raise ApiError(404, "Not found")
        forwarded = self.headers.get("X-Forwarded-For", "")
        client_ip = forwarded.split(",")[0].strip() if forwarded else (self.client_address[0] if self.client_address else "unknown")
        if not allow_post(client_ip):
            raise ApiError(429, "Too many submissions. Try again in a minute.")
        payload = self.read_json()
        normalized = validate_payload(payload)
        result = save_response(normalized)
        log.info(
            "saved response %s (%s) for %s — %s answered, %s priority open",
            result["id"],
            "new" if result["created"] else f"v{result['version']}",
            normalized["respondent"]["name"],
            result["answered_count"],
            result["priority_missing"],
        )
        self.send_json(result)

    def route_delete(self):
        path = urlparse(self.path).path
        prefix = "/api/admin/responses/"
        if not path.startswith(prefix):
            raise ApiError(404, "Not found")
        self.require_admin()
        response_id = unquote(path[len(prefix):]).strip()
        if not response_id or "/" in response_id:
            raise ApiError(400, "Invalid response id.")
        if not delete_response(response_id):
            raise ApiError(404, "Response not found.")
        log.info("deleted response %s", response_id)
        self.send_json({"ok": True, "deleted": response_id})

    def log_message(self, fmt, *args):
        request_line = str(args[0]) if args else ""
        if "/api/health" in request_line or "/health" in request_line:
            return
        log.info("%s - %s", self.address_string(), fmt % args)


def main():
    init_db()
    if len(ADMIN_TOKEN) < 16:
        log.warning("ADMIN_TOKEN is short. Use at least 24 random characters in production.")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    log.info(
        "Justrite questionnaire listening on 0.0.0.0:%s (data: %s, uid: %s, admin token: configured)",
        PORT,
        DB_PATH,
        os.geteuid() if hasattr(os, "geteuid") else "n/a",
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
