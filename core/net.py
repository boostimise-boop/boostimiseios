"""HTTP for the online features (accounts, the enhancement server) and running it off the UI thread.

  request()     one HTTP request; connections are kept alive per thread (the enhancer polls its
                jobs several times a second) and carry a real User-Agent: the enhancement server is
                behind Cloudflare, which refuses Python's default one (error 1010)
  download()    streams a response into a file with progress, named like the server says
  Multipart     a multipart/form-data body that reports upload progress and can be cancelled
  run_bg()      runs a function on a worker thread and hands its result back to the UI thread
  ui()          runs a function on the UI thread (from any thread)
"""
from __future__ import annotations

import http.client
import json
import logging
import os
import re
import ssl
import threading
import time
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal

from . import theme

log = logging.getLogger("allinone.net")

USER_AGENT = (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) {theme.APP_NAME}-Desktop/{theme.APP_VERSION}")
CHUNK = 64 * 1024


class HttpError(Exception):
    """A request that failed: status 0 = no answer (offline, timeout), else the HTTP status.
    ``code`` / ``message`` come from the JSON error body when there is one."""

    def __init__(self, status: int, code: str, message: str, body: Any = None):
        super().__init__(message)
        self.status, self.code, self.message, self.body = status, code, message, body


class Cancelled(Exception):
    """Raised inside an upload or download that was cancelled."""


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    data: bytes

    def json(self) -> Any:
        return json.loads(self.data or b"null")


def _error_from(status: int, data: bytes) -> HttpError:
    """Reads the error out of the body: {"error": {"code", "message"}} (enhancement server),
    {"code", "message"} (Supabase REST), {"error", "error_description"} / {"msg"} (Supabase Auth)."""
    try:
        body = json.loads(data or b"null")
    except ValueError:
        body = None
    code, message = "http_error", f"Request failed ({status})"
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            code, message = str(err.get("code") or code), str(err.get("message") or message)
        else:
            code = str(body.get("error_code") or body.get("code") or err or code)
            message = str(body.get("message") or body.get("msg") or body.get("error_description") or err or message)
    return HttpError(status, code, message, body)


# ---- connections -----------------------------------------------------------------------------
_local = threading.local()
_ssl = ssl.create_default_context()
_RETRYABLE = (http.client.RemoteDisconnected, ConnectionResetError, ConnectionAbortedError, BrokenPipeError,
              http.client.BadStatusLine, ssl.SSLEOFError)


def _connection(parts: urllib.parse.SplitResult, timeout: float, fresh: bool) -> http.client.HTTPConnection:
    pool: dict = getattr(_local, "pool", None) or {}
    _local.pool = pool
    https = parts.scheme == "https"
    key = (parts.scheme, parts.hostname, parts.port)
    conn = pool.get(key)
    if conn is None or fresh:
        if conn is not None:
            conn.close()
        port = parts.port or (443 if https else 80)
        conn = (http.client.HTTPSConnection(parts.hostname, port, timeout=timeout, context=_ssl) if https
                else http.client.HTTPConnection(parts.hostname, port, timeout=timeout))
        pool[key] = conn
    conn.timeout = timeout
    if conn.sock is not None:
        conn.sock.settimeout(timeout)
    return conn


def _open(method: str, url: str, headers: dict[str, str] | None, body, timeout: float):
    """Sends the request and returns (connection, response) with the headers read. A connection
    kept from an earlier request may have been closed by the server meanwhile: then the request is
    sent once more on a new one (only when the body can be sent again)."""
    parts = urllib.parse.urlsplit(url)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json, */*", **(headers or {})}
    replayable = body is None or isinstance(body, (bytes, bytearray))
    for attempt in (0, 1):
        conn = _connection(parts, timeout, fresh=attempt > 0 or not replayable)
        try:
            conn.request(method, path, body=body, headers=hdrs)
            return conn, conn.getresponse()
        except Cancelled:
            conn.close()
            raise
        except _RETRYABLE as e:
            conn.close()
            if attempt or not replayable:
                raise HttpError(0, "network", f"Connection lost ({type(e).__name__})") from e
        except (OSError, http.client.HTTPException) as e:
            conn.close()
            raise HttpError(0, "network", str(e) or type(e).__name__) from e
    raise AssertionError("unreachable")


def request(method: str, url: str, *, headers: dict[str, str] | None = None, body=None,
            json_body: Any = None, timeout: float = 30.0) -> Response:
    """One request; raises HttpError for a non-2xx answer or when there is no answer at all."""
    if json_body is not None:
        body = json.dumps(json_body).encode()
        headers = {"Content-Type": "application/json", **(headers or {})}
    conn, resp = _open(method, url, headers, body, timeout)
    try:
        data = resp.read()
    except (OSError, http.client.HTTPException) as e:
        conn.close()
        raise HttpError(0, "network", str(e) or type(e).__name__) from e
    if resp.will_close:
        conn.close()
    out = Response(resp.status, {k.lower(): v for k, v in resp.getheaders()}, data)
    if not 200 <= resp.status < 300:
        raise _error_from(resp.status, data)
    return out


def get_json(url: str, **kw) -> Any:
    return request("GET", url, **kw).json()


# ---- downloads -------------------------------------------------------------------------------
def filename_from(headers: dict[str, str], fallback: str) -> str:
    cd = headers.get("content-disposition", "")
    m = re.search(r"filename\*\s*=\s*[^']*''([^;]+)", cd, re.I)
    name = urllib.parse.unquote(m.group(1)) if m else None
    if not name:
        m = re.search(r'filename\s*=\s*"?([^";]+)"?', cd, re.I)
        name = m.group(1) if m else fallback
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", Path(name).name).strip(" .")
    return name or fallback


def free_path(folder: Path, name: str) -> Path:
    """folder/name, or folder/name (2).ext ... when that file exists already."""
    path = folder / name
    stem, ext, n = path.stem, path.suffix, 2
    while path.exists():
        path = folder / f"{stem} ({n}){ext}"
        n += 1
    return path


def download(url: str, folder: Path, fallback_name: str, *, method: str = "GET", body: bytes | None = None,
             headers: dict[str, str] | None = None, on_progress: Callable[[int, int], None] | None = None,
             cancel: threading.Event | None = None, timeout: float = 120.0) -> Path:
    """Saves the response in ``folder`` under the name the server gives (Content-Disposition),
    never overwriting a file. Returns the saved file."""
    conn, resp = _open(method, url, headers, body, timeout)
    try:
        if not 200 <= resp.status < 300:
            raise _error_from(resp.status, resp.read())
        hdrs = {k.lower(): v for k, v in resp.getheaders()}
        total = int(hdrs.get("content-length") or 0)
        folder.mkdir(parents=True, exist_ok=True)
        target = free_path(folder, filename_from(hdrs, fallback_name))
        part = target.with_name(target.name + ".part")
        done = 0
        try:
            with open(part, "wb") as f:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise Cancelled()
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(done, total)
            if target.exists():  # appeared while downloading
                target = free_path(folder, target.name)
            os.replace(part, target)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        return target
    except (OSError, http.client.HTTPException) as e:
        raise HttpError(0, "network", str(e) or type(e).__name__) from e
    finally:
        conn.close()  # partly read on error / cancel: never reuse


# ---- uploads ---------------------------------------------------------------------------------
@dataclass
class Multipart:
    """multipart/form-data: text fields plus one file, read in chunks (a 50 MB photo is never
    copied in memory) with progress and cancellation."""

    fields: Iterable[tuple[str, str]]
    file_field: str | None = None
    file_name: str = ""
    file_path: Path | None = None
    file_type: str = "application/octet-stream"
    on_progress: Callable[[float], None] | None = None
    cancel: threading.Event = field(default_factory=threading.Event)

    def __post_init__(self):
        self.boundary = f"----boostimise{uuid.uuid4().hex}"
        head = b"".join(
            f'--{self.boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
            for k, v in self.fields)
        if self.file_field:
            safe = self.file_name.replace('"', "_").replace("\r", "").replace("\n", "")
            head += (f'--{self.boundary}\r\nContent-Disposition: form-data; name="{self.file_field}"; '
                     f'filename="{safe}"\r\nContent-Type: {self.file_type}\r\n\r\n').encode("utf-8")
            self._tail = f"\r\n--{self.boundary}--\r\n".encode()
            self._file_size = self.file_path.stat().st_size
        else:
            self._tail = f"--{self.boundary}--\r\n".encode()
            self._file_size = 0
        self._head = head
        self.length = len(head) + self._file_size + len(self._tail)
        self._sent = 0
        self._file = None
        self._stage = 0
        self._last_report = 0.0

    @property
    def content_type(self) -> str:
        return f"multipart/form-data; boundary={self.boundary}"

    def _report(self):
        if self.on_progress is None:
            return
        now = time.monotonic()
        if now - self._last_report > 0.08 or self._sent >= self.length:
            self._last_report = now
            self.on_progress(self._sent / max(1, self.length))

    def read(self, size: int = -1) -> bytes:
        """http.client reads the body through this, a block at a time."""
        if self.cancel.is_set():
            self.close()
            raise Cancelled()
        size = CHUNK if size is None or size < 0 else max(size, CHUNK)
        out = b""
        if self._stage == 0:
            out, self._stage = self._head, 1
        elif self._stage == 1:
            if self._file is None and self.file_path is not None:
                self._file = open(self.file_path, "rb")
            out = self._file.read(size) if self._file else b""
            if not out:
                self.close()
                out, self._stage = self._tail, 3
        elif self._stage == 3:
            out, self._stage = b"", 4
        self._sent += len(out)
        self._report()
        return out

    def close(self):
        if self._file is not None:
            self._file.close()
            self._file = None


def post_multipart(url: str, body: Multipart, *, timeout: float = 120.0,
                   headers: dict[str, str] | None = None) -> Response:
    hdrs = {"Content-Type": body.content_type, "Content-Length": str(body.length), **(headers or {})}
    try:
        return request("POST", url, headers=hdrs, body=body, timeout=timeout)
    finally:
        body.close()


def form_body(fields: Iterable[tuple[str, str]]) -> bytes:
    return urllib.parse.urlencode(list(fields)).encode()


# ---- threads ---------------------------------------------------------------------------------
class _Invoker(QObject):
    call = Signal(object)

    def __init__(self):
        super().__init__()
        self.call.connect(self._run)

    @staticmethod
    def _run(fn):
        try:
            fn()
        except RuntimeError as e:  # the widget it was for has been closed meanwhile
            if "already deleted" in str(e) or "has been deleted" in str(e):
                log.debug("dropped a callback for a deleted object: %s", e)
            else:
                log.exception("Error in a callback")
        except Exception:
            log.exception("Error in a callback")


_invoker: _Invoker | None = None
_invoker_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=10, thread_name_prefix="net")


def _get_invoker() -> _Invoker:
    global _invoker
    with _invoker_lock:
        if _invoker is None:
            inv = _Invoker()
            app = QCoreApplication.instance()
            if app is not None and QThread.currentThread() is not app.thread():
                inv.moveToThread(app.thread())
            _invoker = inv
        return _invoker


def ui(fn: Callable[[], Any]):
    """Run ``fn`` on the UI thread (right away when already there)."""
    app = QCoreApplication.instance()
    if app is not None and QThread.currentThread() is app.thread():
        _Invoker._run(fn)
    else:
        _get_invoker().call.emit(fn)


def run_bg(fn: Callable[[], Any], done: Callable[[Any], Any] | None = None,
           failed: Callable[[BaseException], Any] | None = None):
    """Run ``fn`` on a worker thread; then ``done(result)`` or ``failed(error)`` on the UI thread."""
    _get_invoker()  # made on the UI thread, before any worker needs it

    def work():
        try:
            result = fn()
        except BaseException as e:  # noqa: BLE001 - handed to the caller
            if failed is not None:
                ui(lambda err=e: failed(err))
            elif not isinstance(e, (HttpError, Cancelled)):
                log.error("Background task failed", exc_info=e)
            return
        if done is not None:
            ui(lambda: done(result))

    return _pool.submit(work)
