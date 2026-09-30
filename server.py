from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import string
import subprocess
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
MEDIA_API = os.environ.get("MEDIA_API", "http://mediamtx:9997")
MEDIA_WEBRTC = os.environ.get("MEDIA_WEBRTC", "http://mediamtx:8889").rstrip("/")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8080").rstrip("/")
PUBLIC_RTSP_HOST = os.environ.get("PUBLIC_RTSP_HOST", "").strip()
HOST = os.environ.get("HOST", "0.0.0.0")
CODE_ALPHABET = string.ascii_letters + string.digits
SESSION_TTL = 6 * 60 * 60
SESSION_CODE_LENGTH = 6
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("screenshare")


def _persistent_value(filename: str, make_value, valid) -> str:
	DATA_DIR.mkdir(parents=True, exist_ok=True)
	path = DATA_DIR / filename
	try:
		value = path.read_text(encoding="utf-8").strip()
		if valid(value):
			return value
	except OSError:
		pass
	value = make_value()
	path.write_text(value + "\n", encoding="utf-8")
	return value


OWNER_KEY = _persistent_value("owner-key", lambda: secrets.token_urlsafe(32), lambda value: len(value) >= 40)
STOP = threading.Event()
sessions: dict[str, float] = {}
sessions_lock = threading.Lock()
relay_lock = threading.Lock()
relay_processes: dict[str, subprocess.Popen] = {}


def _valid_code(value: str) -> bool:
	return len(value) == SESSION_CODE_LENGTH and all(char in CODE_ALPHABET for char in value)


def _new_session() -> str:
	with sessions_lock:
		while True:
			code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(SESSION_CODE_LENGTH))
			if code not in sessions:
				sessions[code] = time.monotonic()
				return code


def _touch_session(code: str) -> bool:
	if not _valid_code(code):
		return False
	with sessions_lock:
		if code not in sessions:
			return False
		sessions[code] = time.monotonic()
		return True


def _session_codes() -> list[str]:
	with sessions_lock:
		return list(sessions)


def _media_path_ready(path: str) -> bool:
	url = f"{MEDIA_API}/v3/paths/get/{quote(path, safe='')}"
	try:
		with urlopen(url, timeout=0.8) as response:
			payload = json.loads(response.read().decode("utf-8"))
		return bool(payload.get("ready"))
	except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError):
		return False


def _stop_relay(code: str) -> None:
	with relay_lock:
		process = relay_processes.pop(code, None)
	if process is None or process.poll() is not None:
		return
	process.terminate()
	try:
		process.wait(timeout=4)
	except subprocess.TimeoutExpired:
		process.kill()
		process.wait(timeout=2)


def _start_relay(code: str) -> None:
	with relay_lock:
		process = relay_processes.get(code)
		if process is not None and process.poll() is None:
			return
		relay_processes.pop(code, None)
		ffmpeg = shutil.which(os.environ.get("FFMPEG", "ffmpeg"))
		if ffmpeg is None:
			log.error("ffmpeg was not found in the container")
			return
		ingest_path = f"{code}_ingest"
		input_url = f"srt://mediamtx:8890?streamid=read:{ingest_path}:relay:{OWNER_KEY}"
		output_url = f"rtsp://relay:{OWNER_KEY}@mediamtx:8554/{code}"
		command = [
			ffmpeg,
			"-nostdin",
			"-hide_banner",
			"-loglevel",
			"warning",
			"-fflags",
			"+discardcorrupt",
			"-analyzeduration",
			"5000000",
			"-probesize",
			"2000000",
			"-rtbufsize",
			"64M",
			"-thread_queue_size",
			"1024",
			"-i",
			input_url,
			"-map",
			"0:v:0",
			"-map",
			"0:a:0?",
			"-c:v",
			"copy",
			"-bsf:v",
			"dump_extra=freq=keyframe",
			"-c:a",
			"aac",
			"-b:a",
			"112k",
			"-profile:a",
			"aac_low",
			"-ar",
			"48000",
			"-ac",
			"2",
			"-af",
			"aresample=async=1:min_hard_comp=0.050:first_pts=0",
			"-rtsp_transport",
			"tcp",
			"-copytb",
			"1",
			"-f",
			"rtsp",
			output_url,
		]
		log.info("Starting RTSP/TCP relay for session %s", code)
		relay_processes[code] = subprocess.Popen(command)


def _expire_sessions(now: float) -> None:
	with sessions_lock:
		expired = [code for code, touched in sessions.items() if now - touched > SESSION_TTL]
	for code in expired:
		if _media_path_ready(f"{code}_ingest"):
			_touch_session(code)
			continue
		with sessions_lock:
			removed = now - sessions.get(code, now) > SESSION_TTL
			if removed:
				sessions.pop(code, None)
		if removed:
			_stop_relay(code)


def _watch_stream() -> None:
	last_cleanup = time.monotonic()
	while not STOP.wait(0.8):
		now = time.monotonic()
		if now - last_cleanup > 60:
			_expire_sessions(now)
			last_cleanup = now
		for code in _session_codes():
			if _media_path_ready(f"{code}_ingest"):
				_start_relay(code)
			else:
				_stop_relay(code)
	with relay_lock:
		codes = list(relay_processes)
	for code in codes:
		_stop_relay(code)


def _authorized(payload: dict) -> bool:
	action = str(payload.get("action", ""))
	path = str(payload.get("path", ""))
	protocol = str(payload.get("protocol", ""))
	user = str(payload.get("user", ""))
	password = str(payload.get("password", ""))
	owner = user in {"owner", "relay"} and secrets.compare_digest(password, OWNER_KEY)
	if action == "api":
		# MediaMTX's API is only reachable on the private Compose network.
		return True
	if action == "publish":
		if path.endswith("_ingest"):
			return _touch_session(path[:-7])
		return owner and _touch_session(path)
	if action == "read" and protocol == "srt" and user == "relay" and owner:
		return path.endswith("_ingest") and _touch_session(path[:-7])
	if action == "read":
		return _touch_session(path)
	return False


class Handler(SimpleHTTPRequestHandler):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, directory=str(DIST), **kwargs)

	def log_message(self, fmt, *args):
		path = urlsplit(self.path).path
		log.info("%s %s %s", self.client_address[0], self.command, path)

	def _json(self, status: int, payload: dict) -> None:
		body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
		self.send_response(status)
		self.send_header("Content-Type", "application/json; charset=utf-8")
		self.send_header("Cache-Control", "no-store")
		self.send_header("Content-Length", str(len(body)))
		self.end_headers()
		self.wfile.write(body)

	def _proxy_media(self) -> None:
		parsed = urlsplit(self.path)
		if not parsed.path.startswith("/media/"):
			return self.send_error(404)
		upstream_path = parsed.path[len("/media"):]
		parts = upstream_path.lstrip("/").split("/", 1)
		endpoint = parts[1] if len(parts) == 2 else ""
		if not parts or not parts[0].endswith("_ingest") or not _touch_session(parts[0][:-7]) or (
			endpoint not in {"publisher.js", "whip"} and not endpoint.startswith("whip/")
		):
			return self.send_error(404)
		url = MEDIA_WEBRTC + upstream_path
		if parsed.query:
			url += "?" + parsed.query
		try:
			length = int(self.headers.get("Content-Length", "0") or "0")
		except ValueError:
			return self.send_error(400, "Invalid content length")
		if length < 0:
			return self.send_error(400, "Invalid content length")
		if length > 2_000_000:
			return self.send_error(413, "Media request is too large")
		body = self.rfile.read(length) if length else None
		headers = {
			name: value for name, value in self.headers.items()
			if name.lower() in {"accept", "authorization", "content-type", "if-match", "if-none-match", "origin", "user-agent"}
		}
		request = Request(url, data=body, headers=headers, method=self.command)
		try:
			response = urlopen(request, timeout=20)
		except HTTPError as error:
			response = error
		except (URLError, OSError, TimeoutError):
			return self.send_error(502, "Media server is unavailable")
		payload = response.read()
		self.send_response(getattr(response, "status", getattr(response, "code", 200)))
		blocked = {"connection", "date", "keep-alive", "server", "te", "trailer", "transfer-encoding", "upgrade"}
		for name, value in response.headers.items():
			if name.lower() in blocked:
				continue
			if name.lower() == "location":
				if value.startswith(MEDIA_WEBRTC):
					value = value[len(MEDIA_WEBRTC):]
				if value.startswith("/"):
					value = "/media" + value
			self.send_header(name, value)
		if not response.headers.get("Content-Length"):
			self.send_header("Content-Length", str(len(payload)))
		self.end_headers()
		if self.command != "HEAD":
			self.wfile.write(payload)
		response.close()

	def do_GET(self):
		path = urlsplit(self.path).path
		if path.startswith("/media/"):
			return self._proxy_media()
		if path == "/healthz":
			return self._json(200, {"ok": True})
		if path == "/api/session":
			requested_code = parse_qs(urlsplit(self.path).query).get("code", [""])[0]
			if not _touch_session(requested_code):
				return self.send_error(404)
			rtsp_host = PUBLIC_RTSP_HOST or urlsplit(PUBLIC_BASE_URL).hostname or "localhost"
			return self._json(
				200,
				{
					"code": requested_code,
					"rtspUrl": f"rtspt://{rtsp_host}:8554/{requested_code}",
					"ready": _media_path_ready(requested_code),
				},
			)
		if path == "/":
			self.path = "/index.html"
		elif not path.startswith("/assets/"):
			self.send_error(404)
			return
		return super().do_GET()

	def do_POST(self):
		path = urlsplit(self.path).path
		if path.startswith("/media/"):
			return self._proxy_media()
		if path == "/api/session":
			code = _new_session()
			rtsp_host = PUBLIC_RTSP_HOST or urlsplit(PUBLIC_BASE_URL).hostname or "localhost"
			return self._json(201, {"code": code, "rtspUrl": f"rtspt://{rtsp_host}:8554/{code}", "ready": False})
		if path != "/api/auth":
			return self.send_error(404)
		try:
			length = min(int(self.headers.get("Content-Length", "0")), 1_000_000)
			payload = json.loads(self.rfile.read(length).decode("utf-8"))
			allowed = isinstance(payload, dict) and _authorized(payload)
		except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
			allowed = False
		self.send_response(200 if allowed else 401)
		self.send_header("Content-Length", "0")
		self.end_headers()

	def do_PATCH(self):
		return self._proxy_media()

	def do_DELETE(self):
		return self._proxy_media()

	def do_OPTIONS(self):
		return self._proxy_media()


def main() -> None:
	if not (DIST / "index.html").is_file():
		raise RuntimeError("Svelte build is missing. Run npm run build before starting the server.")
	threading.Thread(target=_watch_stream, name="stream-relay", daemon=True).start()
	server = ThreadingHTTPServer((HOST, int(os.environ.get("PORT", "8000"))), Handler)
	log.info("Share page: %s", PUBLIC_BASE_URL)
	try:
		server.serve_forever()
	finally:
		STOP.set()
		server.server_close()
		with relay_lock:
			codes = list(relay_processes)
		for code in codes:
			_stop_relay(code)


if __name__ == "__main__":
	main()
