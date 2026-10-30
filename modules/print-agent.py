#!/usr/bin/env python3
"""HTTP control API for the IEEE TAMU officer lounge printer."""

import hmac
import json
import os
import re
import subprocess
import tempfile
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PRINTER_NAME = os.environ.get("PRINTER_NAME", "Brother_MFC-7360N")
BIND_ADDRESS = os.environ.get("BIND_ADDRESS", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8788"))
TOKEN_FILE = os.environ.get("TOKEN_FILE", "")
MAX_FILE_BYTES = int(os.environ.get("MAX_FILE_BYTES", str(10 * 1024 * 1024)))
MAX_COPIES = int(os.environ.get("MAX_COPIES", "20"))
DEFAULT_LP_OPTIONS = [
    option.strip()
    for option in os.environ.get("DEFAULT_LP_OPTIONS", "media=Letter").split(",")
    if option.strip()
]
PAGE_RANGE_RE = re.compile(r"^\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*$")
JOB_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+-\d+$")
MAX_FILENAME_LENGTH = 120
MAX_JSON_BYTES = 65536


class AgentError(RuntimeError):
    """Raised when CUPS cannot be reached or rejects a command."""


class BadRequest(ValueError):
    """Raised for invalid client input."""


def load_token():
    if not TOKEN_FILE:
        return None
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as handle:
            token = handle.read().strip()
    except OSError as error:
        print(
            f"print-agent: cannot read token file {TOKEN_FILE}: {error}",
            flush=True,
        )
        raise SystemExit(1) from error
    if not token:
        print(f"print-agent: token file {TOKEN_FILE} is empty", flush=True)
        raise SystemExit(1)
    return token


TOKEN = load_token()


def run_command(args, timeout=60):
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as error:
        raise AgentError(f"missing command: {args[0]}") from error
    except subprocess.TimeoutExpired as error:
        raise AgentError(f"{args[0]} timed out") from error

    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()
        raise AgentError(message or f"{args[0]} exited with {result.returncode}")
    return result.stdout.strip()


def printer_status():
    try:
        output = run_command(["lpstat", "-p", PRINTER_NAME], timeout=10)
    except AgentError as error:
        return {
            "name": PRINTER_NAME,
            "state": "unknown",
            "message": str(error),
        }

    lowered = output.lower()
    if "disabled since" in lowered or " is stopped" in lowered:
        state = "stopped"
    elif "now printing" in lowered:
        state = "printing"
    elif "is idle" in lowered:
        state = "idle"
    else:
        state = "unknown"

    return {
        "name": PRINTER_NAME,
        "state": state,
        "message": output,
    }


def queued_jobs():
    try:
        output = run_command(
            ["lpstat", "-W", "not-completed", "-o", PRINTER_NAME],
            timeout=10,
        )
    except AgentError:
        return []

    jobs = []
    for line in output.splitlines():
        parts = line.split()
        if not parts:
            continue
        job = {"id": parts[0]}
        if len(parts) > 1:
            job["owner"] = parts[1]
        if len(parts) > 2:
            job["size"] = parts[2]
        jobs.append(job)
    return jobs


def safe_filename(value, fallback):
    name = os.path.basename(value or "").strip()
    name = "".join(
        character
        for character in name
        if character.isprintable() and character not in "\r\n"
    )
    name = name[:MAX_FILENAME_LENGTH].strip()
    return name or fallback


def detect_type(declared, filename, data):
    declared = (declared or "").split(";", 1)[0].strip().lower()
    suffix = os.path.splitext(filename)[1].lower()

    if b"%PDF-" in data[:1024]:
        return "application/pdf", ".pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"

    if declared == "text/plain" or suffix == ".txt":
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise BadRequest("Text files must be valid UTF-8.") from error
        if b"\x00" in data:
            raise BadRequest("Text files must not contain NUL bytes.")
        return "text/plain", ".txt"

    raise BadRequest("Only PDF, PNG, JPEG, and plain-text files are supported.")


def parse_copies(value):
    try:
        copies = int(value)
    except (TypeError, ValueError) as error:
        raise BadRequest("'copies' must be an integer.") from error
    if copies < 1 or copies > MAX_COPIES:
        raise BadRequest(f"'copies' must be between 1 and {MAX_COPIES}.")
    return copies


def status():
    return {
        "ok": True,
        "printer": printer_status(),
        "jobs": queued_jobs(),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "print-agent/0.1"

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def send_json(self, status_code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        if TOKEN is None:
            return True
        header = self.headers.get("Authorization", "")
        return hmac.compare_digest(header, f"Bearer {TOKEN}")

    def read_body(self, limit=MAX_FILE_BYTES):
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError as error:
            raise BadRequest("Invalid Content-Length header.") from error
        if length <= 0:
            raise BadRequest("Request body is required.")
        if length > limit:
            raise BadRequest(f"Request body is too large (limit {limit} bytes).")

        chunks = []
        remaining = length
        while remaining:
            chunk = self.rfile.read(min(remaining, 65536))
            if not chunk:
                raise BadRequest("Request body ended unexpectedly.")
            chunks.append(chunk)
            remaining -= len(chunk)

        body = b"".join(chunks)
        if len(body) > limit:
            raise BadRequest(f"Request body is too large (limit {limit} bytes).")
        return body

    def read_json(self):
        body = self.read_body(limit=MAX_JSON_BYTES)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BadRequest("Request body must be valid JSON.") from error
        if not isinstance(payload, dict):
            raise BadRequest("Request body must be a JSON object.")
        return payload

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/health":
            self.send_json(200, {"ok": True})
            return
        if not self.authorized():
            self.send_json(401, {"ok": False, "error": "unauthorized"})
            return
        if path == "/status":
            self.send_json(200, status())
            return
        self.send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if not self.authorized():
            self.send_json(401, {"ok": False, "error": "unauthorized"})
            return
        try:
            if path == "/print":
                self.handle_print()
            elif path == "/cancel":
                self.handle_cancel()
            else:
                self.send_json(404, {"ok": False, "error": "not found"})
        except BadRequest as error:
            self.send_json(400, {"ok": False, "error": str(error)})
        except AgentError as error:
            self.send_json(503, {"ok": False, "error": str(error)})

    def handle_print(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        filename = safe_filename((query.get("filename") or [""])[0], "print")
        copies = parse_copies((query.get("copies") or ["1"])[0])
        pages = (query.get("pages") or [""])[0].strip()
        if pages and not PAGE_RANGE_RE.fullmatch(pages):
            raise BadRequest("'pages' must look like 1-3,5.")

        data = self.read_body()
        declared = self.headers.get("Content-Type", "")
        _, suffix = detect_type(declared, filename, data)

        handle, path = tempfile.mkstemp(prefix="print-", suffix=suffix)
        try:
            with os.fdopen(handle, "wb") as file:
                file.write(data)

            args = ["lp", "-d", PRINTER_NAME, "-n", str(copies), "-t", filename]
            for option in DEFAULT_LP_OPTIONS:
                args.extend(["-o", option])
            if pages:
                args.extend(["-o", f"page-ranges={pages}"])
            args.append(path)

            output = run_command(args, timeout=60)
            match = re.search(r"request id is (\S+)", output)
            job_id = match.group(1) if match else None

            print(
                f"print-agent: queued {filename!r} ({copies} copy/copies, "
                f"pages={pages or 'all'}) as {job_id or 'unknown job'}",
                flush=True,
            )

            self.send_json(
                200,
                {
                    "ok": True,
                    "jobId": job_id,
                    "printer": PRINTER_NAME,
                    "filename": filename,
                    "copies": copies,
                    "pages": pages or None,
                },
            )
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def handle_cancel(self):
        payload = self.read_json()
        job_id = payload.get("jobId")
        if not isinstance(job_id, str) or not JOB_ID_RE.fullmatch(job_id):
            raise BadRequest("'jobId' must be a valid job ID.")

        run_command(["cancel", job_id], timeout=15)
        print(f"print-agent: cancelled {job_id}", flush=True)
        self.send_json(200, {"ok": True, "jobId": job_id})


def main():
    server = ThreadingHTTPServer((BIND_ADDRESS, PORT), Handler)
    auth = "token required" if TOKEN else "no token configured"
    print(
        f"print-agent: listening on {BIND_ADDRESS}:{PORT} ({auth}), "
        f"printer {PRINTER_NAME}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
