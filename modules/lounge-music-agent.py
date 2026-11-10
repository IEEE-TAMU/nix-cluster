#!/usr/bin/env python3
"""HTTP control API for the IEEE TAMU officer lounge music player."""

import base64
import copy
import errno
import fcntl
import hashlib
import hmac
import json
import os
import queue
import re
import secrets
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.umask(0o077)

MPV_SOCKET = os.environ.get("MPV_SOCKET", "/run/lounge-music/mpv.sock")
BIND_ADDRESS = os.environ.get("BIND_ADDRESS", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8787"))
TOKEN_FILE = os.environ.get("TOKEN_FILE", "")
MAX_QUEUE_LENGTH = int(os.environ.get("MAX_QUEUE_LENGTH", "25"))
MAX_VOLUME = int(os.environ.get("MAX_VOLUME", "100"))
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

STATE_DIR = (
    os.environ.get("STATE_DIRECTORY", "/var/lib/lounge-music").split(":")[0]
    or "/var/lib/lounge-music"
)
CACHE_DIR = (
    os.environ.get("CACHE_DIRECTORY", "/var/cache/lounge-music").split(":")[0]
    or "/var/cache/lounge-music"
)

PLAYER_STATE_PATH = os.path.join(STATE_DIR, "player-state.json")

SPOTIFY_ENABLED = os.environ.get("SPOTIFY_ENABLED", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
SPOTIFY_REDIRECT_URI = os.environ.get("SPOTIFY_REDIRECT_URI", "").strip()
SPOTIFY_DEVICE_NAME = (
    os.environ.get("SPOTIFY_DEVICE_NAME", "IEEE TAMU Lounge").strip()
    or "IEEE TAMU Lounge"
)
try:
    SPOTIFY_OAUTH_PORT = int(os.environ.get("SPOTIFY_OAUTH_PORT", "5588"))
except ValueError:
    SPOTIFY_OAUTH_PORT = 5588
if not 1 <= SPOTIFY_OAUTH_PORT <= 65535:
    SPOTIFY_OAUTH_PORT = 5588
try:
    SPOTIFY_BITRATE = int(os.environ.get("SPOTIFY_BITRATE", "320"))
except ValueError:
    SPOTIFY_BITRATE = 320
if SPOTIFY_BITRATE not in (96, 160, 320):
    SPOTIFY_BITRATE = 320
LIBRESPOT_BIN = os.environ.get("LIBRESPOT_BIN", "librespot").strip()

SPOTIFY_DIR = os.path.join(STATE_DIR, "spotify")
SPOTIFY_CREDENTIALS_PATH = os.path.join(SPOTIFY_DIR, "credentials.json")
SPOTIFY_PENDING_PATH = os.path.join(SPOTIFY_DIR, "pending.json")
SPOTIFY_LIBRESPOT_DIR = os.path.join(SPOTIFY_DIR, "librespot")
SPOTIFY_LIBRESPOT_CREDENTIALS_PATH = os.path.join(
    SPOTIFY_LIBRESPOT_DIR, "credentials.json"
)
SPOTIFY_FIFO_PATH = os.path.join(CACHE_DIR, "spotify-audio.fifo")

SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_URL = "https://api.spotify.com/v1"
SPOTIFY_SCOPES = [
    "streaming",
    "user-read-email",
    "user-read-private",
    "user-read-playback-state",
    "user-modify-playback-state",
    "user-read-currently-playing",
    "playlist-read-private",
    "playlist-read-collaborative",
]

WAV_HEADER = struct.pack(
    "<4sI4s4sIHHIIHH4sI",
    b"RIFF",
    0xFFFFFFFF,
    b"WAVE",
    b"fmt ",
    16,
    1,
    2,
    44100,
    44100 * 2 * 2,
    4,
    16,
    b"data",
    0xFFFFFFFF,
)

_request_lock = threading.Lock()
_request_id = 0
_player_lock = threading.RLock()
_token_lock = threading.Lock()

_current_source = "mpv"
_mpv_volume = 100
_spotify_volume = 100
_spotify_muted = False
_spotify_volume_before_mute = 100
_mpv_queue = None
_spotify_stream_active = False
SPOTIFY = None


class MpvError(RuntimeError):
    """Raised when mpv cannot be reached or rejects a command."""


class BadRequest(ValueError):
    """Raised for invalid client input."""


class SpotifyError(RuntimeError):
    """Base error for Spotify integration failures."""

    status = 503


class SpotifyNotConfigured(SpotifyError):
    status = 400


class SpotifyNotLinked(SpotifyError):
    status = 400


class SpotifyAuthError(SpotifyError):
    status = 409


class SpotifyUnavailable(SpotifyError):
    status = 503


@dataclass(frozen=True)
class SpotifyRef:
    kind: str
    id: str
    uri: str


def log(message):
    print(f"lounge-music-agent: {message}", flush=True)


def ensure_dir(path, mode=0o700):
    if not path:
        return
    try:
        os.makedirs(path, mode=mode, exist_ok=True)
        os.chmod(path, mode)
    except OSError as error:
        log(f"cannot create directory {path}: {error}")
        raise


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except (OSError, json.JSONDecodeError) as error:
        log(f"cannot read {path}: {error}")
        return default


def write_json(path, payload, mode=0o600):
    directory = os.path.dirname(path) or "."
    ensure_dir(directory, 0o700)
    fd, temp_path = tempfile.mkstemp(prefix=".tmp-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
        os.chmod(temp_path, mode)
        os.replace(temp_path, path)
    finally:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass


def ensure_fifo(path):
    ensure_dir(os.path.dirname(path), 0o700)
    try:
        if os.path.exists(path):
            if not stat_is_fifo(path):
                os.unlink(path)
            else:
                os.chmod(path, 0o600)
                return
        os.mkfifo(path, 0o600)
    except OSError as error:
        log(f"cannot create audio fifo {path}: {error}")
        raise


def stat_is_fifo(path):
    try:
        import stat

        return stat.S_ISFIFO(os.stat(path).st_mode)
    except OSError:
        return False


def write_all(fd, data):
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("short write to the Spotify audio fifo")
        view = view[written:]


def load_token():
    if not TOKEN_FILE:
        return None
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as handle:
            token = handle.read().strip()
    except OSError as error:
        log(f"cannot read token file {TOKEN_FILE}: {error}")
        raise SystemExit(1) from error
    if not token:
        log(f"token file {TOKEN_FILE} is empty")
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


def mpv_is_idle():
    try:
        return bool(mpv_command("get_property", "idle-active"))
    except MpvError:
        return True


def mpv_is_paused():
    return bool(get_property("pause", False))


def mpv_has_spotify_stream():
    playlist = get_property("playlist", []) or []
    return any(
        isinstance(entry, dict) and SPOTIFY_FIFO_PATH in (entry.get("filename") or "")
        for entry in playlist
    )


def prune_played():
    """Drop playlist entries before the currently playing one."""
    position = get_property("playlist-pos", -1)
    if not isinstance(position, int) or position <= 0:
        return
    for index in range(position - 1, -1, -1):
        try:
            mpv_command("playlist-remove", index)
        except MpvError:
            break


def mpv_status(prune=True, spotify_snapshot=None):
    if prune:
        prune_played()
    playlist = get_property("playlist", []) or []
    position = get_property("playlist-pos", -1)
    if prune and isinstance(position, int) and position > 0:
        # A track may have advanced between the first prune and the reads.
        prune_played()
        playlist = get_property("playlist", []) or []
        position = get_property("playlist-pos", -1)
    idle = bool(mpv_command("get_property", "idle-active"))
    paused = bool(get_property("pause", False))
    volume = get_property("volume", 100)

    entries = []
    for entry in playlist:
        if isinstance(entry, dict):
            entries.append(
                {
                    "id": entry.get("id"),
                    "title": entry.get("title") or entry.get("filename"),
                    "artist": None,
                    "album": None,
                    "current": bool(entry.get("current")),
                }
            )

    return {
        "ok": True,
        "idle": idle,
        "paused": paused,
        "playing": not idle and not paused,
        "title": get_property("media-title"),
        "artist": None,
        "album": None,
        "volume": int(round(volume)) if isinstance(volume, (int, float)) else None,
        "mute": bool(get_property("mute", False)),
        "position": get_property("time-pos"),
        "duration": get_property("duration"),
        "queuePosition": position,
        "queueCount": len(playlist),
        "queue": entries,
        "source": "mpv",
        "spotify": spotify_info(spotify_snapshot),
    }


# ---------------------------------------------------------------------------
# Spotify OAuth and Web API
# ---------------------------------------------------------------------------


def spotify_configured():
    return bool(
        SPOTIFY_ENABLED and SPOTIFY_CLIENT_ID and SPOTIFY_REDIRECT_URI and LIBRESPOT_BIN
    )


def load_spotify_credentials():
    credentials = read_json(SPOTIFY_CREDENTIALS_PATH, None)
    if not isinstance(credentials, dict):
        return None
    if not credentials.get("refresh_token"):
        return None
    return credentials


def save_spotify_credentials(credentials):
    ensure_dir(SPOTIFY_DIR, 0o700)
    write_json(SPOTIFY_CREDENTIALS_PATH, credentials)


def delete_spotify_credentials():
    for path in (SPOTIFY_CREDENTIALS_PATH, SPOTIFY_PENDING_PATH):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError as error:
            log(f"cannot remove {path}: {error}")


def spotify_error_message(error, fallback):
    try:
        raw = error.read(65536)
    except Exception:  # noqa: BLE001 - best effort error decoding
        return fallback
    text = raw.decode("utf-8", "replace").strip()
    if not text:
        return fallback
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text[:300]
    if isinstance(payload, dict):
        message = payload.get("error_description") or payload.get("error")
        if isinstance(message, dict):
            message = message.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return fallback


def spotify_token_request(form, context):
    body = urllib.parse.urlencode(form).encode("utf-8")
    request = urllib.request.Request(
        SPOTIFY_TOKEN_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "ieee-tamu-lounge-music/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        message = spotify_error_message(error, "Spotify rejected the sign-in.")
        if error.code in (400, 401) and "invalid_grant" in message.lower():
            if context == "refresh":
                delete_spotify_credentials()
                raise SpotifyAuthError(
                    "Spotify sign-in expired. Reconnect Spotify from the lounge web app."
                ) from error
            raise SpotifyAuthError(
                "Spotify sign-in expired. Try connecting Spotify again."
            ) from error
        raise SpotifyUnavailable(f"Spotify sign-in failed: {message}") from error
    except urllib.error.URLError as error:
        raise SpotifyUnavailable(
            f"Cannot reach Spotify: {error.reason}"
        ) from error
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SpotifyUnavailable("Spotify returned an invalid token response.") from error


def spotify_access_token(force_refresh=False):
    with _token_lock:
        credentials = load_spotify_credentials()
        if not credentials:
            raise SpotifyNotLinked(
                "Spotify is not connected. An officer can connect it from the lounge web app."
            )
        now = int(time.time())
        if (
            not force_refresh
            and credentials.get("access_token")
            and int(credentials.get("expires_at", 0)) > now + 60
        ):
            return credentials["access_token"]

        refresh_token = credentials.get("refresh_token")
        if not refresh_token:
            raise SpotifyNotLinked(
                "Spotify is not connected. An officer can connect it from the lounge web app."
            )

        response = spotify_token_request(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": SPOTIFY_CLIENT_ID,
            },
            context="refresh",
        )
        access_token = response.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise SpotifyUnavailable("Spotify did not return an access token.")

        credentials["access_token"] = access_token
        credentials["expires_at"] = now + int(response.get("expires_in") or 3600)
        if response.get("refresh_token"):
            credentials["refresh_token"] = response["refresh_token"]
        if response.get("scope"):
            credentials["scope"] = response["scope"]
        save_spotify_credentials(credentials)
        return access_token


def spotify_api_request(
    method,
    path,
    *,
    token=None,
    query=None,
    body=None,
    retry=True,
):
    if not spotify_configured():
        raise SpotifyNotConfigured("Spotify is not configured on the music host.")
    if token is None:
        token = spotify_access_token()

    url = SPOTIFY_API_URL + path
    if query:
        url += "?" + urllib.parse.urlencode(query)

    data = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "ieee-tamu-lounge-music/1.0",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read()
            if response.status == 204 or not raw:
                return None
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as error:
        message = spotify_error_message(error, "Spotify request failed.")
        if error.code == 401 and retry:
            spotify_access_token(force_refresh=True)
            return spotify_api_request(
                method,
                path,
                token=None,
                query=query,
                body=body,
                retry=False,
            )
        if error.code == 429 and retry:
            try:
                delay = int(error.headers.get("Retry-After", "1"))
            except (TypeError, ValueError):
                delay = 1
            time.sleep(max(1, min(delay, 10)))
            return spotify_api_request(
                method,
                path,
                token=None,
                query=query,
                body=body,
                retry=False,
            )
        if error.code == 403:
            lower = message.lower()
            if "not registered" in lower or "not authorized" in lower:
                raise SpotifyAuthError(
                    "That Spotify account is not registered for the lounge Spotify app. "
                    "Add it under Users and Access in the Spotify Developer Dashboard, "
                    "or use the lounge's approved Premium account."
                ) from error
            if "premium" in lower:
                raise SpotifyAuthError(
                    "Spotify Premium is required for playback on the lounge account."
                ) from error
            raise SpotifyUnavailable(
                f"Spotify API error ({error.code}): {message}"
            ) from error
        raise SpotifyUnavailable(
            f"Spotify API error ({error.code}): {message}"
        ) from error
    except urllib.error.URLError as error:
        raise SpotifyUnavailable(f"Cannot reach Spotify: {error.reason}") from error
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SpotifyUnavailable("Spotify returned an invalid API response.") from error


def spotify_me(token=None):
    return spotify_api_request("GET", "/me", token=token) or {}


# ---------------------------------------------------------------------------
# Spotify URL parsing
# ---------------------------------------------------------------------------

SPOTIFY_ID_RE = re.compile(r"^[A-Za-z0-9]{22}$")


def spotify_ref(kind, item_id):
    if kind not in {"track", "album", "playlist"}:
        return None
    if not SPOTIFY_ID_RE.match(item_id or ""):
        return None
    return SpotifyRef(kind=kind, id=item_id, uri=f"spotify:{kind}:{item_id}")


def parse_spotify_uri(value):
    if not value.startswith("spotify:"):
        return None
    parts = value.split(":")
    if len(parts) < 3:
        return None
    return spotify_ref(parts[1], parts[2])


def parse_spotify_url(value):
    try:
        parsed = urllib.parse.urlparse(value)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in {"open.spotify.com", "play.spotify.com", "spotify.com"}:
        return None
    segments = [segment for segment in parsed.path.split("/") if segment]
    if segments and segments[0].startswith("intl-"):
        segments = segments[1:]
    if len(segments) >= 2:
        return spotify_ref(segments[0], segments[1])
    return None


def resolve_spotify_short_link(value):
    request = urllib.request.Request(
        value,
        method="GET",
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": "ieee-tamu-lounge-music/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            final_url = response.geturl()
            final_ref = parse_spotify_url(final_url)
            if final_ref:
                return final_ref
            body = response.read(65536).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise BadRequest(f"Could not resolve that Spotify link: {error}") from error

    match = re.search(
        r"https?://open\.spotify\.com/(?:intl-[^/]+/)?(track|album|playlist)/([A-Za-z0-9]{22})",
        body,
    )
    if match:
        ref = spotify_ref(match.group(1), match.group(2))
        if ref:
            return ref
    raise BadRequest("Could not resolve that Spotify link.")


def resolve_input(value):
    ref = parse_spotify_uri(value)
    if ref:
        return "spotify", ref
    if value.startswith("spotify:"):
        raise BadRequest(
            "Only Spotify track, album, and playlist URIs are supported."
        )

    if value.startswith("http://") or value.startswith("https://"):
        parsed = urllib.parse.urlparse(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        if host in {"open.spotify.com", "play.spotify.com", "spotify.com"}:
            ref = parse_spotify_url(value)
            if not ref:
                raise BadRequest(
                    "Only Spotify track, album, and playlist links are supported."
                )
            return "spotify", ref
        if host in {"spotify.link", "spotify.app.link"}:
            return "spotify", resolve_spotify_short_link(value)
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
        return "mpv", value
    if "://" in value:
        raise BadRequest("Only http(s) URLs, Spotify links, or search text are supported.")
    return "mpv", "ytdl://ytsearch1:" + value


def host_allowed(host):
    return any(host == allowed or host.endswith("." + allowed) for allowed in ALLOWED_HOSTS)


# ---------------------------------------------------------------------------
# Spotify playback state
# ---------------------------------------------------------------------------


def summarize_track(item):
    if not isinstance(item, dict):
        return None
    artists = [
        artist.get("name")
        for artist in (item.get("artists") or [])
        if isinstance(artist, dict) and artist.get("name")
    ]
    album = item.get("album")
    return {
        "name": item.get("name"),
        "uri": item.get("uri"),
        "duration_ms": item.get("duration_ms"),
        "artists": artists,
        "album": album.get("name") if isinstance(album, dict) else None,
    }


class SpotifyState:
    def __init__(self, service):
        self.service = service
        self._lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._data = self._empty()
        self._updated_at = 0.0

    def _empty(self):
        return {
            "configured": spotify_configured(),
            "linked": False,
            "account": None,
            "active": False,
            "playing": False,
            "item": None,
            "queue": [],
            "volume": None,
            "device": None,
            "error": None,
            "progress_ms": 0,
        }

    def _set(self, **fields):
        with self._lock:
            self._data.update(fields)
            self._updated_at = time.monotonic()

    def set_volume(self, volume):
        with self._lock:
            self._data["volume"] = volume
            self._updated_at = time.monotonic()

    def set_playing(self, playing):
        with self._lock:
            if self._data.get("item") is not None:
                self._data["playing"] = playing
                self._updated_at = time.monotonic()

    def snapshot(self, max_age=4.0, force=False):
        with self._lock:
            data = copy.deepcopy(self._data)
            age = time.monotonic() - self._updated_at
        if force or age > max_age:
            self.refresh()
            with self._lock:
                data = copy.deepcopy(self._data)
        return data

    def refresh(self):
        if not spotify_configured():
            self._set(
                configured=False,
                linked=False,
                account=None,
                active=False,
                playing=False,
                item=None,
                queue=[],
                volume=None,
                device=None,
                error=None,
                progress_ms=0,
            )
            return

        with self._refresh_lock:
            credentials = load_spotify_credentials()
            if not credentials:
                self._set(
                    configured=True,
                    linked=False,
                    account=None,
                    active=False,
                    playing=False,
                    item=None,
                    queue=[],
                    volume=None,
                    device=None,
                    error=None,
                    progress_ms=0,
                )
                return

            try:
                token = spotify_access_token()
                player = spotify_api_request("GET", "/me/player", token=token)
                account = credentials.get("account")
                if not isinstance(account, dict):
                    profile = spotify_me(token)
                    account = {
                        "id": profile.get("id"),
                        "display_name": profile.get("display_name")
                        or profile.get("id")
                        or "Spotify",
                        "product": profile.get("product"),
                        "email": profile.get("email"),
                    }
                    with _token_lock:
                        current = load_spotify_credentials()
                        if current is not None and not isinstance(
                            current.get("account"), dict
                        ):
                            current["account"] = account
                            save_spotify_credentials(current)

                if not player:
                    active = False
                    device = None
                    item = None
                    playing = False
                    progress_ms = 0
                    volume = None
                else:
                    device_data = player.get("device")
                    device = (
                        {
                            "id": device_data.get("id"),
                            "name": device_data.get("name"),
                            "type": device_data.get("type"),
                            "volume_percent": device_data.get("volume_percent"),
                            "supports_volume": device_data.get("supports_volume"),
                        }
                        if isinstance(device_data, dict)
                        else None
                    )
                    active = bool(
                        device and device.get("name") == SPOTIFY_DEVICE_NAME
                    )
                    item = summarize_track(player.get("item")) if active else None
                    playing = bool(player.get("is_playing")) and active and item is not None
                    progress_ms = player.get("progress_ms") or 0
                    volume = device.get("volume_percent") if active and device else None

                queue_items = []
                if active:
                    queue_data = spotify_api_request(
                        "GET", "/me/player/queue", token=token
                    )
                    current_uri = item.get("uri") if item else None
                    if queue_data:
                        queue_items = [
                            summary
                            for summary in (
                                summarize_track(item)
                                for item in (queue_data.get("queue") or [])
                            )
                            if summary and summary.get("uri") != current_uri
                        ]

                self._set(
                    configured=True,
                    linked=True,
                    account=account,
                    active=active,
                    playing=playing,
                    item=item,
                    queue=queue_items,
                    volume=volume,
                    device=device,
                    error=None,
                    progress_ms=progress_ms,
                )
            except SpotifyAuthError as error:
                with _token_lock:
                    delete_spotify_credentials()
                self.service.supervisor.stop()
                self._set(
                    configured=True,
                    linked=False,
                    account=None,
                    active=False,
                    playing=False,
                    item=None,
                    queue=[],
                    volume=None,
                    device=None,
                    error=str(error),
                    progress_ms=0,
                )
            except SpotifyNotLinked as error:
                self._set(
                    configured=True,
                    linked=False,
                    account=None,
                    active=False,
                    playing=False,
                    item=None,
                    queue=[],
                    volume=None,
                    device=None,
                    error=str(error),
                    progress_ms=0,
                )
            except SpotifyError as error:
                self._set(configured=True, linked=True, error=str(error))
            except Exception as error:  # noqa: BLE001 - keep the player usable
                log(f"unexpected Spotify status error: {error}")
                self._set(
                    configured=True,
                    linked=True,
                    error="Spotify is unavailable right now.",
                )


# ---------------------------------------------------------------------------
# Spotify audio relay and librespot supervisor
# ---------------------------------------------------------------------------


class SpotifyAudioRelay:
    """Feed librespot PCM to mpv through a WAV-headed FIFO."""

    def __init__(self, fifo_path):
        self.fifo_path = fifo_path
        self.queue = queue.Queue(maxsize=128)
        self.enabled = threading.Event()
        self._writer = None
        self._lock = threading.Lock()

    def feed(self, chunk):
        if not self.enabled.is_set():
            return
        try:
            self.queue.put(chunk, timeout=0.2)
        except queue.Full:
            log("Spotify audio relay queue is full; dropping audio")

    def start(self):
        ensure_fifo(self.fifo_path)
        with self._lock:
            if self._writer is not None and self._writer.is_alive():
                if self.enabled.is_set():
                    return
                self._writer.join(timeout=2)
                self._writer = None
            while True:
                try:
                    self.queue.get_nowait()
                except queue.Empty:
                    break
            self.enabled.set()
            self._writer = threading.Thread(
                target=self._write_loop, name="spotify-audio", daemon=True
            )
            self._writer.start()

    def _write_loop(self):
        deadline = time.monotonic() + 10
        fd = None
        while time.monotonic() < deadline and self.enabled.is_set():
            try:
                fd = os.open(self.fifo_path, os.O_WRONLY | os.O_NONBLOCK)
                break
            except OSError as error:
                if error.errno not in (errno.ENXIO, errno.EAGAIN):
                    log(f"cannot open Spotify audio fifo: {error}")
                    self.enabled.clear()
                    return
                time.sleep(0.05)

        if fd is None:
            log("Spotify audio fifo was not opened by mpv")
            self.enabled.clear()
            return

        try:
            fcntl.fcntl(fd, fcntl.F_SETFL, 0)
            while True:
                try:
                    self.queue.get_nowait()
                except queue.Empty:
                    break
            write_all(fd, WAV_HEADER)
            while self.enabled.is_set():
                try:
                    chunk = self.queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                write_all(fd, chunk)
        except (BrokenPipeError, OSError) as error:
            log(f"Spotify audio relay stopped: {error}")
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
            self.enabled.clear()

    def stop(self):
        self.enabled.clear()
        while True:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                break
        writer = self._writer
        if writer is not None and writer is not threading.current_thread():
            writer.join(timeout=2)
        self._writer = None


class LibrespotSupervisor:
    def __init__(self, relay):
        self.relay = relay
        self._lock = threading.Lock()
        self._proc = None
        self._generation = 0
        self._started_at = 0.0
        self._attempt = 0
        self.last_error = None

    def is_running(self):
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def generation(self):
        with self._lock:
            return self._generation

    def has_stored_credentials(self):
        credentials = read_json(SPOTIFY_LIBRESPOT_CREDENTIALS_PATH, None)
        return bool(
            isinstance(credentials, dict)
            and credentials.get("auth_type") == 1
            and credentials.get("auth_data")
        )

    def stored_username(self):
        credentials = read_json(SPOTIFY_LIBRESPOT_CREDENTIALS_PATH, None)
        if not isinstance(credentials, dict):
            return None
        username = credentials.get("username")
        return username if isinstance(username, str) and username else None

    def ensure_started(self):
        if not spotify_configured():
            return
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return
            try:
                self._start_locked()
            except Exception as error:  # noqa: BLE001 - keep the agent alive
                self.last_error = str(error)
                log(f"cannot start librespot: {error}")

    def begin_oauth_login(self):
        if not spotify_configured():
            raise SpotifyNotConfigured(
                "Spotify is not configured on the music host."
            )
        self.stop()
        shutil.rmtree(SPOTIFY_LIBRESPOT_DIR, ignore_errors=True)
        ensure_dir(SPOTIFY_LIBRESPOT_DIR, 0o700)
        self._attempt = 0
        self.last_error = None
        with self._lock:
            try:
                return self._start_locked(oauth=True)
            except Exception as error:
                self.last_error = str(error)
                raise

    def _librespot_args(self, oauth=False):
        args = [
            LIBRESPOT_BIN,
            "--backend",
            "pipe",
            "--format",
            "S16",
            "--name",
            SPOTIFY_DEVICE_NAME,
            "--device-type",
            "speaker",
            "--bitrate",
            str(SPOTIFY_BITRATE),
            "--mixer",
            "softvol",
            "--volume-ctrl",
            "log",
            "--autoplay",
            "off",
            "--disable-audio-cache",
            "--system-cache",
            SPOTIFY_LIBRESPOT_DIR,
        ]
        if oauth:
            args.extend(
                [
                    "--enable-oauth",
                    "--oauth-port",
                    str(SPOTIFY_OAUTH_PORT),
                ]
            )
        return args

    def _start_locked(self, oauth=False):
        ensure_dir(SPOTIFY_LIBRESPOT_DIR, 0o700)
        legacy_credentials = read_json(SPOTIFY_LIBRESPOT_CREDENTIALS_PATH, None)
        if (
            isinstance(legacy_credentials, dict)
            and legacy_credentials.get("auth_type") == 3
        ):
            # Older agents bootstrapped librespot with our Web API access token.
            # Spotify login5 rejects those sessions, so remove the stale cache.
            log("removing legacy developer-token librespot credentials")
            try:
                os.unlink(SPOTIFY_LIBRESPOT_CREDENTIALS_PATH)
            except FileNotFoundError:
                pass
        log(
            f"starting librespot as {SPOTIFY_DEVICE_NAME!r}"
            + (" (official OAuth pairing)" if oauth else "")
        )
        proc = subprocess.Popen(
            self._librespot_args(oauth=oauth),
            stdout=subprocess.PIPE,
            stderr=None,
            bufsize=0,
            close_fds=True,
        )
        self._proc = proc
        self._generation += 1
        generation = self._generation
        self._started_at = time.monotonic()
        self._attempt += 1
        self.last_error = None

        url = None
        if oauth:
            try:
                url = self._read_oauth_url(proc)
            except Exception:
                self._terminate(proc)
                with self._lock:
                    if self._proc is proc:
                        self._proc = None
                    self._generation += 1
                raise

        threading.Thread(
            target=self._read_stdout,
            args=(proc, generation),
            name="librespot-stdout",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._monitor,
            args=(proc, generation, oauth),
            name="librespot-monitor",
            daemon=True,
        ).start()
        return url

    def _read_oauth_url(self, proc):
        result = queue.Queue()

        def read_lines():
            try:
                while True:
                    line = proc.stdout.readline()
                    if not line:
                        break
                    text = line.decode("utf-8", "replace").strip()
                    if text.startswith("Browse to: "):
                        result.put(text[len("Browse to: ") :])
                        return
            except Exception as error:  # noqa: BLE001 - forwarded to caller
                result.put(error)
                return
            result.put(
                SpotifyUnavailable(
                    "librespot exited before printing the Spotify login URL."
                )
            )

        threading.Thread(
            target=read_lines, name="librespot-oauth-url", daemon=True
        ).start()
        try:
            value = result.get(timeout=10)
        except queue.Empty as error:
            raise SpotifyUnavailable(
                "Timed out waiting for the Spotify login URL."
            ) from error
        if isinstance(value, Exception):
            raise value
        return value

    def _read_stdout(self, proc, generation):
        try:
            while True:
                chunk = proc.stdout.read(8192)
                if not chunk:
                    break
                self.relay.feed(chunk)
        except (OSError, ValueError) as error:
            log(f"librespot audio pipe closed: {error}")

    def _monitor(self, proc, generation, oauth=False):
        code = proc.wait()
        with self._lock:
            if self._proc is proc:
                self._proc = None
            current = self._generation == generation
        if not current:
            return

        runtime = time.monotonic() - self._started_at
        log(f"librespot exited with code {code} after {runtime:.1f}s")
        if runtime > 60:
            self._attempt = 0

        if oauth and not self.has_stored_credentials():
            self.last_error = (
                "Spotify player pairing was not completed. "
                "Run the SSH tunnel and open the login link again."
            )
            log(self.last_error)
            return

        if not spotify_configured():
            return

        if self._attempt >= 4:
            self.last_error = "librespot keeps exiting; check the lounge music journal."
            log(self.last_error)
            self._attempt = 0
            time.sleep(15)
        else:
            time.sleep(min(5 * self._attempt, 20))

        with self._lock:
            if self._generation != generation:
                return
        self.ensure_started()

    def reset_and_start(self, clear_credentials=False):
        self.stop()
        if clear_credentials:
            shutil.rmtree(SPOTIFY_LIBRESPOT_DIR, ignore_errors=True)
        self._attempt = 0
        self.last_error = None
        self.ensure_started()

    def _terminate(self, proc):
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

    def stop(self):
        with self._lock:
            self._generation += 1
            proc = self._proc
            self._proc = None
        self._terminate(proc)


# ---------------------------------------------------------------------------
# Spotify service
# ---------------------------------------------------------------------------


class SpotifyService:
    def __init__(self):
        self.state = SpotifyState(self)
        self.relay = SpotifyAudioRelay(SPOTIFY_FIFO_PATH)
        self.supervisor = LibrespotSupervisor(self.relay)
        self._device_lock = threading.Lock()
        self._device_id = None
        self._device_at = 0.0
        self._device_generation = -1

    def start(self):
        self.supervisor.ensure_started()

    def stop(self):
        self.supervisor.stop()
        self.relay.stop()

    def begin_player_login(self):
        url = self.supervisor.begin_oauth_login()
        host = socket.gethostname() or "ieee-tamu-5B"
        return {
            "url": url,
            "command": (
                f"ssh -L {SPOTIFY_OAUTH_PORT}:127.0.0.1:{SPOTIFY_OAUTH_PORT} {host}"
            ),
        }

    def device_id(self):
        if not self.supervisor.has_stored_credentials():
            raise SpotifyUnavailable(
                "The Spotify player is not paired yet. In the lounge web app, "
                "use 'Set up Spotify player' and complete the one-time Spotify "
                "login."
            )
        with self._device_lock:
            generation = self.supervisor.generation()
            now = time.monotonic()
            if generation != self._device_generation:
                self._device_id = None
                self._device_generation = generation
            if self._device_id and now - self._device_at < 30:
                return self._device_id

            deadline = now + 8
            while True:
                data = spotify_api_request("GET", "/me/player/devices") or {}
                for device in data.get("devices") or []:
                    if not isinstance(device, dict):
                        continue
                    if device.get("name") == SPOTIFY_DEVICE_NAME and device.get("id"):
                        self._device_id = device["id"]
                        self._device_at = time.monotonic()
                        self._device_generation = self.supervisor.generation()
                        return self._device_id
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.5)

            raise SpotifyUnavailable(
                f"The Spotify device {SPOTIFY_DEVICE_NAME!r} is not online yet. "
                "Try again in a moment."
            )

    def info(self, snapshot=None):
        if snapshot is None:
            snapshot = self.state.snapshot(max_age=4)
        credentials = load_spotify_credentials() or {}
        account = credentials.get("account") or snapshot.get("account") or {}
        return {
            "configured": spotify_configured(),
            "linked": bool(snapshot.get("linked")) or bool(credentials),
            "accountName": account.get("display_name"),
            "product": account.get("product"),
            "deviceName": SPOTIFY_DEVICE_NAME,
            "deviceOnline": self.supervisor.is_running(),
            "paired": self.supervisor.has_stored_credentials(),
            "error": snapshot.get("error") or self.supervisor.last_error,
        }

    def link_url(self):
        if not spotify_configured():
            raise SpotifyNotConfigured(
                "Spotify is not configured on the music host. Set the Spotify "
                "client ID and redirect URI in the lounge-music NixOS module."
            )

        pending = read_json(SPOTIFY_PENDING_PATH, {}) or {}
        if not isinstance(pending, dict):
            pending = {}
        now = int(time.time())
        pending = {
            state: value
            for state, value in pending.items()
            if isinstance(value, dict) and now - int(value.get("created_at", 0)) < 600
        }

        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
            .rstrip(b"=")
            .decode("ascii")
        )
        pending[state] = {"verifier": verifier, "created_at": now}
        if len(pending) > 5:
            pending = dict(
                sorted(
                    pending.items(),
                    key=lambda item: int(item[1].get("created_at", 0)),
                    reverse=True,
                )[:5]
            )
        write_json(SPOTIFY_PENDING_PATH, pending)

        params = {
            "client_id": SPOTIFY_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": SPOTIFY_REDIRECT_URI,
            "state": state,
            "scope": " ".join(SPOTIFY_SCOPES),
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "show_dialog": "false",
        }
        return SPOTIFY_AUTH_URL + "?" + urllib.parse.urlencode(params)

    def complete_link(self, code, state):
        if not spotify_configured():
            raise SpotifyNotConfigured(
                "Spotify is not configured on the music host. Set the Spotify "
                "client ID and redirect URI in the lounge-music NixOS module."
            )

        pending = read_json(SPOTIFY_PENDING_PATH, {}) or {}
        entry = pending.pop(state, None) if isinstance(pending, dict) else None
        write_json(SPOTIFY_PENDING_PATH, pending if isinstance(pending, dict) else {})
        if not isinstance(entry, dict):
            raise SpotifyAuthError(
                "Spotify sign-in state is invalid or expired. Try connecting again."
            )
        if time.time() - int(entry.get("created_at", 0)) > 600:
            raise SpotifyAuthError(
                "Spotify sign-in took too long. Try connecting again."
            )

        with _token_lock:
            response = spotify_token_request(
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": SPOTIFY_REDIRECT_URI,
                    "client_id": SPOTIFY_CLIENT_ID,
                    "code_verifier": entry.get("verifier", ""),
                },
                context="link",
            )
            access_token = response.get("access_token")
            refresh_token = response.get("refresh_token")
            if not isinstance(access_token, str) or not isinstance(refresh_token, str):
                raise SpotifyAuthError(
                    "Spotify did not return a refresh token. Try connecting again."
                )

            credentials = {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_at": int(time.time()) + int(response.get("expires_in") or 3600),
                "scope": response.get("scope"),
                "account": None,
                "linked_at": int(time.time()),
            }
            save_spotify_credentials(credentials)

        try:
            profile = spotify_me()
        except SpotifyAuthError as error:
            with _token_lock:
                delete_spotify_credentials()
            raise SpotifyAuthError(str(error)) from error
        except SpotifyError as error:
            with _token_lock:
                delete_spotify_credentials()
            raise SpotifyUnavailable(
                f"Could not verify the Spotify account: {error}"
            ) from error

        if not isinstance(profile, dict) or not (
            profile.get("id") or profile.get("display_name")
        ):
            with _token_lock:
                delete_spotify_credentials()
            raise SpotifyAuthError(
                "Spotify did not return account details. Try connecting again."
            )

        if profile.get("product") != "premium":
            with _token_lock:
                delete_spotify_credentials()
            raise SpotifyAuthError(
                "Spotify Premium is required for the lounge account. "
                "Link a Premium account, or upgrade that account and try again."
            )

        account = {
            "id": profile.get("id"),
            "display_name": profile.get("display_name")
            or profile.get("id")
            or "Spotify",
            "product": profile.get("product"),
            "email": profile.get("email"),
        }
        with _token_lock:
            current = load_spotify_credentials() or credentials
            current["account"] = account
            save_spotify_credentials(current)

        cached_username = self.supervisor.stored_username()
        clear_librespot = bool(
            cached_username
            and profile.get("id")
            and cached_username.lower() != str(profile["id"]).lower()
        )
        self.supervisor.reset_and_start(clear_credentials=clear_librespot)
        self.state.refresh()
        return account

    def disconnect(self):
        self.supervisor.stop()
        with _token_lock:
            delete_spotify_credentials()
        shutil.rmtree(SPOTIFY_LIBRESPOT_DIR, ignore_errors=True)
        self.state.refresh()

    def _uris_for_ref(self, ref, limit):
        if ref.kind == "track":
            return [ref.uri][:limit]
        if ref.kind == "album":
            data = spotify_api_request(
                "GET", f"/albums/{ref.id}/tracks", query={"limit": limit}
            ) or {}
            return [
                item.get("uri")
                for item in data.get("items") or []
                if isinstance(item, dict) and item.get("uri")
            ][:limit]
        if ref.kind == "playlist":
            data = spotify_api_request(
                "GET",
                f"/playlists/{ref.id}/tracks",
                query={
                    "limit": limit,
                    "offset": 0,
                    "fields": "items(track(uri,is_local))",
                },
            ) or {}
            uris = []
            for item in data.get("items") or []:
                if not isinstance(item, dict):
                    continue
                track = item.get("track")
                if not isinstance(track, dict) or track.get("is_local"):
                    continue
                if track.get("uri"):
                    uris.append(track["uri"])
            return uris[:limit]
        raise BadRequest("Only Spotify tracks, albums, and playlists are supported.")

    def play(self, ref, force_start=False):
        if not spotify_configured():
            raise SpotifyNotConfigured(
                "Spotify is not configured on the music host."
            )
        if not load_spotify_credentials():
            raise SpotifyNotLinked(
                "Spotify is not connected. An officer can connect it from the lounge web app."
            )

        device_id = self.device_id()
        player = spotify_api_request("GET", "/me/player")
        active = bool(
            player
            and isinstance(player.get("device"), dict)
            and player["device"].get("name") == SPOTIFY_DEVICE_NAME
        )
        item = player.get("item") if active and player else None
        uris = self._uris_for_ref(ref, MAX_QUEUE_LENGTH)
        if not uris:
            raise BadRequest("That Spotify item has no playable tracks.")

        if active and item and not force_start:
            queue_data = spotify_api_request("GET", "/me/player/queue") or {}
            upcoming = [
                item for item in queue_data.get("queue") or [] if isinstance(item, dict)
            ]
            remaining = max(0, MAX_QUEUE_LENGTH - len(upcoming))
            if remaining <= 0:
                raise BadRequest(f"The Spotify queue is full ({MAX_QUEUE_LENGTH} tracks).")
            uris = uris[:remaining]
            for uri in uris:
                spotify_api_request(
                    "POST",
                    "/me/player/queue",
                    query={"uri": uri, "device_id": device_id},
                )
            action = "queued"
        else:
            uris = uris[:MAX_QUEUE_LENGTH]
            spotify_api_request(
                "PUT",
                "/me/player/play",
                query={"device_id": device_id},
                body={"uris": uris},
            )
            action = "playing"

        self.state.refresh()
        return {"action": action, "count": len(uris)}

    def pause(self):
        player = spotify_api_request("GET", "/me/player")
        if player and player.get("is_playing"):
            spotify_api_request(
                "PUT", "/me/player/pause", query={"device_id": self.device_id()}
            )
        self.state.set_playing(False)
        self.state.refresh()

    def resume(self):
        player = spotify_api_request("GET", "/me/player")
        if not player or not player.get("item"):
            raise SpotifyUnavailable("Nothing is queued on Spotify right now.")
        spotify_api_request(
            "PUT", "/me/player/play", query={"device_id": self.device_id()}
        )
        self.state.set_playing(True)
        self.state.refresh()

    def skip(self, count):
        player = spotify_api_request("GET", "/me/player")
        if not player or not player.get("item"):
            raise BadRequest("Nothing is playing on Spotify.")
        device_id = self.device_id()
        for _ in range(count):
            spotify_api_request(
                "POST", "/me/player/next", query={"device_id": device_id}
            )
            time.sleep(0.15)
        self.state.refresh()

    def set_volume(self, value):
        value = max(0, min(MAX_VOLUME, int(round(value))))
        spotify_api_request(
            "PUT",
            "/me/player/volume",
            query={"volume_percent": value, "device_id": self.device_id()},
        )
        self.state.set_volume(value)

    def current_volume(self):
        snapshot = self.state.snapshot(max_age=4)
        volume = snapshot.get("volume")
        if isinstance(volume, (int, float)):
            return int(round(volume))
        return _spotify_volume

    def toggle_mute(self):
        global _spotify_muted, _spotify_volume, _spotify_volume_before_mute
        if _spotify_muted:
            target = _spotify_volume_before_mute or 50
            self.set_volume(target)
            with _player_lock:
                _spotify_muted = False
                _spotify_volume = target
                persist_player_state_locked()
        else:
            current = self.current_volume()
            self.set_volume(0)
            with _player_lock:
                _spotify_volume_before_mute = current or 50
                _spotify_muted = True
                persist_player_state_locked()


# ---------------------------------------------------------------------------
# Player state and source switching
# ---------------------------------------------------------------------------


def load_player_state():
    global _current_source, _spotify_muted, _mpv_queue

    data = read_json(PLAYER_STATE_PATH, {}) or {}
    if not isinstance(data, dict):
        data = {}

    source = data.get("current_source")
    if source in {"mpv", "spotify"}:
        _current_source = source

    for attr, global_name, default in (
        ("mpv_volume", "_mpv_volume", 100),
        ("spotify_volume", "_spotify_volume", 100),
        ("spotify_volume_before_mute", "_spotify_volume_before_mute", 100),
    ):
        value = data.get(attr)
        if isinstance(value, (int, float)):
            value = max(0, min(MAX_VOLUME, int(round(value))))
        else:
            value = default
        globals()[global_name] = value

    _spotify_muted = bool(data.get("spotify_muted"))

    saved_queue = data.get("mpv_queue")
    if isinstance(saved_queue, dict) and isinstance(saved_queue.get("entries"), list):
        entries = [
            entry
            for entry in saved_queue["entries"]
            if isinstance(entry, dict) and entry.get("filename")
        ]
        if entries:
            position = saved_queue.get("position")
            _mpv_queue = {
                "entries": entries,
                "position": position if isinstance(position, int) else 0,
                "time_pos": saved_queue.get("time_pos")
                if isinstance(saved_queue.get("time_pos"), (int, float))
                else 0,
            }


def persist_player_state_locked():
    with _player_lock:
        write_json(
            PLAYER_STATE_PATH,
            {
                "current_source": _current_source,
                "mpv_volume": _mpv_volume,
                "spotify_volume": _spotify_volume,
                "spotify_volume_before_mute": _spotify_volume_before_mute,
                "spotify_muted": _spotify_muted,
                "mpv_queue": _mpv_queue,
            },
        )


def spotify_snapshot(force=False, max_age=4.0):
    if not SPOTIFY:
        return {
            "configured": False,
            "linked": False,
            "account": None,
            "active": False,
            "playing": False,
            "item": None,
            "queue": [],
            "volume": None,
            "device": None,
            "error": None,
        }
    return SPOTIFY.state.snapshot(force=force, max_age=max_age)


def spotify_info(snapshot=None):
    if not SPOTIFY:
        return {
            "configured": False,
            "linked": False,
            "accountName": None,
            "product": None,
            "deviceName": None,
            "deviceOnline": False,
            "error": None,
        }
    if snapshot is None:
        snapshot = spotify_snapshot()
    return SPOTIFY.info(snapshot)


def resolve_source(snapshot=None, force=False):
    if snapshot is None:
        snapshot = spotify_snapshot(force=force)

    if snapshot.get("playing"):
        return "spotify"
    if _spotify_stream_active and snapshot.get("item"):
        return "spotify"
    if not mpv_is_idle():
        return "mpv"
    if snapshot.get("item") and _current_source == "spotify":
        return "spotify"
    return "mpv"


def save_mpv_volume():
    global _mpv_volume
    if _spotify_stream_active:
        return
    value = get_property("volume", 100)
    if isinstance(value, (int, float)):
        _mpv_volume = max(0, min(MAX_VOLUME, int(round(value))))


def restore_mpv_volume():
    try:
        mpv_command("set_property", "volume", _mpv_volume)
    except MpvError:
        pass


def save_mpv_queue():
    global _mpv_queue
    if _spotify_stream_active:
        return
    try:
        if mpv_is_idle():
            _mpv_queue = None
            return
        prune_played()
        playlist = get_property("playlist", []) or []
        position = get_property("playlist-pos", 0)
        time_pos = get_property("time-pos", 0)
        entries = []
        for entry in playlist:
            if isinstance(entry, dict) and entry.get("filename"):
                filename = entry["filename"]
                if SPOTIFY_FIFO_PATH in filename:
                    continue
                entries.append(
                    {
                        "filename": filename,
                        "title": entry.get("title") or filename,
                    }
                )
        if not entries:
            _mpv_queue = None
            return
        _mpv_queue = {
            "entries": entries,
            "position": position if isinstance(position, int) else 0,
            "time_pos": time_pos if isinstance(time_pos, (int, float)) else 0,
        }
    except MpvError as error:
        log(f"cannot save the mpv queue: {error}")


def restore_mpv_queue():
    global _mpv_queue
    saved = _mpv_queue
    if not isinstance(saved, dict):
        return False
    entries = saved.get("entries") or []
    if not entries:
        _mpv_queue = None
        return False

    try:
        mpv_command("playlist-clear")
        for entry in entries:
            filename = entry.get("filename")
            if filename:
                mpv_command("loadfile", filename, "append")
        position = saved.get("position", 0)
        if not isinstance(position, int) or position < 0 or position >= len(entries):
            position = 0
        mpv_command("playlist-play-index", position)
        _mpv_queue = None
        persist_player_state_locked()
        return True
    except MpvError as error:
        log(f"cannot restore the mpv queue: {error}")
        return False


def switch_to_spotify(playing=None):
    global _spotify_stream_active, _current_source

    if not SPOTIFY:
        raise SpotifyNotConfigured("Spotify is not enabled on the music host.")

    with _player_lock:
        if playing is None:
            playing = bool(spotify_snapshot().get("playing"))

        if not _spotify_stream_active:
            if not mpv_has_spotify_stream():
                save_mpv_volume()
                save_mpv_queue()
            try:
                mpv_command("stop")
            except MpvError as error:
                log(f"cannot stop mpv before Spotify: {error}")

            SPOTIFY.relay.start()
            try:
                mpv_command("set_property", "volume", 100)
                mpv_command(
                    "loadfile", f"file://{SPOTIFY_FIFO_PATH}", "replace"
                )
            except MpvError as error:
                log(f"cannot load the Spotify audio stream: {error}")
                SPOTIFY.relay.stop()
                _spotify_stream_active = False
                raise

            _spotify_stream_active = True
        elif mpv_is_idle():
            # The relay or mpv lost the stream; reconnect it.
            SPOTIFY.relay.stop()
            SPOTIFY.relay.start()
            try:
                mpv_command("set_property", "volume", 100)
                mpv_command(
                    "loadfile", f"file://{SPOTIFY_FIFO_PATH}", "replace"
                )
            except MpvError as error:
                log(f"cannot reload the Spotify audio stream: {error}")
                SPOTIFY.relay.stop()
                _spotify_stream_active = False
                raise

        try:
            mpv_command("set_property", "pause", not playing)
        except MpvError as error:
            log(f"cannot set mpv pause state: {error}")

        _current_source = "spotify"
        persist_player_state_locked()


def stop_spotify_stream():
    global _spotify_stream_active

    with _player_lock:
        if not _spotify_stream_active:
            return
        try:
            mpv_command("stop")
        except MpvError:
            pass
        if SPOTIFY:
            SPOTIFY.relay.stop()
        _spotify_stream_active = False
        restore_mpv_volume()
        persist_player_state_locked()


def switch_to_mpv(restore=False):
    global _current_source

    with _player_lock:
        if _spotify_stream_active:
            stop_spotify_stream()

        if SPOTIFY:
            snapshot = spotify_snapshot()
            if snapshot.get("playing"):
                try:
                    SPOTIFY.pause()
                except SpotifyError as error:
                    log(f"cannot pause Spotify while switching to mpv: {error}")

        if restore:
            restore_mpv_volume()
            restore_mpv_queue()

        _current_source = "mpv"
        persist_player_state_locked()


def reconcile_spotify(snapshot):
    if not SPOTIFY:
        return

    playing = bool(snapshot.get("playing"))
    item = snapshot.get("item")

    with _player_lock:
        if playing:
            if not _spotify_stream_active:
                try:
                    switch_to_spotify(playing=True)
                except Exception as error:  # noqa: BLE001 - watcher must keep running
                    log(f"cannot switch to the Spotify audio stream: {error}")
            else:
                try:
                    if mpv_is_idle():
                        switch_to_spotify(playing=True)
                    elif mpv_is_paused():
                        mpv_command("set_property", "pause", False)
                except (MpvError, SpotifyError) as error:
                    log(f"cannot resume mpv for Spotify: {error}")
        elif _spotify_stream_active:
            if item:
                try:
                    if not mpv_is_paused():
                        mpv_command("set_property", "pause", True)
                except MpvError as error:
                    log(f"cannot pause mpv for Spotify: {error}")
            else:
                stop_spotify_stream()
                _current_source = "mpv"
                persist_player_state_locked()


def spotify_watch_loop():
    while True:
        try:
            if not SPOTIFY:
                time.sleep(2)
                continue
            if load_spotify_credentials():
                SPOTIFY.state.refresh()
                reconcile_spotify(SPOTIFY.state.snapshot(max_age=1))
            else:
                with _player_lock:
                    if _spotify_stream_active:
                        stop_spotify_stream()
                        _current_source = "mpv"
                        persist_player_state_locked()
                if SPOTIFY.supervisor.is_running():
                    SPOTIFY.supervisor.stop()
                SPOTIFY.state.refresh()
        except Exception as error:  # noqa: BLE001 - watcher must keep running
            log(f"Spotify watcher error: {error}")
        time.sleep(2)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def spotify_status(snapshot):
    item = snapshot.get("item")
    playing = bool(snapshot.get("playing"))
    queue_items = snapshot.get("queue") or []

    entries = []
    if item:
        entries.append(
            {
                "id": None,
                "title": item.get("name"),
                "artist": ", ".join(item.get("artists") or []) or None,
                "album": item.get("album"),
                "current": True,
            }
        )
    for queued in queue_items:
        entries.append(
            {
                "id": None,
                "title": queued.get("name"),
                "artist": ", ".join(queued.get("artists") or []) or None,
                "album": queued.get("album"),
                "current": False,
            }
        )

    duration_ms = item.get("duration_ms") if item else None
    progress_ms = snapshot.get("progress_ms")
    artist = (", ".join(item.get("artists") or []) or None) if item else None

    return {
        "ok": True,
        "idle": item is None,
        "paused": item is not None and not playing,
        "playing": item is not None and playing,
        "title": item.get("name") if item else None,
        "artist": artist,
        "album": item.get("album") if item else None,
        "volume": snapshot.get("volume")
        if isinstance(snapshot.get("volume"), (int, float))
        else _spotify_volume,
        "mute": _spotify_muted,
        "position": (progress_ms or 0) / 1000 if isinstance(progress_ms, (int, float)) else None,
        "duration": duration_ms / 1000 if isinstance(duration_ms, (int, float)) else None,
        "queuePosition": 0 if item else -1,
        "queueCount": len(entries),
        "queue": entries,
        "source": "spotify",
        "spotify": SPOTIFY.info(snapshot)
        if SPOTIFY
        else {
            "configured": False,
            "linked": False,
            "accountName": None,
            "product": None,
            "deviceName": None,
            "deviceOnline": False,
            "error": snapshot.get("error"),
        },
    }


def status(prune=True):
    snapshot = spotify_snapshot()
    source = resolve_source(snapshot)
    if source == "spotify":
        return spotify_status(snapshot)
    return mpv_status(prune=prune, spotify_snapshot=snapshot)


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "lounge-music/0.2"

    def log_message(self, fmt, *args):
        log(f"{self.address_string()} - {fmt % args}")

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
            except Exception as error:  # noqa: BLE001 - keep the API responsive
                log(f"unhandled status error: {error}")
                self.send_json(500, {"ok": False, "error": "Internal player error."})
            return
        if path == "/spotify/status":
            payload = {"ok": True}
            payload.update(spotify_info())
            self.send_json(200, payload)
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
                self.handle_pause()
            elif path == "/resume":
                self.handle_resume()
            elif path == "/volume":
                self.handle_volume(body)
            elif path == "/mute":
                self.handle_mute()
            elif path == "/skip":
                self.handle_skip(body)
            elif path == "/remove":
                self.handle_remove(body)
            elif path == "/move":
                self.handle_move(body)
            elif path == "/clear":
                self.handle_clear()
            elif path == "/stop":
                self.handle_stop()
            elif path == "/spotify/link":
                self.handle_spotify_link()
            elif path == "/spotify/callback":
                self.handle_spotify_callback(body)
            elif path == "/spotify/player-login":
                self.handle_spotify_player_login()
            elif path == "/spotify/disconnect":
                self.handle_spotify_disconnect()
            else:
                self.send_json(404, {"ok": False, "error": "not found"})
        except BadRequest as error:
            self.send_json(400, {"ok": False, "error": str(error)})
        except SpotifyError as error:
            self.send_json(error.status, {"ok": False, "error": str(error)})
        except MpvError as error:
            self.send_json(503, {"ok": False, "error": str(error)})
        except Exception as error:  # noqa: BLE001 - keep the API responsive
            log(f"unhandled request error: {error}")
            self.send_json(500, {"ok": False, "error": "Internal player error."})

    def handle_play(self, body):
        value = body.get("input")
        if not isinstance(value, str) or not value.strip():
            raise BadRequest("'input' must be a non-empty string.")
        value = value.strip()
        if len(value) > MAX_INPUT_LENGTH:
            raise BadRequest(f"'input' must be at most {MAX_INPUT_LENGTH} characters.")

        kind, target = resolve_input(value)
        if kind == "spotify":
            self.handle_play_spotify(target, value, body)
        else:
            self.handle_play_mpv(target, value, body)

    def handle_play_mpv(self, target, value, body):
        global _current_source, _mpv_queue
        requested_by = body.get("requestedBy")
        with _player_lock:
            snapshot = spotify_snapshot(max_age=2)
            if SPOTIFY and snapshot.get("playing"):
                try:
                    SPOTIFY.pause()
                except SpotifyError as error:
                    log(f"cannot pause Spotify before mpv playback: {error}")

            if _spotify_stream_active:
                stop_spotify_stream()

            if _current_source == "spotify":
                restore_mpv_volume()
                restore_mpv_queue()
                _current_source = "mpv"
                persist_player_state_locked()

            current = mpv_status(prune=True, spotify_snapshot=snapshot)

            # If nothing is playing, any entries left in the playlist are finished
            # or failed tracks. Clear them so they don't count against the queue
            # limit. Re-check after a short delay to avoid clearing during a track
            # transition.
            if current["idle"] and current["queueCount"] > 0:
                time.sleep(0.3)
                if bool(get_property("idle-active", True)):
                    mpv_command("playlist-clear")
                    current["queueCount"] = 0
                else:
                    current["idle"] = False

            if current["queueCount"] >= MAX_QUEUE_LENGTH:
                raise BadRequest(f"The queue is full ({MAX_QUEUE_LENGTH} tracks).")

            previous_count = current["queueCount"]
            mpv_command("loadfile", target, "append-play")

            # mpv's append-play is not reliable with the yt-dlp hook: it can add
            # the entry but leave playlist-pos at -1, so nothing starts. When the
            # player was idle, explicitly select the first newly queued entry.
            if current["idle"]:
                mpv_command("playlist-play-index", previous_count)

            _mpv_queue = None
            persist_player_state_locked()

        log(
            f"queued {value!r} requested by {requested_by or 'unknown'}"
        )

        result = mpv_status(prune=True, spotify_snapshot=snapshot)
        result["queued"] = value
        self.send_json(200, result)

    def handle_play_spotify(self, ref, value, body):
        if not SPOTIFY:
            raise SpotifyNotConfigured("Spotify is not enabled on the music host.")

        requested_by = body.get("requestedBy")

        with _player_lock:
            force_start = resolve_source(force=True) != "spotify"
            was_mpv_active = not mpv_is_idle() and not _spotify_stream_active
            was_mpv_paused = mpv_is_paused()
            if was_mpv_active:
                try:
                    mpv_command("set_property", "pause", True)
                except MpvError:
                    pass
            try:
                result = SPOTIFY.play(ref, force_start=force_start)
            except Exception:
                if was_mpv_active:
                    try:
                        mpv_command("set_property", "pause", was_mpv_paused)
                    except MpvError:
                        pass
                raise

            try:
                snapshot = SPOTIFY.state.snapshot(max_age=1)
                playing = result.get("action") == "playing" or bool(
                    snapshot.get("playing")
                )
                switch_to_spotify(playing=playing)
            except Exception as error:
                log(f"cannot switch to the Spotify audio stream: {error}")
                if was_mpv_active:
                    try:
                        mpv_command("set_property", "pause", was_mpv_paused)
                    except MpvError:
                        pass
                raise

        log(
            f"spotify {result['action']} {value!r} requested by "
            f"{requested_by or 'unknown'}"
        )

        result_status = status()
        result_status["queued"] = value
        self.send_json(200, result_status)

    def handle_pause(self):
        global _current_source
        with _player_lock:
            source = resolve_source(force=True)
            if source == "spotify" and SPOTIFY:
                SPOTIFY.pause()
                if _spotify_stream_active:
                    try:
                        mpv_command("set_property", "pause", True)
                    except MpvError:
                        pass
                _current_source = "spotify"
                persist_player_state_locked()
            else:
                mpv_command("set_property", "pause", True)
                _current_source = "mpv"
                persist_player_state_locked()
            self.send_json(200, status())

    def handle_resume(self):
        global _current_source
        with _player_lock:
            source = resolve_source(force=True)
            if source == "spotify" and SPOTIFY:
                SPOTIFY.resume()
                if not _spotify_stream_active:
                    switch_to_spotify(playing=True)
                else:
                    try:
                        mpv_command("set_property", "pause", False)
                    except MpvError:
                        pass
                _current_source = "spotify"
                persist_player_state_locked()
            else:
                mpv_command("set_property", "pause", False)
                _current_source = "mpv"
                persist_player_state_locked()
            self.send_json(200, status())

    def handle_skip(self, body):
        global _current_source, _mpv_queue
        count = body.get("count", 1)
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise BadRequest("'count' must be a positive integer.")

        with _player_lock:
            source = resolve_source(force=True)
            if source == "spotify" and SPOTIFY:
                SPOTIFY.skip(count)
                result = status()
                result["skipped"] = count
                self.send_json(200, result)
                return

            current = mpv_status(prune=True)
            position = current["queuePosition"]
            total = current["queueCount"]
            if not isinstance(position, int) or position < 0:
                raise BadRequest("Nothing is playing.")

            target = position + count
            if target >= total:
                # Skipping past the end stops playback.
                skipped = total - position
                mpv_command("stop")
                mpv_command("playlist-clear")
                _mpv_queue = None
                _current_source = "mpv"
                persist_player_state_locked()
            else:
                skipped = count
                mpv_command("playlist-play-index", target)

            result = mpv_status(prune=True)
            result["skipped"] = skipped
            self.send_json(200, result)

    def handle_remove(self, body):
        source = resolve_source()
        if source == "spotify":
            raise BadRequest(
                "Spotify manages its own queue; remove the track in the Spotify app."
            )

        entry_id = body.get("id")
        if isinstance(entry_id, bool) or not isinstance(entry_id, int):
            raise BadRequest("'id' must be an integer.")

        playlist = get_property("playlist", []) or []
        position = get_property("playlist-pos", -1)
        for index, entry in enumerate(playlist):
            if not isinstance(entry, dict) or entry.get("id") != entry_id:
                continue
            if isinstance(position, int) and index == position:
                raise BadRequest("Cannot remove the currently playing track.")
            if isinstance(position, int) and index < position:
                raise BadRequest("That track has already played.")
            mpv_command("playlist-remove", index)
            result = status()
            result["removedTitle"] = entry.get("title") or entry.get("filename")
            self.send_json(200, result)
            return

        raise BadRequest("That track is no longer in the queue.")

    def handle_move(self, body):
        source = resolve_source()
        if source == "spotify":
            raise BadRequest(
                "Spotify manages its own queue; reorder the track in the Spotify app."
            )

        entry_id = body.get("id")
        target = body.get("position")
        if isinstance(entry_id, bool) or not isinstance(entry_id, int):
            raise BadRequest("'id' must be an integer.")
        if isinstance(target, bool) or not isinstance(target, int) or target < 1:
            raise BadRequest("'position' must be a positive integer.")

        playlist = get_property("playlist", []) or []
        position = get_property("playlist-pos", -1)
        if not isinstance(position, int) or position < 0:
            raise BadRequest("Nothing is playing.")

        upcoming_count = len(playlist) - position - 1
        if target > upcoming_count:
            raise BadRequest(
                f"There are only {upcoming_count} upcoming "
                f"track{'' if upcoming_count == 1 else 's'}."
            )

        for from_index, entry in enumerate(playlist):
            if not isinstance(entry, dict) or entry.get("id") != entry_id:
                continue
            if from_index == position:
                raise BadRequest("Cannot move the currently playing track.")
            if from_index < position:
                raise BadRequest("That track has already played.")

            # Absolute playlist index where the track should end up.
            final_index = position + target
            # mpv's playlist-move takes the index of the entry to insert
            # *before*, so moving a track later needs +1.
            mpv_index = final_index + 1 if from_index < final_index else final_index
            if from_index != final_index:
                mpv_command("playlist-move", from_index, mpv_index)

            result = status()
            result["movedTitle"] = entry.get("title") or entry.get("filename")
            self.send_json(200, result)
            return

        raise BadRequest("That track is no longer in the queue.")

    def handle_clear(self):
        global _current_source, _mpv_queue
        with _player_lock:
            source = resolve_source()
            if source == "spotify":
                raise BadRequest(
                    "Spotify manages its own queue; clear it in the Spotify app."
                )

            playlist = get_property("playlist", []) or []
            position = get_property("playlist-pos", -1)
            removed = 0
            if isinstance(position, int) and position >= 0:
                for index in range(len(playlist) - 1, position, -1):
                    mpv_command("playlist-remove", index)
                    removed += 1
            else:
                removed = len(playlist)
                mpv_command("playlist-clear")

            _mpv_queue = None
            _current_source = "mpv"
            persist_player_state_locked()
            result = mpv_status(prune=True)
            result["removedCount"] = removed
            self.send_json(200, result)

    def handle_stop(self):
        global _current_source, _mpv_queue
        with _player_lock:
            source = resolve_source(force=True)
            if source == "spotify" and SPOTIFY:
                SPOTIFY.pause()
                if _spotify_stream_active:
                    try:
                        mpv_command("set_property", "pause", True)
                    except MpvError:
                        pass
                _current_source = "spotify"
                persist_player_state_locked()
                self.send_json(200, status())
            else:
                mpv_command("stop")
                mpv_command("playlist-clear")
                _mpv_queue = None
                _current_source = "mpv"
                persist_player_state_locked()
                self.send_json(200, mpv_status(prune=True))

    def handle_spotify_link(self):
        if not SPOTIFY:
            raise SpotifyNotConfigured(
                "Spotify is not configured on the music host. Set the Spotify "
                "client ID and redirect URI in the lounge-music NixOS module."
            )
        self.send_json(200, {"ok": True, "url": SPOTIFY.link_url()})

    def handle_spotify_callback(self, body):
        if not SPOTIFY:
            raise SpotifyNotConfigured("Spotify is not configured on the music host.")
        code = body.get("code")
        state = body.get("state")
        if not isinstance(code, str) or not code:
            raise BadRequest("'code' is required.")
        if not isinstance(state, str) or not state:
            raise BadRequest("'state' is required.")
        account = SPOTIFY.complete_link(code, state)
        self.send_json(200, {"ok": True, "account": account})

    def handle_spotify_player_login(self):
        if not SPOTIFY:
            raise SpotifyNotConfigured("Spotify is not configured on the music host.")
        result = SPOTIFY.begin_player_login()
        self.send_json(200, {"ok": True, **result})

    def handle_spotify_disconnect(self):
        global _current_source
        if not SPOTIFY:
            raise SpotifyNotConfigured("Spotify is not configured on the music host.")

        with _player_lock:
            was_spotify = _spotify_stream_active or _current_source == "spotify"
            SPOTIFY.disconnect()
            if was_spotify:
                stop_spotify_stream()
                restore_mpv_volume()
                restore_mpv_queue()
                _current_source = "mpv"
                persist_player_state_locked()
            self.send_json(200, status())

    def handle_volume(self, body):
        global _spotify_volume, _spotify_muted
        value = body.get("volume")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise BadRequest("'volume' must be a number between 0 and 100.")
        volume = max(0, min(MAX_VOLUME, int(round(value))))

        with _player_lock:
            source = resolve_source()
            if source == "spotify" and SPOTIFY:
                SPOTIFY.set_volume(volume)
                with _player_lock:
                    _spotify_volume = volume
                    if volume > 0 and _spotify_muted:
                        _spotify_muted = False
                    persist_player_state_locked()
            else:
                mpv_command("set_property", "volume", volume)
            self.send_json(200, status())

    def handle_mute(self):
        with _player_lock:
            source = resolve_source()
            if source == "spotify" and SPOTIFY:
                SPOTIFY.toggle_mute()
            else:
                mpv_command(
                    "set_property", "mute", not bool(get_property("mute", False))
                )
            self.send_json(200, status())


def main():
    global SPOTIFY

    load_player_state()
    ensure_dir(STATE_DIR, 0o700)
    ensure_dir(CACHE_DIR, 0o700)

    if spotify_configured():
        ensure_dir(SPOTIFY_DIR, 0o700)
        ensure_dir(SPOTIFY_LIBRESPOT_DIR, 0o700)
        SPOTIFY = SpotifyService()
        SPOTIFY.start()
        threading.Thread(
            target=spotify_watch_loop, name="spotify-watcher", daemon=True
        ).start()
        log(
            f"Spotify enabled as {SPOTIFY_DEVICE_NAME!r} "
            f"(redirect {SPOTIFY_REDIRECT_URI})"
        )
    else:
        log("Spotify disabled or not configured")

    server = ThreadingHTTPServer((BIND_ADDRESS, PORT), Handler)
    auth = "token required" if TOKEN else "no token configured"
    log(
        f"listening on {BIND_ADDRESS}:{PORT} ({auth}), "
        f"mpv socket {MPV_SOCKET}"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if SPOTIFY:
            SPOTIFY.stop()
        server.server_close()


if __name__ == "__main__":
    main()
