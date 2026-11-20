#!/usr/bin/env python3
"""HTTP control API for the IEEE TAMU lounge Bambu Lab X2D 3D printer."""

import ftplib
import hashlib
import hmac
import io
import json
import os
import re
import socket
import ssl
import subprocess
import threading
import time
import urllib.parse
import uuid
import xml.etree.ElementTree as ET
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import paho.mqtt.client as mqtt
except ImportError as error:  # pragma: no cover - handled by the systemd unit
    raise SystemExit(
        "bambu-printer-agent: paho-mqtt is required; install python3Packages.paho-mqtt"
    ) from error


PRINTER_IP = os.environ.get("PRINTER_IP", "192.168.1.66")
PRINTER_SERIAL = os.environ.get("PRINTER_SERIAL", "").strip()
BIND_ADDRESS = os.environ.get("BIND_ADDRESS", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8789"))
TOKEN_FILE = os.environ.get("TOKEN_FILE", "")
MAX_FILE_BYTES = int(os.environ.get("MAX_FILE_BYTES", str(100 * 1024 * 1024)))
MAX_UNCOMPRESSED_BYTES = int(
    os.environ.get("MAX_UNCOMPRESSED_BYTES", str(512 * 1024 * 1024))
)
MAX_FILENAME_LENGTH = 160
MAX_JSON_BYTES = 65536
FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
CAMERA_TIMEOUT = int(os.environ.get("CAMERA_TIMEOUT", "30"))
CAMERA_CACHE_SECONDS = float(os.environ.get("CAMERA_CACHE_SECONDS", "2"))
CAMERA_URL = os.environ.get("CAMERA_URL", "").strip()
MQTT_KEEPALIVE = int(os.environ.get("MQTT_KEEPALIVE", "30"))
MQTT_REFRESH_SECONDS = int(os.environ.get("MQTT_REFRESH_SECONDS", "30"))
COMMAND_TIMEOUT_SECONDS = int(os.environ.get("COMMAND_TIMEOUT_SECONDS", "15"))
PRINT_URL_SCHEME = os.environ.get("PRINT_URL_SCHEME", "ftp:///")
SUPPORTED_MODELS = {"N6"}  # Bambu Lab X2D
# X2D raises this hidden camera warning at every boot; Bambu Studio does not
# show it. Keep the Discord status focused on actionable faults.
HIDDEN_HMS = {"0500_0600_0002_0070"}

_sequence_lock = threading.Lock()
_sequence_id = int(time.time()) % 100000


class AgentError(RuntimeError):
    """Raised when the printer cannot be reached or rejects a command."""


class BadRequest(ValueError):
    """Raised for invalid client input."""


class PrinterRejected(AgentError):
    """Raised when the printer acknowledges a command with a failure."""


class PrinterBusy(AgentError):
    """Raised when a print is requested while the printer is already busy."""


class MappingRequired(BadRequest):
    """Raised when a print needs an explicit AMS/filament mapping."""

    def __init__(self, message, filaments, slots):
        super().__init__(message)
        self.filaments = filaments
        self.slots = slots


def load_access_code():
    code = os.environ.get("PRINTER_ACCESS_CODE", "").strip()
    code_file = os.environ.get("ACCESS_CODE_FILE", "")
    if code_file:
        try:
            with open(code_file, "r", encoding="utf-8") as handle:
                code = handle.read().strip()
        except OSError as error:
            print(
                f"bambu-printer-agent: cannot read access code file {code_file}: {error}",
                flush=True,
            )
            raise SystemExit(1) from error
    if not code:
        print(
            "bambu-printer-agent: set ACCESS_CODE_FILE or PRINTER_ACCESS_CODE",
            flush=True,
        )
        raise SystemExit(1)
    return code


def load_token():
    if not TOKEN_FILE:
        return None
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as handle:
            token = handle.read().strip()
    except OSError as error:
        print(
            f"bambu-printer-agent: cannot read token file {TOKEN_FILE}: {error}",
            flush=True,
        )
        raise SystemExit(1) from error
    if not token:
        print(f"bambu-printer-agent: token file {TOKEN_FILE} is empty", flush=True)
        raise SystemExit(1)
    return token


ACCESS_CODE = load_access_code()
TOKEN = load_token()


def next_sequence_id():
    global _sequence_id
    with _sequence_lock:
        _sequence_id = (_sequence_id + 1) % 2147483647
        return str(_sequence_id)


def deep_merge(target, patch):
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            deep_merge(target[key], value)
        else:
            target[key] = value


def safe_filename(value, fallback="print.gcode.3mf"):
    name = os.path.basename((value or "").replace("\\", "/")).strip()
    name = "".join(
        character
        for character in name
        if character.isprintable() and character not in "\r\n\x00"
    )
    name = name[:MAX_FILENAME_LENGTH].strip()
    return name or fallback


def parse_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def sanitize_temp(value):
    temp = parse_float(value)
    if temp is None or temp < -50 or temp > 500:
        return None
    return temp


def parse_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def hex_color(value):
    raw = str(value or "").strip().lstrip("#").upper()
    if len(raw) == 8:
        raw = raw[:6]
    if len(raw) != 6 or any(character not in "0123456789ABCDEF" for character in raw):
        return None
    return f"#{raw}"


def format_hms(hms):
    formatted = []
    for entry in hms or []:
        if not isinstance(entry, dict):
            continue
        attr = parse_int(entry.get("attr"))
        code = parse_int(entry.get("code"))
        if attr is None or code is None:
            continue
        display = (
            f"{attr >> 16:04X}_{attr & 0xFFFF:04X}_"
            f"{code >> 16:04X}_{code & 0xFFFF:04X}"
        )
        if display in HIDDEN_HMS:
            continue
        formatted.append(
            {
                "attr": f"{attr:08X}",
                "code": f"{code:08X}",
                "display": display,
                "timestamp": entry.get("ts_unix"),
            }
        )
    return formatted


class PrinterState:
    def __init__(self):
        self._condition = threading.Condition()
        self._print = {}
        self._modules = []
        self._connected = False
        self._last_update = 0.0
        self._last_error = None
        self._responses = {}
        self._last_response = None

    def update_print(self, patch):
        with self._condition:
            deep_merge(self._print, patch)
            self._last_update = time.time()
            self._connected = True
            self._last_error = None
            self._condition.notify_all()

    def set_modules(self, modules):
        with self._condition:
            self._modules = modules
            self._last_update = time.time()
            self._condition.notify_all()

    def set_connected(self, connected, error=None):
        with self._condition:
            self._connected = connected
            if error:
                self._last_error = error
            self._condition.notify_all()

    def record_response(self, response):
        sequence = str(response.get("sequence_id", ""))
        recorded = {**response, "received_at": time.time()}
        with self._condition:
            self._last_response = recorded
            if sequence:
                self._responses[sequence] = recorded
                # Keep the response map bounded.
                if len(self._responses) > 50:
                    oldest = sorted(
                        self._responses.items(),
                        key=lambda item: item[1].get("received_at", 0),
                    )
                    for key, _ in oldest[: len(self._responses) - 50]:
                        self._responses.pop(key, None)
            self._condition.notify_all()

    def wait_for_command(
        self, sequence, command=None, started_at=0.0, timeout=COMMAND_TIMEOUT_SECONDS
    ):
        deadline = time.time() + timeout
        with self._condition:
            while True:
                response = self._responses.get(sequence)
                if response is not None:
                    return response
                last = self._last_response
                if (
                    last is not None
                    and command is not None
                    and last.get("command") == command
                    and last.get("received_at", 0) >= started_at
                ):
                    return last
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)

    def snapshot(self):
        with self._condition:
            return {
                "print": dict(self._print),
                "modules": list(self._modules),
                "connected": self._connected,
                "lastUpdate": self._last_update,
                "lastError": self._last_error,
            }

    def wait_for_data(self, timeout=8):
        deadline = time.time() + timeout
        with self._condition:
            while not self._last_update:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True


STATE = PrinterState()

# Uploads waiting for the user to choose an explicit filament mapping.
UPLOADS = {}
UPLOADS_LOCK = threading.Lock()
UPLOAD_TTL_SECONDS = int(os.environ.get("UPLOAD_TTL_SECONDS", "3600"))
MAX_PENDING_UPLOADS = int(os.environ.get("MAX_PENDING_UPLOADS", "64"))


def prune_uploads():
    now = time.time()
    with UPLOADS_LOCK:
        for key, value in list(UPLOADS.items()):
            if now - value["createdAt"] > UPLOAD_TTL_SECONDS:
                UPLOADS.pop(key, None)
        if len(UPLOADS) > MAX_PENDING_UPLOADS:
            oldest = sorted(UPLOADS.items(), key=lambda item: item[1]["createdAt"])
            for key, _ in oldest[: len(UPLOADS) - MAX_PENDING_UPLOADS]:
                UPLOADS.pop(key, None)


def store_upload(plan, remote_name, options, snapshot):
    prune_uploads()
    upload_id = uuid.uuid4().hex
    with UPLOADS_LOCK:
        UPLOADS[upload_id] = {
            "plan": plan,
            "remoteName": remote_name,
            "options": options,
            "createdAt": time.time(),
            "printer": snapshot["printer"]["name"],
        }
    return upload_id


def get_upload(upload_id):
    prune_uploads()
    with UPLOADS_LOCK:
        return UPLOADS.get(upload_id)


def delete_upload(upload_id):
    with UPLOADS_LOCK:
        UPLOADS.pop(upload_id, None)


class MqttWorker(threading.Thread):
    def __init__(self, ip, serial, access_code, state):
        super().__init__(name="bambu-mqtt", daemon=True)
        self.ip = ip
        self.serial = serial
        self.access_code = access_code
        self.state = state
        self.report_topic = f"device/{serial}/report"
        self.request_topic = f"device/{serial}/request"
        self._stop = threading.Event()
        self._client = None
        self._client_lock = threading.Lock()

    def stop(self):
        self._stop.set()
        with self._client_lock:
            client = self._client
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass

    def publish_command(self, section, command, fields=None, qos=1):
        with self._client_lock:
            client = self._client
        if client is None or not client.is_connected():
            raise AgentError("the printer MQTT connection is not available")
        sequence = next_sequence_id()
        payload = {"sequence_id": sequence, "command": command}
        payload.update(fields or {})
        client.publish(
            self.request_topic,
            json.dumps({section: payload}, separators=(",", ":")),
            qos=qos,
        )
        return sequence

    def _make_client(self):
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"ieee-bambu-{os.getpid()}-{int(time.time())}",
            protocol=mqtt.MQTTv311,
            reconnect_on_failure=False,
        )
        client.username_pw_set("bblp", self.access_code)
        client.tls_set(cert_reqs=ssl.CERT_NONE)
        client.tls_insecure_set(True)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        return client

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            self.state.set_connected(False, f"MQTT connect failed: {reason_code}")
            return
        client.subscribe(self.report_topic, qos=1)
        client.publish(
            self.request_topic,
            json.dumps(
                {"pushing": {"sequence_id": next_sequence_id(), "command": "pushall"}},
                separators=(",", ":"),
            ),
            qos=1,
        )
        client.publish(
            self.request_topic,
            json.dumps(
                {"info": {"sequence_id": next_sequence_id(), "command": "get_version"}},
                separators=(",", ":"),
            ),
            qos=1,
        )
        self.state.set_connected(True)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        self.state.set_connected(False, f"MQTT disconnected: {reason_code}")

    def _on_message(self, client, userdata, message):
        try:
            payload = json.loads(message.payload.decode("utf-8", "replace"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        print_section = payload.get("print")
        if isinstance(print_section, dict):
            if "command" in print_section and (
                "result" in print_section or "reason" in print_section
            ):
                self.state.record_response(print_section)
            else:
                self.state.update_print(print_section)
        info_section = payload.get("info")
        if isinstance(info_section, dict):
            modules = info_section.get("module")
            if isinstance(modules, list):
                self.state.set_modules(modules)

    def run(self):
        while not self._stop.is_set():
            client = self._make_client()
            with self._client_lock:
                self._client = client
            try:
                client.connect(self.ip, 8883, keepalive=MQTT_KEEPALIVE)
                client.loop_start()
                last_refresh = time.time()
                while not self._stop.wait(1):
                    if not client.is_connected():
                        break
                    if time.time() - last_refresh >= MQTT_REFRESH_SECONDS:
                        client.publish(
                            self.request_topic,
                            json.dumps(
                                {
                                    "pushing": {
                                        "sequence_id": next_sequence_id(),
                                        "command": "pushall",
                                    }
                                },
                                separators=(",", ":"),
                            ),
                            qos=1,
                        )
                        last_refresh = time.time()
            except Exception as error:
                self.state.set_connected(False, str(error))
            finally:
                try:
                    client.loop_stop()
                except Exception:
                    pass
                try:
                    client.disconnect()
                except Exception:
                    pass
                with self._client_lock:
                    self._client = None
            if not self._stop.is_set():
                self._stop.wait(5)


MQTT_WORKER = None


class ImplicitFTP_TLS(ftplib.FTP_TLS):
    """FTPS client for Bambu's implicit-TLS FTP server.

    Bambu requires the data connection to reuse the control connection's TLS
    session, otherwise directory listings and uploads fail with 522.
    """

    def connect(self, host="", port=990, timeout=30, source_address=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.source_address = source_address
        self.sock = socket.create_connection((host, port), timeout, source_address)
        self.af = self.sock.family
        self.sock = self.context.wrap_socket(self.sock, server_hostname=host)
        self.file = self.sock.makefile("r", encoding=self.encoding)
        self.welcome = self.getresp()
        return self.welcome

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(
                conn,
                server_hostname=self.host,
                session=self.sock.session,
            )
        return conn, size


def ftp_connection():
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    ftp = ImplicitFTP_TLS(context=context)
    ftp.connect(PRINTER_IP, 990, timeout=30)
    ftp.login("bblp", ACCESS_CODE)
    ftp.prot_p()
    return ftp


def upload_3mf(data, remote_name):
    ftp = ftp_connection()
    try:
        ftp.storbinary(
            f"STOR {remote_name}",
            io.BytesIO(data),
            blocksize=64 * 1024,
        )
        try:
            size = ftp.size(remote_name)
        except ftplib.all_errors:
            size = None
        if size is not None and size != len(data):
            raise AgentError(
                f"upload verification failed: printer has {size} bytes, expected {len(data)}"
            )
    except ftplib.all_errors as error:
        raise AgentError(f"FTPS upload failed: {error}") from error
    finally:
        try:
            ftp.quit()
        except Exception:
            pass


def analyze_3mf(data, filename, plate=1):
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise BadRequest("That file is not a valid .3mf archive.") from error

    with archive:
        names = set(archive.namelist())
        total_size = sum(
            info.file_size for info in archive.infolist() if not info.is_dir()
        )
        if total_size > MAX_UNCOMPRESSED_BYTES:
            raise BadRequest(
                f"The .3mf expands to more than {MAX_UNCOMPRESSED_BYTES // (1024 * 1024)} MB."
            )

        gcode_path = f"Metadata/plate_{plate}.gcode"
        if gcode_path not in names:
            available = sorted(
                re.findall(r"Metadata/plate_(\d+)\.gcode", "\n".join(names)),
                key=int,
            )
            plates = ", ".join(available) if available else "none"
            raise BadRequest(
                f"Plate {plate} has no sliced G-code in this file (available plates: {plates})."
            )

        try:
            gcode = archive.read(gcode_path)
        except (KeyError, zipfile.BadZipFile) as error:
            raise BadRequest("Could not read the sliced G-code from the .3mf.") from error
        if not gcode:
            raise BadRequest("The sliced G-code inside the .3mf is empty.")

        plate_meta = {}
        plate_json_path = f"Metadata/plate_{plate}.json"
        if plate_json_path in names:
            try:
                plate_meta = json.loads(archive.read(plate_json_path))
            except (KeyError, json.JSONDecodeError, UnicodeDecodeError):
                plate_meta = {}

        slice_meta = {}
        filaments = []
        if "Metadata/slice_info.config" in names:
            try:
                slice_meta, filaments = parse_slice_info(
                    archive.read("Metadata/slice_info.config"), plate
                )
            except (ET.ParseError, KeyError, UnicodeDecodeError):
                slice_meta = {}
                filaments = []

        if not filaments:
            colors = plate_meta.get("filament_colors") or ["#000000"]
            ids = plate_meta.get("filament_ids") or [1]
            filaments = [
                {
                    "id": parse_int(ids[index], index + 1),
                    "type": None,
                    "color": hex_color(color),
                }
                for index, color in enumerate(colors)
            ]

        model_id = str(slice_meta.get("printer_model_id") or "").strip()
        if model_id and model_id not in SUPPORTED_MODELS:
            raise BadRequest(
                f"This file was sliced for printer model {model_id}; "
                "the lounge printer is a Bambu Lab X2D (N6). Re-slice for the X2D."
            )

        md5_path = f"Metadata/plate_{plate}.gcode.md5"
        expected_md5 = None
        if md5_path in names:
            expected_md5 = (
                archive.read(md5_path).decode("ascii", "ignore").strip().upper()
            )
        computed_md5 = hashlib.md5(gcode).hexdigest().upper()
        if expected_md5 and expected_md5 != computed_md5:
            raise BadRequest(
                "The .3mf is corrupt: its plate MD5 does not match the sliced G-code."
            )

        mapping_length = 0
        if "Metadata/filament_sequence.json" in names:
            try:
                sequence_data = json.loads(
                    archive.read("Metadata/filament_sequence.json")
                )
                plate_sequence = sequence_data.get(f"plate_{plate}") or {}
                optimal = plate_sequence.get("optimal_assignment")
                if isinstance(optimal, list) and optimal:
                    mapping_length = len(optimal)
            except (KeyError, json.JSONDecodeError, UnicodeDecodeError, AttributeError):
                mapping_length = 0
        if not mapping_length:
            filament_maps = str(slice_meta.get("filament_maps") or "")
            if filament_maps:
                mapping_length = len(filament_maps.split())
        if not mapping_length:
            mapping_length = len(filaments)
        max_filament_id = max(
            (parse_int(filament.get("id"), 0) or 0 for filament in filaments),
            default=0,
        )
        mapping_length = max(mapping_length, max_filament_id, len(filaments))

        nozzle_diameter = parse_float(
            plate_meta.get("nozzle_diameter")
            or str(slice_meta.get("nozzle_diameters", "")).split(",")[0]
            or "0.4",
            0.4,
        )
        return {
            "gcode_path": gcode_path,
            "gcode_size": len(gcode),
            "md5": computed_md5,
            "plate": plate,
            "filaments": filaments,
            "mapping_length": mapping_length,
            "nozzle_diameter": nozzle_diameter,
            "bed_type": str(plate_meta.get("bed_type") or "auto"),
            "model_id": model_id or None,
            "prediction_seconds": parse_int(slice_meta.get("prediction")),
            "weight_grams": parse_float(slice_meta.get("weight")),
            "filename": filename,
        }


def parse_slice_info(raw, plate):
    root = ET.fromstring(raw)
    for plate_element in root.findall("plate"):
        meta = {
            item.get("key"): item.get("value")
            for item in plate_element.findall("metadata")
            if item.get("key")
        }
        if parse_int(meta.get("index"), -1) != plate:
            continue
        filaments = []
        for item in plate_element.findall("filament"):
            filaments.append(
                {
                    "id": parse_int(item.get("id")),
                    "type": (item.get("type") or "").strip() or None,
                    "color": hex_color(item.get("color")),
                    "tray_info_idx": (item.get("tray_info_idx") or "").strip() or None,
                    "nozzle_diameter": parse_float(item.get("nozzle_diameter")),
                    "used_grams": parse_float(item.get("used_g")),
                }
            )
        return meta, filaments
    raise KeyError(f"plate {plate} not found in slice_info.config")


def printer_snapshot():
    snapshot = STATE.snapshot()
    print_state = snapshot["print"]
    device = print_state.get("device") if isinstance(print_state.get("device"), dict) else {}

    modules = snapshot["modules"]
    printer_module = next(
        (
            module
            for module in modules
            if isinstance(module, dict) and module.get("name") == "ota"
        ),
        {},
    )
    ams_modules = [
        module
        for module in modules
        if isinstance(module, dict) and str(module.get("name", "")).startswith("n3f")
    ]

    fun = None
    try:
        fun = int(str(print_state.get("fun", "")), 16)
    except ValueError:
        fun = None
    signed_required = bool(fun & 0x20000000) if fun is not None else None
    developer_mode = (not signed_required) if signed_required is not None else None

    nozzle_section = device.get("nozzle") if isinstance(device.get("nozzle"), dict) else {}
    nozzle_metadata = nozzle_section.get("info") or []
    extruders = device.get("extruder", {}).get("info") or []
    active_nozzle = parse_int(nozzle_section.get("tar_id"), 0)
    active_nozzle_temp = sanitize_temp(print_state.get("nozzle_temper"))
    active_nozzle_target = sanitize_temp(print_state.get("nozzle_target_temper"))
    nozzles = []
    for index, nozzle in enumerate(nozzle_metadata or extruders):
        if not isinstance(nozzle, dict):
            nozzle = {}
        is_active = index == active_nozzle
        nozzles.append(
            {
                "index": index,
                "current": active_nozzle_temp if is_active else None,
                "target": active_nozzle_target if is_active else None,
                "active": is_active,
                "type": nozzle.get("type") or print_state.get("nozzle_type"),
                "diameter": parse_float(
                    nozzle.get("diameter") or print_state.get("nozzle_diameter")
                ),
                "wear": parse_float(nozzle.get("wear")),
            }
        )

    ctc = device.get("ctc") if isinstance(device.get("ctc"), dict) else {}
    chamber = sanitize_temp((ctc.get("info") or {}).get("temp"))
    if chamber is None:
        chamber = sanitize_temp(print_state.get("chamber_temper"))

    ams = print_state.get("ams") if isinstance(print_state.get("ams"), dict) else {}
    tray_now = parse_int(ams.get("tray_now"), 255)
    slots = []
    units = []
    for unit in ams.get("ams") or []:
        if not isinstance(unit, dict):
            continue
        unit_id = parse_int(unit.get("id"), 0)
        unit_slots = []
        for tray in unit.get("tray") or []:
            if not isinstance(tray, dict):
                continue
            tray_id = parse_int(tray.get("id"), 0)
            global_id = unit_id * 4 + tray_id
            slot = {
                "globalId": global_id,
                "amsId": unit_id,
                "slotId": tray_id,
                "label": f"AMS {unit_id + 1} slot {tray_id + 1}",
                "type": (tray.get("tray_type") or "").strip() or None,
                "color": hex_color(tray.get("tray_color")),
                "remain": parse_int(tray.get("remain")),
                "nozzleTempMin": parse_int(tray.get("nozzle_temp_min")),
                "nozzleTempMax": parse_int(tray.get("nozzle_temp_max")),
                "state": parse_int(tray.get("state")),
                "active": global_id == tray_now,
                "external": False,
            }
            unit_slots.append(slot)
            slots.append(slot)
        units.append(
            {
                "id": unit_id,
                "humidity": parse_int(unit.get("humidity")),
                "humidityRaw": parse_int(unit.get("humidity_raw")),
                "temperature": parse_float(unit.get("temp")),
                "drying": parse_int(unit.get("dry_time")),
                "trays": unit_slots,
            }
        )

    external = print_state.get("vt_tray")
    if isinstance(external, dict) and external.get("tray_type"):
        slot = {
            "globalId": 254,
            "amsId": 254,
            "slotId": 0,
            "label": "External spool",
            "type": (external.get("tray_type") or "").strip() or None,
            "color": hex_color(external.get("tray_color")),
            "remain": parse_int(external.get("remain")),
            "nozzleTempMin": parse_int(external.get("nozzle_temp_min")),
            "nozzleTempMax": parse_int(external.get("nozzle_temp_max")),
            "state": parse_int(external.get("state")),
            "active": tray_now == 254,
            "external": True,
        }
        slots.append(slot)

    gcode_state = str(print_state.get("gcode_state") or "UNKNOWN").upper()
    state_labels = {
        "IDLE": "Idle",
        "PREPARE": "Preparing",
        "RUNNING": "Printing",
        "PAUSE": "Paused",
        "FINISH": "Finished",
        "FAILED": "Failed",
        "SLICING": "Slicing",
    }
    progress = parse_int(print_state.get("mc_percent"))
    if progress is None:
        progress = parse_int(print_state.get("percent"))

    errors = format_hms(print_state.get("hms"))
    print_error = parse_int(print_state.get("print_error"), 0)
    mc_error = parse_int(print_state.get("mc_err"), 0)
    fail_reason = str(print_state.get("fail_reason") or "0")

    ipcam = print_state.get("ipcam")
    if not isinstance(ipcam, dict):
        ipcam = {}
    camera_available = bool(CAMERA_URL) or bool(
        ipcam.get("rtsp_url") and ipcam.get("rtsp_url") != "disable"
    )

    return {
        "ok": True,
        "printer": {
            "name": printer_module.get("product_name") or "Bambu Lab X2D",
            "serial": PRINTER_SERIAL,
            "ip": PRINTER_IP,
            "firmware": printer_module.get("sw_ver"),
            "amsModules": [
                {
                    "name": module.get("product_name") or module.get("name"),
                    "serial": module.get("sn"),
                    "firmware": module.get("sw_ver"),
                }
                for module in ams_modules
            ],
            "state": gcode_state,
            "stateLabel": state_labels.get(gcode_state, gcode_state.title()),
            "progress": progress,
            "remainingMinutes": parse_int(print_state.get("mc_remaining_time")),
            "layer": parse_int(print_state.get("layer_num")),
            "totalLayers": parse_int(print_state.get("total_layer_num")),
            "subtask": print_state.get("subtask_name") or None,
            "gcodeFile": print_state.get("gcode_file") or None,
            "wifi": print_state.get("wifi_signal"),
            "sdcard": print_state.get("sdcard"),
            "developerMode": developer_mode,
            "signedCommandsRequired": signed_required,
            "errors": {
                "printError": print_error,
                "mcError": mc_error,
                "failReason": fail_reason,
                "hms": errors,
            },
        },
        "temperatures": {
            "bed": {
                "current": sanitize_temp(print_state.get("bed_temper")),
                "target": sanitize_temp(print_state.get("bed_target_temper")),
            },
            "nozzles": nozzles,
            "chamber": {"current": chamber, "target": None},
        },
        "fans": {
            "partCooling": parse_int(print_state.get("cooling_fan_speed")),
            "auxiliary": parse_int(print_state.get("big_fan1_speed")),
            "chamber": parse_int(print_state.get("big_fan2_speed")),
            "heatbreak": parse_int(print_state.get("heatbreak_fan_speed")),
        },
        "ams": {
            "units": units,
            "slots": slots,
            "activeTray": None if tray_now == 255 else tray_now,
            "targetTray": None if parse_int(ams.get("tray_tar"), 255) == 255 else parse_int(ams.get("tray_tar"), 255),
        },
        "camera": {"available": camera_available},
        "capabilities": {"startPrint": developer_mode is not False},
        "connection": {
            "mqtt": snapshot["connected"],
            "lastUpdate": snapshot["lastUpdate"] or None,
            "lastError": snapshot["lastError"],
        },
    }


def ams_slots():
    snapshot = printer_snapshot()
    return snapshot["ams"]["slots"]


def synthetic_external_slot(global_id):
    return {
        "globalId": global_id,
        "amsId": global_id,
        "slotId": 0,
        "label": "External spool (right)" if global_id == 255 else "External spool",
        "external": True,
        "type": None,
        "color": None,
    }


def resolve_mapping(filaments, mapping_value):
    slots = ams_slots()
    if not slots:
        raise MappingRequired(
            "No AMS slots or external spool are reporting filament. Load filament and try again.",
            filaments,
            slots,
        )

    requested = (mapping_value or "").strip()
    if not requested or requested.lower() == "auto":
        raise MappingRequired(
            "Choose an explicit filament slot for every sliced filament.",
            filaments,
            slots,
        )

    tokens = [token.strip() for token in requested.split(",") if token.strip()]
    if len(tokens) != len(filaments):
        raise MappingRequired(
            f"This print uses {len(filaments)} filament(s); provide {len(filaments)} comma-separated slot(s).",
            filaments,
            slots,
        )
    selected = []
    for token in tokens:
        lowered = token.lower()
        if lowered in {"ext", "external", "ext1", "254"}:
            selected.append(
                next((slot for slot in slots if slot["external"]), None)
                or synthetic_external_slot(254)
            )
        elif lowered in {"ext2", "255"}:
            selected.append(synthetic_external_slot(255))
        else:
            index = parse_int(token)
            if index is None or index < 1:
                raise BadRequest(
                    f"Invalid filament slot {token!r}; use 1-{len(slots)} or 'ext'."
                )
            selected.append(slots[index - 1] if index <= len(slots) else None)
    if any(slot is None for slot in selected):
        raise MappingRequired(
            "One or more selected filament slots do not exist on the printer.",
            filaments,
            slots,
        )
    return selected, []


def resolve_mapping_values(filaments, values, slots):
    if not slots:
        raise MappingRequired(
            "No AMS slots or external spool are reporting filament. Load filament and try again.",
            filaments,
            slots,
        )
    if not isinstance(values, list) or len(values) != len(filaments):
        raise MappingRequired(
            f"This print uses {len(filaments)} filament(s); choose one slot for each.",
            filaments,
            slots,
        )

    selected = []
    for value in values:
        slot = None
        if isinstance(value, bool):
            slot = None
        elif isinstance(value, int):
            global_id = value
            slot = next((candidate for candidate in slots if candidate["globalId"] == global_id), None)
            if slot is None and global_id in (254, 255):
                slot = synthetic_external_slot(global_id)
        elif isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"ext", "external", "ext1"}:
                slot = next((candidate for candidate in slots if candidate["external"]), None)
                if slot is None:
                    slot = synthetic_external_slot(254)
            elif lowered in {"ext2", "255"}:
                slot = synthetic_external_slot(255)
            elif lowered.lstrip("-").isdigit():
                global_id = int(lowered)
                slot = next(
                    (candidate for candidate in slots if candidate["globalId"] == global_id),
                    None,
                )
                if slot is None and global_id in (254, 255):
                    slot = synthetic_external_slot(global_id)
        if slot is None:
            raise MappingRequired(
                "One or more selected filament slots do not exist on the printer.",
                filaments,
                slots,
            )
        selected.append(slot)
    return selected


def mapping_payload(selected, filaments, mapping_length=None):
    # slice_info.config filament ids are 1-based; ams_mapping is a 0-based
    # project-length lookup table. Unused filament slots stay -1 / 255.255.
    indices = []
    for position, filament in enumerate(filaments):
        filament_id = parse_int(filament.get("id"))
        index = filament_id - 1 if filament_id and filament_id >= 1 else position
        indices.append(max(0, index))
    length = max(mapping_length or 0, len(filaments))
    if indices:
        length = max(length, max(indices) + 1)

    flat = [-1] * length
    detailed = [{"ams_id": 255, "slot_id": 255} for _ in range(length)]
    use_ams = False
    for position, slot in enumerate(selected):
        index = indices[position] if position < len(indices) else position
        if slot is None:
            continue
        global_id = slot["globalId"]
        if slot.get("external"):
            detailed[index] = {
                "ams_id": global_id if global_id in (254, 255) else 254,
                "slot_id": 0,
            }
        else:
            use_ams = True
            flat[index] = global_id
            detailed[index] = {
                "ams_id": slot["amsId"],
                "slot_id": slot["slotId"],
            }
    return flat, detailed, use_ams


def normalize_options(options):
    options = options or {}
    return {
        "bedLeveling": parse_bool(options.get("bed_leveling"), True),
        "flowCali": parse_bool(options.get("flow_cali"), False),
        "vibrationCali": parse_bool(options.get("vibration_cali"), False),
        "layerInspect": parse_bool(options.get("layer_inspect"), True),
        "timelapse": parse_bool(options.get("timelapse"), False),
    }


def filament_summary(filaments, selected=None):
    return [
        {
            "index": index + 1,
            "id": filament.get("id"),
            "type": filament.get("type"),
            "color": filament.get("color"),
            "grams": filament.get("used_grams"),
            "slot": (
                selected[index]["label"]
                if selected is not None and index < len(selected)
                else None
            ),
            "globalTray": (
                selected[index]["globalId"]
                if selected is not None and index < len(selected)
                else None
            ),
        }
        for index, filament in enumerate(filaments)
    ]


def upload_response(plan, remote_name, upload_id, snapshot, options):
    return {
        "ok": True,
        "uploaded": True,
        "started": False,
        "uploadId": upload_id,
        "filename": remote_name,
        "plate": plan["plate"],
        "subtask": re.sub(
            r"\.gcode\.3mf$|\.3mf$",
            "",
            remote_name,
            flags=re.IGNORECASE,
        ),
        "printer": snapshot["printer"]["name"],
        "printerState": snapshot["printer"]["state"],
        "capabilities": snapshot["capabilities"],
        "filaments": filament_summary(plan["filaments"]),
        "slots": snapshot["ams"]["slots"],
        "settings": options,
        "warnings": [],
    }


def start_plan(plan, remote_name, selected, options, snapshot):
    printer = snapshot["printer"]
    if printer["signedCommandsRequired"]:
        raise AgentError(
            "The printer is rejecting unsigned MQTT commands (Developer Mode is off). "
            "Enable Settings → LAN Only → Developer Mode on the X2D, then try again."
        )
    busy_states = {"PREPARE", "RUNNING", "PAUSE", "SLICING"}
    if printer["state"] in busy_states:
        raise PrinterBusy(
            f"The printer is busy ({printer['stateLabel']}). "
            "Wait for the current job to finish or stop it before starting another."
        )

    flat_mapping, detailed_mapping, use_ams = mapping_payload(
        selected,
        plan["filaments"],
        plan.get("mapping_length"),
    )
    subtask_name = re.sub(
        r"\.gcode\.3mf$|\.3mf$",
        "",
        remote_name,
        flags=re.IGNORECASE,
    )
    fields = {
        "param": plan["gcode_path"],
        "project_id": "0",
        "profile_id": "0",
        "task_id": "0",
        "subtask_id": "0",
        "subtask_name": subtask_name,
        "file": remote_name,
        "url": f"{PRINT_URL_SCHEME}{urllib.parse.quote(remote_name)}",
        "md5": plan["md5"],
        "bed_type": "auto",
        "bed_leveling": options.get("bedLeveling", True),
        "flow_cali": options.get("flowCali", False),
        "vibration_cali": options.get("vibrationCali", False),
        "layer_inspect": options.get("layerInspect", True),
        "timelapse": options.get("timelapse", False),
        "use_ams": use_ams,
    }
    if any(slot is not None for slot in selected):
        fields["ams_mapping"] = flat_mapping
        fields["ams_mapping2"] = detailed_mapping

    warnings = []
    started_at = time.time()
    sequence = MQTT_WORKER.publish_command("print", "project_file", fields)
    response = STATE.wait_for_command(
        sequence,
        "project_file",
        started_at,
        COMMAND_TIMEOUT_SECONDS,
    )
    if response is not None:
        result = str(response.get("result") or "").strip().lower()
        if result and result not in {"success", "ok"}:
            reason = response.get("reason") or result
            raise PrinterRejected(f"printer rejected the print: {reason}")
    else:
        warnings.append(
            "The printer did not acknowledge the command in time; check /3dprint-status."
        )

    return {
        "ok": True,
        "filename": remote_name,
        "plate": plan["plate"],
        "subtask": subtask_name,
        "printer": printer["name"],
        "filaments": filament_summary(plan["filaments"], selected),
        "warnings": warnings,
        "settings": {
            "useAms": use_ams,
            "amsMapping": flat_mapping if use_ams else None,
            "amsMapping2": detailed_mapping if use_ams else None,
            "bedLeveling": fields["bed_leveling"],
            "flowCali": fields["flow_cali"],
            "vibrationCali": fields["vibration_cali"],
            "layerInspect": fields["layer_inspect"],
            "timelapse": fields["timelapse"],
        },
        "printerResponse": response,
    }


def print_file(data, filename, plate=1, mapping_value="", options=None):
    options = options or {}
    if not filename.lower().endswith(".gcode.3mf"):
        raise BadRequest("The file must be a pre-sliced .gcode.3mf.")
    if len(data) > MAX_FILE_BYTES:
        raise BadRequest(
            f"That file is too large; the limit is {MAX_FILE_BYTES // (1024 * 1024)} MB."
        )

    plan = analyze_3mf(data, filename, plate)
    if not STATE.wait_for_data():
        raise AgentError("no status received from the printer yet")
    snapshot = printer_snapshot()
    remote_name = safe_filename(filename)
    normalized = normalize_options(options)

    if options.get("dry_run"):
        return {
            "ok": True,
            "dryRun": True,
            "filename": remote_name,
            "plate": plan["plate"],
            "subtask": re.sub(
                r"\.gcode\.3mf$|\.3mf$",
                "",
                remote_name,
                flags=re.IGNORECASE,
            ),
            "printer": snapshot["printer"]["name"],
            "filaments": filament_summary(plan["filaments"]),
            "slots": snapshot["ams"]["slots"],
            "capabilities": snapshot["capabilities"],
            "printerState": snapshot["printer"]["state"],
            "warnings": [],
        }

    if options.get("upload_only"):
        upload_3mf(data, remote_name)
        upload_id = store_upload(plan, remote_name, normalized, snapshot)
        return upload_response(plan, remote_name, upload_id, snapshot, normalized)

    selected, warnings = resolve_mapping(plan["filaments"], mapping_value)
    upload_3mf(data, remote_name)
    result = start_plan(plan, remote_name, selected, normalized, snapshot)
    result["warnings"] = warnings + result.get("warnings", [])
    return result


_snapshot_lock = threading.Lock()
_snapshot_cache = {"at": 0.0, "bytes": None}


def camera_url():
    if CAMERA_URL:
        return CAMERA_URL
    snapshot = STATE.snapshot()
    print_state = snapshot["print"]
    reported = str((print_state.get("ipcam") or {}).get("rtsp_url") or "").strip()
    if reported and reported != "disable":
        parts = urllib.parse.urlsplit(reported)
        port = parts.port or 322
        path = parts.path or "/streaming/live/1"
        username = urllib.parse.quote("bblp", safe="")
        password = urllib.parse.quote(ACCESS_CODE, safe="")
        return urllib.parse.urlunsplit(
            ("rtsps", f"{username}:{password}@{PRINTER_IP}:{port}", path, "", "")
        )
    username = urllib.parse.quote("bblp", safe="")
    password = urllib.parse.quote(ACCESS_CODE, safe="")
    return f"rtsps://{username}:{password}@{PRINTER_IP}:322/streaming/live/1"


def capture_snapshot():
    now = time.time()
    with _snapshot_lock:
        cached = _snapshot_cache["bytes"]
        if cached and now - _snapshot_cache["at"] < CAMERA_CACHE_SECONDS:
            return cached
        url = camera_url()
        args = [
            FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-rtsp_transport",
            "tcp",
            "-tls_verify",
            "0",
            "-i",
            url,
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "-",
        ]
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                timeout=CAMERA_TIMEOUT,
                check=False,
            )
        except FileNotFoundError as error:
            raise AgentError(f"missing command: {FFMPEG}") from error
        except subprocess.TimeoutExpired as error:
            raise AgentError("camera snapshot timed out") from error
        if result.returncode != 0 or not result.stdout.startswith(b"\xff\xd8\xff"):
            detail = result.stderr.decode("utf-8", "replace").strip()
            raise AgentError(f"camera snapshot failed: {detail or 'no image returned'}")
        _snapshot_cache["at"] = now
        _snapshot_cache["bytes"] = result.stdout
        return result.stdout


class Handler(BaseHTTPRequestHandler):
    server_version = "bambu-printer-agent/0.1"

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def send_json(self, status_code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, status_code, content_type, body):
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
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

    def handle_error(self, error):
        if isinstance(error, MappingRequired):
            self.send_json(
                400,
                {
                    "ok": False,
                    "error": str(error),
                    "needsMapping": True,
                    "filaments": error.filaments,
                    "slots": error.slots,
                },
            )
            return
        if isinstance(error, BadRequest):
            self.send_json(400, {"ok": False, "error": str(error)})
            return
        if isinstance(error, PrinterRejected):
            self.send_json(502, {"ok": False, "error": str(error)})
            return
        if isinstance(error, PrinterBusy):
            self.send_json(409, {"ok": False, "error": str(error), "busy": True})
            return
        if isinstance(error, AgentError):
            self.send_json(503, {"ok": False, "error": str(error)})
            return
        print(f"bambu-printer-agent: unhandled error: {error!r}", flush=True)
        self.send_json(500, {"ok": False, "error": "internal server error"})

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/health":
            self.send_json(200, {"ok": True})
            return
        if not self.authorized():
            self.send_json(401, {"ok": False, "error": "unauthorized"})
            return
        try:
            if path == "/status":
                if not STATE.wait_for_data():
                    raise AgentError("no status received from the printer yet")
                self.send_json(200, printer_snapshot())
            elif path == "/snapshot":
                self.send_bytes(200, "image/jpeg", capture_snapshot())
            elif path == "/filaments":
                snapshot = printer_snapshot()
                self.send_json(
                    200,
                    {
                        "ok": True,
                        "slots": snapshot["ams"]["slots"],
                        "activeTray": snapshot["ams"]["activeTray"],
                    },
                )
            else:
                self.send_json(404, {"ok": False, "error": "not found"})
        except Exception as error:
            self.handle_error(error)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if not self.authorized():
            self.send_json(401, {"ok": False, "error": "unauthorized"})
            return
        try:
            if path == "/print":
                query = urllib.parse.parse_qs(parsed.query)
                filename = safe_filename(
                    (query.get("filename") or [""])[0], "print.gcode.3mf"
                )
                plate = parse_int((query.get("plate") or ["1"])[0], 1)
                if plate is None or plate < 1 or plate > 64:
                    raise BadRequest("'plate' must be between 1 and 64.")
                mapping_value = (query.get("filament_map") or [""])[0]
                options = {
                    "bed_leveling": (query.get("bed_leveling") or ["true"])[0],
                    "flow_cali": (query.get("flow_cali") or ["false"])[0],
                    "vibration_cali": (query.get("vibration_cali") or ["false"])[0],
                    "layer_inspect": (query.get("layer_inspect") or ["true"])[0],
                    "timelapse": (query.get("timelapse") or ["false"])[0],
                    "dry_run": parse_bool((query.get("dry_run") or ["false"])[0], False),
                    "upload_only": not parse_bool(
                        (query.get("start") or ["true"])[0], True
                    )
                    or parse_bool((query.get("upload_only") or ["false"])[0], False),
                }
                data = self.read_body()
                self.send_json(
                    200,
                    print_file(data, filename, plate, mapping_value, options),
                )
            elif path == "/start":
                payload = self.read_json()
                upload_id = payload.get("uploadId") or payload.get("upload_id")
                if not isinstance(upload_id, str) or not upload_id:
                    raise BadRequest("'uploadId' is required.")
                upload = get_upload(upload_id)
                if upload is None:
                    raise BadRequest(
                        "That upload expired; upload the .gcode.3mf again."
                    )
                if not STATE.wait_for_data():
                    raise AgentError("no status received from the printer yet")
                snapshot = printer_snapshot()
                slots = snapshot["ams"]["slots"]
                mapping_value = payload.get("filament_map")
                if isinstance(mapping_value, str):
                    selected, _ = resolve_mapping(
                        upload["plan"]["filaments"], mapping_value
                    )
                elif isinstance(mapping_value, list):
                    selected = resolve_mapping_values(
                        upload["plan"]["filaments"], mapping_value, slots
                    )
                else:
                    raise BadRequest(
                        "'filament_map' must be a list of tray ids or a comma-separated string."
                    )
                result = start_plan(
                    upload["plan"],
                    upload["remoteName"],
                    selected,
                    upload["options"],
                    snapshot,
                )
                delete_upload(upload_id)
                self.send_json(200, result)
            elif path in {"/stop", "/pause", "/resume"}:
                command = path.lstrip("/")
                started_at = time.time()
                sequence = MQTT_WORKER.publish_command(
                    "print", command, {"param": ""}
                )
                response = STATE.wait_for_command(
                    sequence,
                    command,
                    started_at,
                    COMMAND_TIMEOUT_SECONDS,
                )
                if response is not None:
                    result = str(response.get("result") or "").strip().lower()
                    if result and result not in {"success", "ok"}:
                        raise PrinterRejected(
                            f"printer rejected {command}: {response.get('reason') or result}"
                        )
                self.send_json(
                    200,
                    {
                        "ok": True,
                        "command": command,
                        "printerResponse": response,
                    },
                )
            else:
                self.send_json(404, {"ok": False, "error": "not found"})
        except Exception as error:
            self.handle_error(error)


def main():
    if not PRINTER_SERIAL:
        print(
            "bambu-printer-agent: set PRINTER_SERIAL to the printer serial number",
            flush=True,
        )
        raise SystemExit(1)
    global MQTT_WORKER
    MQTT_WORKER = MqttWorker(PRINTER_IP, PRINTER_SERIAL, ACCESS_CODE, STATE)
    MQTT_WORKER.start()

    server = ThreadingHTTPServer((BIND_ADDRESS, PORT), Handler)
    auth = "token required" if TOKEN else "no token configured"
    print(
        f"bambu-printer-agent: listening on {BIND_ADDRESS}:{PORT} ({auth}), "
        f"printer {PRINTER_IP} ({PRINTER_SERIAL})",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        MQTT_WORKER.stop()
        server.server_close()


if __name__ == "__main__":
    main()
