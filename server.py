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


ROOM_CODE = _persistent_value(
	"room-code",
	lambda: "".join(secrets.choice(CODE_ALPHABET) for _ in range(6)),
	lambda value: len(value) == 6 and all(char in CODE_ALPHABET for char in value),
)
OWNER_KEY = _persistent_value("owner-key", lambda: secrets.token_urlsafe(32), lambda value: len(value) >= 40)
INGEST_PATH = f"{ROOM_CODE}_ingest"
STOP = threading.Event()
relay_lock = threading.Lock()
relay_process: subprocess.Popen | None = None


def _media_path_ready(path: str) -> bool:
	url = f"{MEDIA_API}/v3/paths/get/{quote(path, safe='')}"
	try:
		with urlopen(url, timeout=0.8) as response:
			payload = json.loads(response.read().decode("utf-8"))
		return bool(payload.get("ready"))
	except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError):
		return False


def _stop_relay() -> None:
	global relay_process
	with relay_lock:
		process = relay_process
		relay_process = None
	if process is None or process.poll() is not None:
		return
	process.terminate()
	try:
		process.wait(timeout=4)
	except subprocess.TimeoutExpired:
		process.kill()
		process.wait(timeout=2)


def _start_relay() -> None:
	global relay_process
	with relay_lock:
		if relay_process is not None and relay_process.poll() is None:
			return
		ffmpeg = shutil.which(os.environ.get("FFMPEG", "ffmpeg"))
		if ffmpeg is None:
			log.error("ffmpeg was not found in the container")
			return
		input_url = f"srt://mediamtx:8890?streamid=read:{INGEST_PATH}:relay:{OWNER_KEY}"
		output_url = f"rtsp://relay:{OWNER_KEY}@mediamtx:8554/{ROOM_CODE}"
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
		log.info("Starting RTSP/TCP relay for room %s", ROOM_CODE)
		relay_process = subprocess.Popen(command)


def _watch_stream() -> None:
	last_start = 0.0
	while not STOP.wait(0.8):
		if _media_path_ready(INGEST_PATH):
			with relay_lock:
				running = relay_process is not None and relay_process.poll() is None
			if not running and time.monotonic() - last_start > 1.0:
				_start_relay()
				last_start = time.monotonic()
		else:
			_stop_relay()
	_stop_relay()


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
		# The shared community room accepts public publishers on its ingest path.
		return path == INGEST_PATH or (owner and path == ROOM_CODE)
	if action == "read" and path == ROOM_CODE:
		return True
	return action == "read" and protocol == "srt" and path == INGEST_PATH and user == "relay" and owner


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
		if not parts or parts[0] != INGEST_PATH or (
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
			if requested_code and requested_code != ROOM_CODE:
				return self.send_error(404)
			rtsp_host = PUBLIC_RTSP_HOST or urlsplit(PUBLIC_BASE_URL).hostname or "localhost"
			return self._json(
				200,
				{
					"code": ROOM_CODE,
					"rtspHost": rtsp_host,
					"rtspUrl": f"rtspt://{rtsp_host}:8554/{ROOM_CODE}",
					"ready": _media_path_ready(ROOM_CODE),
					"canPublish": True,
				},
			)
		if path in {"/", f"/{ROOM_CODE}"}:
			self.path = "/index.html"
		elif not path.startswith("/assets/"):
			self.send_error(404)
			return
		return super().do_GET()

	def do_POST(self):
		path = urlsplit(self.path).path
		if path.startswith("/media/"):
			return self._proxy_media()
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
	rtsp_host = PUBLIC_RTSP_HOST or urlsplit(PUBLIC_BASE_URL).hostname or "localhost"
	log.info("Share page: %s", PUBLIC_BASE_URL)
	log.info("RTSP/TCP URL: rtspt://%s:8554/%s", rtsp_host, ROOM_CODE)
	try:
		server.serve_forever()
	finally:
		STOP.set()
		server.server_close()
		_stop_relay()


if __name__ == "__main__":
	main()
