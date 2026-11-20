"""
Local Engine Server Module
Lets the web page drive this PC's transcription pipeline: a small HTTP server bound
to 127.0.0.1 that takes uploaded songs (or YouTube links), runs one pipeline job at a
time on the local GPU, reports its stages and log, and serves the finished files.
See docs/engine_api.md for the endpoints.

Only the web page may use it:
  - it listens on 127.0.0.1 only and refuses any other Host header (DNS rebinding);
  - browsers may call it only from the allowed origins (CORS), others get 403;
  - everything but /api/status and /api/pair needs the token a browser gets by
    entering the pairing code shown in the engine window, once per browser.
"""

import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlsplit

from src import __version__
from src.cancellation import PipelineCancelled
from src.gui_state import find_songs
from src.pipeline_options import ALL_PARTS, DEFAULT_PARTS, VRAM_PROFILES, is_url

APP_ID = "ai-band-transcriber"
DEFAULT_PORT = 51823
SITE_URL = "https://manofkimchi08.github.io/ai-guitar-transcriber/"
DEFAULT_ORIGINS = ("https://manofkimchi08.github.io",)
# A page served from this PC (any port) may also use the engine, for developing the page
LOCAL_ORIGIN = re.compile(r"^http://(localhost|127\.0\.0\.1)(:\d+)?$")

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".opus", ".aac", ".webm", ".mp4"}
MAX_UPLOAD_BYTES = 1024 ** 3
MAX_JSON_BYTES = 64 * 1024
SERVED_FOLDERS = ("scores", "midi", "stems")
STYLES = ("tab", "standard", "both")
LOG_LIMIT = 5000
PAIR_ATTEMPTS = 5
UPLOAD_MAX_AGE = 3 * 24 * 3600


class EngineError(Exception):
    """A request the engine refuses: carries the HTTP status and a message for the page."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def detect_gpu():
    """{'name', 'vram_gb'} of the first CUDA GPU, or None (no GPU, CPU-only PyTorch, no PyTorch)."""
    try:
        import torch
        if torch.cuda.is_available():
            props = torch.cuda.get_device_properties(0)
            return {"name": props.name, "vram_gb": round(props.total_memory / 1024 ** 3, 1)}
    except Exception:
        pass
    return None


def default_runner(input_audio, output_dir, **options):
    """The real pipeline, imported on first use so the server starts before PyTorch loads."""
    from src.pipeline import run_pipeline
    return run_pipeline(input_audio, output_dir, **options)


def safe_file_name(name: str) -> str:
    """An uploaded file's name made safe to save on Windows; its stem becomes the song title."""
    name = os.path.basename(name.replace("\\", "/"))
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip().rstrip(" .")
    if name.startswith("."):  # ".mp3": no title left, only the extension
        name = "song" + name
    stem, ext = os.path.splitext(name)
    return (stem[:100] or "song") + ext.lower()


class Job:
    """One pipeline run: its options, the stages and log it reported, and how it ended."""

    def __init__(self, options: dict, display_input: str):
        self.id = uuid.uuid4().hex[:12]
        self.options = options
        self.input = display_input
        self.state = "running"  # running -> done | error | cancelled
        self.stages = []        # stage keys in the order they started; the last one is current
        self.status = "준비 중..."
        self.fraction = 0.0
        self.log = []
        self.log_base = 0       # index of self.log[0] once old lines are dropped
        self.result = None
        self.error = None
        self.started = time.time()
        self.finished = None
        self.cancel = threading.Event()
        self.lock = threading.Lock()

    def add_log(self, line: str):
        with self.lock:
            self.log.append(str(line))
            if len(self.log) > LOG_LIMIT:
                drop = len(self.log) - LOG_LIMIT
                del self.log[:drop]
                self.log_base += drop

    def snapshot(self, log_from: int = 0) -> dict:
        with self.lock:
            start = max(log_from - self.log_base, 0)
            return {
                "id": self.id, "state": self.state, "input": self.input, "options": self.options,
                "stages": list(self.stages), "stage": self.stages[-1] if self.stages else None,
                "status": self.status, "fraction": self.fraction,
                "log": self.log[start:], "log_next": self.log_base + len(self.log),
                "result": self.result, "error": self.error,
                "elapsed": round((self.finished or time.time()) - self.started, 1),
            }


class Engine:
    """What the HTTP handler works on: pairing, uploads, the job and the finished songs."""

    def __init__(self, output_root: str, data_dir: str, origins=DEFAULT_ORIGINS, runner=None,
                 gpu_detector=detect_gpu):
        self.output_root = os.path.abspath(output_root)
        self.data_dir = os.path.abspath(data_dir)
        self.upload_dir = os.path.join(self.data_dir, "uploads")
        os.makedirs(self.upload_dir, exist_ok=True)
        self.origins = set(origins)
        self.runner = runner or default_runner
        self.lock = threading.Lock()
        self.jobs = {}
        self.current = None
        self.token = self._load_token()
        self.pair_code = self._new_code()
        self.pair_failures = 0
        self.on_pair_code = lambda code: None  # the window showing the code is told of a new one
        self.gpu = None
        self.gpu_checked = False
        self._remove_old_uploads()
        threading.Thread(target=self._check_gpu, args=(gpu_detector,), daemon=True).start()

    # --- pairing ---------------------------------------------------------------------
    def _settings_path(self):
        return os.path.join(self.data_dir, "engine.json")

    def _load_token(self) -> str:
        try:
            with open(self._settings_path(), encoding="utf-8") as f:
                token = json.load(f).get("token")
            if isinstance(token, str) and len(token) >= 32:
                return token
        except (OSError, ValueError, AttributeError):
            pass
        return self.reset_token()

    def reset_token(self) -> str:
        """A new token: every paired browser has to pair again."""
        self.token = secrets.token_urlsafe(32)
        with open(self._settings_path(), "w", encoding="utf-8") as f:
            json.dump({"token": self.token}, f)
        return self.token

    @staticmethod
    def _new_code() -> str:
        return f"{secrets.randbelow(10 ** 6):06d}"

    def _rotate_code(self):
        self.pair_code = self._new_code()
        self.pair_failures = 0
        self.on_pair_code(self.pair_code)

    def pair(self, code) -> str:
        """The token for the right pairing code. The code works once, and a new one is shown
        after it is used or after PAIR_ATTEMPTS wrong guesses."""
        with self.lock:
            if isinstance(code, str) and hmac.compare_digest(code.strip(), self.pair_code):
                self._rotate_code()
                return self.token
            self.pair_failures += 1
            if self.pair_failures >= PAIR_ATTEMPTS:
                self._rotate_code()
        raise EngineError(403, "연결 코드가 맞지 않습니다. 엔진 창에 보이는 코드를 입력하세요.")

    def authorized(self, header) -> bool:
        if not header or not header.startswith("Bearer "):
            return False
        return hmac.compare_digest(header[7:].strip().encode(), self.token.encode())

    def origin_allowed(self, origin: str) -> bool:
        return origin in self.origins or bool(LOCAL_ORIGIN.match(origin))

    # --- status ----------------------------------------------------------------------
    def _check_gpu(self, detector):
        try:
            self.gpu = detector()
        finally:
            self.gpu_checked = True

    def status(self, paired: bool) -> dict:
        current = self.current
        return {
            "app": APP_ID, "version": __version__, "paired": paired,
            "gpu": self.gpu, "gpu_checked": self.gpu_checked,
            "busy": bool(current and current.state == "running"),
            "job_id": current.id if current else None,
            "parts": ALL_PARTS, "default_parts": DEFAULT_PARTS,
            "vram_profiles": {key: p["label"] for key, p in VRAM_PROFILES.items()},
            "styles": list(STYLES),
        }

    # --- uploads ---------------------------------------------------------------------
    def _remove_old_uploads(self):
        now = time.time()
        for name in os.listdir(self.upload_dir):
            path = os.path.join(self.upload_dir, name)
            try:
                if now - os.path.getmtime(path) > UPLOAD_MAX_AGE:
                    shutil.rmtree(path, ignore_errors=True)
            except OSError:
                pass

    def save_upload(self, name: str, stream, length: int) -> dict:
        name = safe_file_name(name or "")
        if os.path.splitext(name)[1] not in AUDIO_EXTENSIONS:
            raise EngineError(400, f"지원하지 않는 파일 형식입니다: {name}")
        if length <= 0:
            raise EngineError(400, "빈 파일입니다.")
        if length > MAX_UPLOAD_BYTES:
            raise EngineError(413, "파일이 너무 큽니다 (최대 1GB).")
        upload_id = uuid.uuid4().hex[:12]
        folder = os.path.join(self.upload_dir, upload_id)
        os.makedirs(folder)
        path = os.path.join(folder, name)
        remaining = length
        try:
            with open(path, "wb") as f:
                while remaining:
                    chunk = stream.read(min(remaining, 1024 * 1024))
                    if not chunk:
                        raise EngineError(400, "파일 전송이 중간에 끊겼습니다.")
                    f.write(chunk)
                    remaining -= len(chunk)
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        return {"upload_id": upload_id, "name": name, "size": length}

    def _upload_path(self, upload_id) -> str:
        if not isinstance(upload_id, str) or not re.fullmatch(r"[0-9a-f]{12}", upload_id):
            raise EngineError(400, "잘못된 업로드 번호입니다.")
        folder = os.path.join(self.upload_dir, upload_id)
        files = os.listdir(folder) if os.path.isdir(folder) else []
        if not files:
            raise EngineError(404, "업로드한 파일을 찾을 수 없습니다. 다시 올려 주세요.")
        return os.path.join(folder, files[0])

    # --- jobs ------------------------------------------------------------------------
    def _options(self, body: dict) -> dict:
        parts = body.get("parts", DEFAULT_PARTS)
        if not isinstance(parts, list) or not parts or any(p not in ALL_PARTS for p in parts):
            raise EngineError(400, f"파트는 {', '.join(ALL_PARTS)} 중에서 골라 주세요.")
        style = body.get("style", "tab")
        if style not in STYLES:
            raise EngineError(400, "PDF 스타일은 tab, standard, both 중 하나입니다.")
        vram = body.get("vram")
        if vram is not None and vram not in VRAM_PROFILES:
            raise EngineError(400, "VRAM 설정은 8gb, 4gb 또는 자동(null)입니다.")
        language = body.get("lyrics_language")
        if language is not None and not (isinstance(language, str) and re.fullmatch(r"[a-z]{2,3}", language)):
            raise EngineError(400, "가사 언어는 ko, en, ja 같은 언어 코드입니다.")
        return {"parts": [p for p in ALL_PARTS if p in parts], "style": style, "vram": vram,
                "lyrics": bool(body.get("lyrics", True)), "lyrics_language": language}

    def start_job(self, body: dict) -> Job:
        if not isinstance(body, dict):
            raise EngineError(400, "요청 형식이 잘못되었습니다.")
        options = self._options(body)
        url = body.get("url")
        if url is not None:
            if not (isinstance(url, str) and is_url(url.strip())):
                raise EngineError(400, "http:// 또는 https://로 시작하는 링크를 넣어 주세요.")
            input_audio = display = url.strip()
        else:
            input_audio = self._upload_path(body.get("upload_id"))
            display = os.path.basename(input_audio)
        with self.lock:
            if self.current and self.current.state == "running":
                raise EngineError(409, "이미 변환 중인 곡이 있습니다. 끝나거나 취소한 뒤에 시작하세요.")
            job = Job(options, display)
            self.jobs[job.id] = job
            self.current = job
        threading.Thread(target=self._run, args=(job, input_audio), daemon=True).start()
        return job

    def _run(self, job: Job, input_audio: str):
        def progress(text, fraction):
            with job.lock:
                job.status, job.fraction = str(text), float(fraction)

        def stage(key):
            with job.lock:
                job.stages.append(key)

        try:
            result = self.runner(input_audio, self.output_root, log=job.add_log, progress=progress,
                                 stage=stage, cancel=job.cancel, **job.options)
            state, outcome = "done", self._result_summary(result or {})
        except PipelineCancelled:
            state, outcome = "cancelled", None
            job.add_log("[!] 사용자가 변환을 취소했습니다.")
        except Exception as e:
            state, outcome = "error", None
            with job.lock:
                job.error = str(e) or type(e).__name__
            job.add_log(f"[!] 오류: {job.error}")
            for line in traceback.format_exc().rstrip().splitlines()[-8:]:
                job.add_log(line)
        with job.lock:
            job.state, job.result, job.finished = state, outcome, time.time()
            if state == "done":
                job.fraction, job.status = 1.0, "완료"
            elif state == "cancelled":
                job.status = "취소됨"
            else:
                job.status = "오류"

    def _result_summary(self, result: dict) -> dict:
        """The run's files as paths the page can request: song folder name + path inside it."""
        song_dir = result.get("song_dir") or ""
        summary = {"song": os.path.basename(os.path.normpath(song_dir)) if song_dir else None,
                   "song_title": result.get("song_title"), "elapsed": result.get("elapsed"),
                   "bpm": result.get("bpm")}
        for key, out in (("score_xml", "score"), ("pdf_path", "pdf"), ("lyrics_txt", "lyrics")):
            path = result.get(key)
            summary[out] = (os.path.relpath(path, song_dir).replace(os.sep, "/")
                            if path and song_dir and os.path.exists(path) else None)
        return summary

    def job(self, job_id: str) -> Job:
        job = self.jobs.get(job_id)
        if not job:
            raise EngineError(404, "작업을 찾을 수 없습니다.")
        return job

    def cancel_job(self, job_id: str) -> Job:
        job = self.job(job_id)
        job.cancel.set()
        return job

    def cancel_all(self):
        for job in list(self.jobs.values()):
            job.cancel.set()

    # --- songs -----------------------------------------------------------------------
    def _song_dir(self, name: str) -> str:
        if not name or name in (".", "..") or "/" in name or "\\" in name:
            raise EngineError(404, "곡을 찾을 수 없습니다.")
        song_dir = os.path.join(self.output_root, name)
        if not os.path.isdir(song_dir):
            raise EngineError(404, "곡을 찾을 수 없습니다.")
        return song_dir

    def _files(self, song_dir: str) -> list:
        files = []
        for folder in SERVED_FOLDERS:
            base = os.path.join(song_dir, folder)
            if not os.path.isdir(base):
                continue
            for root, _, names in os.walk(base):
                for n in sorted(names):
                    path = os.path.join(root, n)
                    files.append({"path": os.path.relpath(path, song_dir).replace(os.sep, "/"),
                                  "size": os.path.getsize(path)})
        return files

    def songs(self) -> list:
        listed = []
        for song in find_songs(self.output_root):
            if song["dir"] == self.output_root:  # output/scores/ of old versions: no song folder
                continue
            rel = lambda p: os.path.relpath(p, song["dir"]).replace(os.sep, "/") if p else None
            listed.append({"name": song["name"], "tags": song["tags"], "mtime": song["mtime"],
                           "score": rel(song["score"]), "pdf": rel(song["pdf"]),
                           "files": self._files(song["dir"])})
        return listed

    def song_file(self, name: str, rel_path: str) -> str:
        """The absolute path of a file inside one of the song's SERVED_FOLDERS, else EngineError."""
        song_dir = os.path.realpath(self._song_dir(name))
        path = os.path.realpath(os.path.join(song_dir, rel_path))
        inside = any(path.startswith(os.path.join(song_dir, folder) + os.sep) for folder in SERVED_FOLDERS)
        if not inside or not os.path.isfile(path):
            raise EngineError(404, "파일을 찾을 수 없습니다.")
        return path


class RequestBody:
    """A request's body read through Content-Length, so a refused request's unread rest can
    be skipped and the kept-alive connection stays in step."""

    DISCARD_LIMIT = 16 * 1024 * 1024

    def __init__(self, rfile, length: int):
        self.rfile, self.left = rfile, length

    def read(self, n: int = -1) -> bytes:
        n = self.left if n < 0 else min(n, self.left)
        if n <= 0:
            return b""
        data = self.rfile.read(n)
        self.left -= len(data)
        if not data:
            self.left = 0
        return data

    def discard(self) -> bool:
        """Skips what is left; False when that is too much (the connection is closed instead)."""
        if self.left > self.DISCARD_LIMIT:
            return False
        while self.left > 0:
            if not self.read(1024 * 1024):
                return False
        return True


class EngineHandler(BaseHTTPRequestHandler):
    server_version = "AIBandTranscriberEngine"
    protocol_version = "HTTP/1.1"

    @property
    def engine(self) -> Engine:
        return self.server.engine

    def log_message(self, format, *args):  # keep the engine window for the pairing code and jobs
        pass

    # --- plumbing --------------------------------------------------------------------
    def _origin(self):
        return self.headers.get("Origin")

    def _cors_headers(self):
        origin = self._origin()
        if origin and self.engine.origin_allowed(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Expose-Headers", "Content-Disposition")

    def _send(self, status: int, body: bytes, content_type: str, extra=None):
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data):
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, status: int, message: str):
        self._json(status, {"error": message})

    def _read_json(self):
        if self.body.left > MAX_JSON_BYTES:
            raise EngineError(413, "요청이 너무 큽니다.")
        raw = self.body.read()
        try:
            return json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            raise EngineError(400, "요청 형식이 잘못되었습니다 (JSON).")

    def _gate(self) -> bool:
        """Host and Origin checks every request passes before routing; False = refused."""
        port = self.server.server_address[1]
        if self.headers.get("Host") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            self._refuse_rest()
            self._error(403, "이 엔진은 127.0.0.1 주소로만 접속할 수 있습니다.")
            return False
        origin = self._origin()
        if origin and not self.engine.origin_allowed(origin):
            self._refuse_rest()
            self._error(403, f"허용되지 않은 사이트입니다: {origin}")
            return False
        return True

    def _refuse_rest(self):
        """Skips a refused request's unread body, or closes the connection after the reply."""
        if not self.body.discard():
            self.close_connection = True

    def _handle(self, method: str):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        self.body = RequestBody(self.rfile, max(length, 0))
        try:
            if length < 0:
                self.close_connection = True
                raise EngineError(400, "Content-Length가 잘못되었습니다.")
            if not self._gate():
                return
            url = urlsplit(self.path)
            parts = [unquote(p) for p in url.path.split("/") if p]
            query = parse_qs(url.query)
            if parts[:1] != ["api"]:
                raise EngineError(404, "없는 주소입니다.")
            self._route(method, parts[1:], query)
        except EngineError as e:
            self._refuse_rest()
            self._error(e.status, str(e))
        except (ConnectionError, TimeoutError):
            self.close_connection = True
        except Exception as e:  # a bug here must not stop the engine
            self._refuse_rest()
            self._error(500, f"엔진 내부 오류: {e}")

    def _route(self, method, path, query):
        engine = self.engine
        paired = engine.authorized(self.headers.get("Authorization"))
        if path == ["status"] and method in ("GET", "HEAD"):
            return self._json(200, engine.status(paired))
        if path == ["pair"] and method == "POST":
            return self._json(200, {"token": engine.pair(self._read_json().get("code"))})
        if not paired:
            raise EngineError(401, "이 브라우저는 아직 엔진과 연결되지 않았습니다. 연결 코드를 입력하세요.")

        if path == ["uploads"] and method == "POST":
            name = unquote(self.headers.get("X-File-Name") or "")
            return self._json(201, engine.save_upload(name, self.body, self.body.left))
        if path == ["jobs"] and method == "POST":
            return self._json(202, engine.start_job(self._read_json()).snapshot())
        if path == ["jobs", "current"] and method == "GET":
            job = engine.current
            return self._json(200, job.snapshot(self._log_from(query)) if job else None)
        if len(path) == 2 and path[0] == "jobs" and method == "GET":
            return self._json(200, engine.job(path[1]).snapshot(self._log_from(query)))
        if len(path) == 3 and path[0] == "jobs" and path[2] == "cancel" and method == "POST":
            return self._json(200, engine.cancel_job(path[1]).snapshot())
        if path == ["songs"] and method == "GET":
            return self._json(200, engine.songs())
        if len(path) >= 4 and path[0] == "songs" and path[2] == "files" and method in ("GET", "HEAD"):
            return self._file(engine.song_file(path[1], "/".join(path[3:])))
        raise EngineError(404, "없는 주소입니다.")

    @staticmethod
    def _log_from(query) -> int:
        try:
            return max(int(query.get("log_from", ["0"])[0]), 0)
        except ValueError:
            return 0

    def _file(self, path: str):
        content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if path.endswith((".musicxml", ".xml")):
            content_type = "application/vnd.recordare.musicxml+xml"
        with open(path, "rb") as f:
            body = f.read()
        name = os.path.basename(path)
        disposition = f"inline; filename*=UTF-8''{quote(name, safe='')}"
        self._send(200, body, content_type, {"Content-Disposition": disposition})

    # --- methods ---------------------------------------------------------------------
    def do_OPTIONS(self):
        """CORS preflight: allowed origins only, with Chrome's private network opt-in."""
        self.body = RequestBody(self.rfile, 0)
        if not self._gate():
            return
        self.send_response(204)
        self._cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-File-Name")
        self.send_header("Access-Control-Max-Age", "600")
        if self.headers.get("Access-Control-Request-Private-Network") == "true":
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self._handle("GET")

    def do_HEAD(self):
        self._handle("HEAD")

    def do_POST(self):
        self._handle("POST")


class EngineServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, engine: Engine, port: int = DEFAULT_PORT):
        super().__init__(("127.0.0.1", port), EngineHandler)
        self.engine = engine
