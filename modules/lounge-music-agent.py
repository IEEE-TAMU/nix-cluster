#!/usr/bin/env python3
"""HTTP control API for the IEEE TAMU officer lounge music player."""

import hmac
import json
import os
import socket
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MPV_SOCKET = os.environ.get("MPV_SOCKET", "/run/lounge-music/mpv.sock")
BIND_ADDRESS = os.environ.get("BIND_ADDRESS", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8787"))
TOKEN_FILE = os.environ.get("TOKEN_FILE", "")
MAX_QUEUE_LENGTH = int(os.environ.get("MAX_QUEUE_LENGTH", "25"))
DEFAULT_ALLOWED_HOSTS = ",".join(
    [
        "youtube.com",
        "youtu.be",
        "youtube-nocookie.com",
        "soundcloud.com",
        "bandcamp.com",
    ]
)
ALLOWED_HOSTS = [
    host.strip().lower().rstrip(".")
    for host in os.environ.get("ALLOWED_HOSTS", DEFAULT_ALLOWED_HOSTS).split(",")
    if host.strip()
]
MAX_INPUT_LENGTH = 2048
MAX_BODY_BYTES = 65536

_request_lock = threading.Lock()
_request_id = 0


class MpvError(RuntimeError):
    """Raised when mpv cannot be reached or rejects a command."""


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
            f"lounge-music-agent: cannot read token file {TOKEN_FILE}: {error}",
            flush=True,
        )
        raise SystemExit(1) from error
    if not token:
        print(
            f"lounge-music-agent: token file {TOKEN_FILE} is empty",
            flush=True,
        )
        raise SystemExit(1)
    return token


TOKEN = load_token()


def next_request_id():
    global _request_id
    with _request_lock:
        _request_id += 1
        return _request_id


def mpv_command(*args):
    request_id = next_request_id()
    payload = json.dumps({"command": list(args), "request_id": request_id}) + "\n"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(10)
            sock.connect(MPV_SOCKET)
            sock.sendall(payload.encode("utf-8"))
            buffer = b""
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    raise MpvError("mpv closed the IPC connection")
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        message = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if message.get("request_id") != request_id:
                        continue
                    if message.get("error") != "success":
                        raise MpvError(str(message.get("error", "unknown mpv error")))
                    return message.get("data")
    except OSError as error:
        raise MpvError(f"cannot talk to mpv: {error}") from error


def get_property(name, default=None):
    try:
        value = mpv_command("get_property", name)
    except MpvError:
        return default
    return default if value is None else value


def status():
    playlist = get_property("playlist", []) or []
    idle = bool(mpv_command("get_property", "idle-active"))
    paused = bool(get_property("pause", False))
    volume = get_property("volume", 100)

    entries = []
    for entry in playlist:
        if isinstance(entry, dict):
            entries.append(
                {
                    "title": entry.get("filename") or entry.get("title"),
                    "current": bool(entry.get("current")),
                }
            )

    return {
        "ok": True,
        "idle": idle,
        "paused": paused,
        "playing": not idle and not paused,
        "title": get_property("media-title"),
        "volume": int(round(volume)) if isinstance(volume, (int, float)) else None,
        "position": get_property("time-pos"),
        "duration": get_property("duration"),
        "queuePosition": get_property("playlist-pos"),
        "queueCount": len(playlist),
        "queue": entries,
    }


def host_allowed(host):
    return any(host == allowed or host.endswith("." + allowed) for allowed in ALLOWED_HOSTS)


def resolve_input(value):
    if value.startswith("http://") or value.startswith("https://"):
        parsed = urllib.parse.urlparse(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        if host in {"open.spotify.com", "spotify.link"}:
            raise BadRequest(
                "Spotify links are not supported yet; use a YouTube link or search text."
            )
        if not host:
            raise BadRequest("Could not determine the URL host.")
        if "@" in parsed.netloc or "\\" in parsed.netloc:
            raise BadRequest("Media URLs must not contain userinfo.")
        try:
            port = parsed.port
        except ValueError as error:
            raise BadRequest("Invalid port in media URL.") from error
        if port not in (None, 80, 443):
            raise BadRequest("Media URLs must use port 80 or 443.")
        if not host_allowed(host):
            raise BadRequest(f"{host} is not an allowed media host.")
        return value
    if "://" in value:
        raise BadRequest("Only http(s) URLs or search text are supported.")
    return "ytdl://ytsearch1:" + value


class Handler(BaseHTTPRequestHandler):
    server_version = "lounge-music/0.1"

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

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError as error:
            raise BadRequest("Invalid Content-Length header.") from error
        if length > MAX_BODY_BYTES:
            raise BadRequest("Request body is too large.")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
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
            try:
                self.send_json(200, status())
            except MpvError as error:
                self.send_json(503, {"ok": False, "error": str(error)})
            return
        self.send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if not self.authorized():
            self.send_json(401, {"ok": False, "error": "unauthorized"})
            return
        try:
            body = self.read_json()
            if path == "/play":
                self.handle_play(body)
            elif path == "/pause":
                mpv_command("set_property", "pause", True)
                self.send_json(200, status())
            elif path == "/resume":
                mpv_command("set_property", "pause", False)
                self.send_json(200, status())
            elif path == "/skip":
                mpv_command("playlist-next", "force")
                self.send_json(200, status())
            elif path == "/stop":
                mpv_command("stop")
                mpv_command("playlist-clear")
                self.send_json(200, status())
            else:
                self.send_json(404, {"ok": False, "error": "not found"})
        except BadRequest as error:
            self.send_json(400, {"ok": False, "error": str(error)})
        except MpvError as error:
            self.send_json(503, {"ok": False, "error": str(error)})

    def handle_play(self, body):
        value = body.get("input")
        if not isinstance(value, str) or not value.strip():
            raise BadRequest("'input' must be a non-empty string.")
        value = value.strip()
        if len(value) > MAX_INPUT_LENGTH:
            raise BadRequest(
                f"'input' must be at most {MAX_INPUT_LENGTH} characters."
            )

        current = status()
        if current["queueCount"] >= MAX_QUEUE_LENGTH:
            raise BadRequest(f"The queue is full ({MAX_QUEUE_LENGTH} tracks).")

        target = resolve_input(value)
        requested_by = body.get("requestedBy")
        mpv_command("loadfile", target, "append-play")
        print(
            f"lounge-music-agent: queued {value!r} requested by "
            f"{requested_by or 'unknown'}",
            flush=True,
        )

        result = status()
        result["queued"] = value
        self.send_json(200, result)


def main():
    server = ThreadingHTTPServer((BIND_ADDRESS, PORT), Handler)
    auth = "token required" if TOKEN else "no token configured"
    print(
        f"lounge-music-agent: listening on {BIND_ADDRESS}:{PORT} ({auth}), "
        f"mpv socket {MPV_SOCKET}",
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
