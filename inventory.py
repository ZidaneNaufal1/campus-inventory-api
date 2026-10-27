"""Local-only inventory API. No authentication; do not expose to a network."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sqlite3
import stat
import sys
from urllib.parse import parse_qs, urlsplit

VERSION = "0.1.0"
APP_ID = 1128873558
MAX_BODY = 65536
SCHEMA = """
CREATE TABLE assets (id INTEGER PRIMARY KEY, name TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity BETWEEN 1 AND 100000));
CREATE TABLE borrowers (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE loans (id INTEGER PRIMARY KEY, asset_id INTEGER NOT NULL REFERENCES assets(id), borrower_id INTEGER NOT NULL REFERENCES borrowers(id), borrowed_at TEXT NOT NULL, returned_at TEXT);
CREATE UNIQUE INDEX one_active_loan_per_borrower_asset ON loans(asset_id, borrower_id) WHERE returned_at IS NULL;
"""


class ApiError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message
        super().__init__(message)


def plain_path(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.exists() or part.is_symlink():
            st = part.lstat()
            if stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & 0x400:
                raise ValueError("Symlink or reparse-point database paths are not supported")
    return path


def connect(path, create=False):
    path = plain_path(path)
    new = not path.exists()
    if new and not create:
        raise ValueError("Database does not exist")
    if not new and not path.is_file():
        raise ValueError("Database must be a regular file")
    if new:
        with path.open("xb"):
            pass
    conn = sqlite3.connect(path, timeout=5)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        if new:
            with conn:
                # executescript commits implicitly; new files only, never an existing DB.
                conn.executescript(SCHEMA)
                conn.execute(f"PRAGMA application_id={APP_ID}")
                conn.execute("PRAGMA user_version=1")
        elif conn.execute("PRAGMA application_id").fetchone()[0] != APP_ID or conn.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("Not an inventory database at schema version 1")
        expected = {"assets": ["id", "name", "quantity"], "borrowers": ["id", "name"], "loans": ["id", "asset_id", "borrower_id", "borrowed_at", "returned_at"]}
        for table, fields in expected.items():
            if [r[1] for r in conn.execute(f"PRAGMA table_info({table})")] != fields:
                raise ValueError("Unexpected database schema")
        conn.row_factory = sqlite3.Row
        return conn
    except BaseException:
        conn.close()
        raise


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def valid_name(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 100 or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise ApiError(400, "name must be 1–100 printable characters")
    return value.strip()


def positive(value, maximum=100000):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ApiError(400, "expected a positive integer within the supported range")
    return value


def fields(data, required):
    if not isinstance(data, dict) or set(data) != set(required):
        raise ApiError(400, "request fields do not match the expected schema")


def create_asset(db, data):
    fields(data, {"name", "quantity"})
    name, qty = valid_name(data["name"]), positive(data["quantity"])
    with closing(connect(db)) as conn, conn:
        cur = conn.execute("INSERT INTO assets(name,quantity) VALUES (?,?)", (name, qty))
        return {"id": cur.lastrowid, "name": name, "quantity": qty, "available": qty}


def create_borrower(db, data):
    fields(data, {"name"})
    name = valid_name(data["name"])
    with closing(connect(db)) as conn, conn:
        cur = conn.execute("INSERT INTO borrowers(name) VALUES (?)", (name,))
        return {"id": cur.lastrowid, "name": name}


def borrow(db, data):
    fields(data, {"asset_id", "borrower_id"})
    asset_id = positive(data["asset_id"], 2147483647)
    borrower_id = positive(data["borrower_id"], 2147483647)
    with closing(connect(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        asset = conn.execute("SELECT quantity FROM assets WHERE id=?", (asset_id,)).fetchone()
        borrower = conn.execute("SELECT id FROM borrowers WHERE id=?", (borrower_id,)).fetchone()
        if not asset or not borrower:
            raise ApiError(404, "asset or borrower not found")
        duplicate = conn.execute("SELECT 1 FROM loans WHERE asset_id=? AND borrower_id=? AND returned_at IS NULL", (asset_id, borrower_id)).fetchone()
        if duplicate:
            raise ApiError(409, "borrower already has an active loan for this asset")
        active_count = conn.execute("SELECT COUNT(*) FROM loans WHERE asset_id=? AND returned_at IS NULL", (asset_id,)).fetchone()[0]
        if active_count >= asset["quantity"]:
            raise ApiError(409, "asset is unavailable")
        stamp = now()
        cur = conn.execute("INSERT INTO loans(asset_id,borrower_id,borrowed_at) VALUES (?,?,?)", (asset_id, borrower_id, stamp))
        return {"id": cur.lastrowid, "asset_id": asset_id, "borrower_id": borrower_id, "borrowed_at": stamp, "returned_at": None}


def return_loan(db, loan_id):
    positive(loan_id, 2147483647)
    with closing(connect(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM loans WHERE id=?", (loan_id,)).fetchone()
        if row is None:
            raise ApiError(404, "loan not found")
        if row["returned_at"] is not None:
            raise ApiError(409, "loan was already returned")
        stamp = now()
        conn.execute("UPDATE loans SET returned_at=? WHERE id=? AND returned_at IS NULL", (stamp, loan_id))
        result = dict(row)
        result["returned_at"] = stamp
        return result


def list_items(db, kind, query):
    allowed = {"assets": {"available"}, "borrowers": set(), "loans": {"status"}}[kind]
    if set(query) - allowed or any(len(v) != 1 for v in query.values()):
        raise ApiError(400, "unknown or repeated query parameter")
    with closing(connect(db)) as conn:
        if kind == "assets":
            available = query.get("available", [None])[0]
            if available not in (None, "true", "false"):
                raise ApiError(400, "available must be true or false")
            having = {None: "", "true": " HAVING a.quantity-COUNT(l.id)>0", "false": " HAVING a.quantity-COUNT(l.id)<=0"}[available]
            rows = conn.execute("SELECT a.id,a.name,a.quantity,a.quantity-COUNT(l.id) AS available FROM assets a LEFT JOIN loans l ON l.asset_id=a.id AND l.returned_at IS NULL GROUP BY a.id" + having + " ORDER BY a.id LIMIT 1000").fetchall()
            return [dict(r) for r in rows]
        if kind == "borrowers":
            return [dict(r) for r in conn.execute("SELECT id,name FROM borrowers ORDER BY id LIMIT 1000")]
        status = query.get("status", [None])[0]
        if status not in (None, "active", "returned"):
            raise ApiError(400, "status must be active or returned")
        suffix = {None: "", "active": " WHERE returned_at IS NULL", "returned": " WHERE returned_at IS NOT NULL"}[status]
        return [dict(r) for r in conn.execute("SELECT id,asset_id,borrower_id,borrowed_at,returned_at FROM loans" + suffix + " ORDER BY id LIMIT 1000")]


def no_duplicates(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate JSON key")
        obj[key] = value
    return obj


def parse_body(raw):
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=no_duplicates, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, ValueError, RecursionError):
        raise ApiError(400, "invalid JSON") from None


def make_server(db, port=8768):
    db = plain_path(db)
    with closing(connect(db, create=True)):
        pass

    class Handler(BaseHTTPRequestHandler):
        server_version = "CampusInventory/" + VERSION
        sys_version = ""

        def setup(self):
            super().setup()
            self.connection.settimeout(3)

        def log_message(self, *args):
            pass

        def respond(self, status, data):
            raw = json.dumps(data, ensure_ascii=True).encode("utf-8")
            self.close_connection = True
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(raw)
            except OSError:
                pass

        def do_GET(self):
            parsed = urlsplit(self.path)
            kind = parsed.path.lstrip("/")
            try:
                if kind == "openapi.json" and not parsed.query:
                    spec = json.loads((Path(__file__).parent / "openapi.json").read_text(encoding="utf-8"))
                    return self.respond(200, spec)
                if kind not in {"assets", "borrowers", "loans"} or parsed.path != "/" + kind:
                    raise ApiError(404, "not found")
                query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=10)
                return self.respond(200, list_items(db, kind, query))
            except ApiError as e:
                return self.respond(e.status, {"error": e.message})
            except ValueError:
                return self.respond(400, {"error": "invalid query or database"})
            except (OSError, sqlite3.Error):
                return self.respond(503, {"error": "storage unavailable"})

        def do_POST(self):
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,6}", lengths[0]):
                return self.respond(400, {"error": "one valid Content-Length is required"})
            size = int(lengths[0])
            if size > MAX_BODY:
                return self.respond(413, {"error": "body too large"})
            if size < 1:
                return self.respond(400, {"error": "body required"})
            try:
                raw = self.rfile.read(size)
                if len(raw) != size:
                    return self.respond(400, {"error": "incomplete body"})
            except OSError:
                return self.respond(408, {"error": "body read timeout"})
            if self.headers.get_all("Transfer-Encoding") or self.headers.get_all("Content-Encoding"):
                return self.respond(400, {"error": "unsupported encoding"})
            if self.headers.get_all("Content-Type", []) != ["application/json"]:
                return self.respond(415, {"error": "Content-Type must be application/json"})
            try:
                data = parse_body(raw)
                path = self.path
                if path == "/assets":
                    return self.respond(201, create_asset(db, data))
                if path == "/borrowers":
                    return self.respond(201, create_borrower(db, data))
                if path == "/loans":
                    return self.respond(201, borrow(db, data))
                match = re.fullmatch(r"/loans/([1-9][0-9]{0,9})/return", path)
                if match:
                    fields(data, set())
                    return self.respond(200, return_loan(db, int(match.group(1))))
                raise ApiError(404, "not found")
            except ApiError as e:
                return self.respond(e.status, {"error": e.message})
            except (OSError, ValueError, sqlite3.Error):
                return self.respond(503, {"error": "storage unavailable"})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local campus inventory MVP")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--version", action="version", version=VERSION)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be 1–65535")
    try:
        server = make_server(args.db, args.port)
        print(f"Local only: http://127.0.0.1:{args.port} (Ctrl+C to stop)", flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, sqlite3.Error):
        print("Cannot start: database or local port unavailable", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
