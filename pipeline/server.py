"""Minimal local HTTP server using Python stdlib for Anti-Algorithm Personal Hub."""

from __future__ import annotations

import argparse
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import socket
import sys
import threading
from typing import Any
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from pipeline import (
    DAILY_STATE_PATH,
    DEFAULT_CONFIG_PATH,
    DEFAULT_DB_PATH,
    DEFAULT_DIGEST_PATH,
    NOTIFY_STATE_PATH,
    ROOT_DIR,
    get_notification_settings,
    load_config,
)
from pipeline.build_digest import export_terms_json
from pipeline.db import (
    backfill_daily_term_from_state,
    get_all_daily_terms,
    get_glossary_stats,
    set_term_mastered,
    utc_now_iso,
)
from pipeline.notifier import (
    ToastSender,
    check_and_notify,
    get_runtime_notify_status,
    send_test_toast,
    toggle_notify_state,
)
from pipeline.run import run_pipeline

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_DIST_DIR = ROOT_DIR / "web" / "dist"
SERVER_LOG_PATH = ROOT_DIR / "data" / "server.log"
SERVICE_NAME = "anti-algo-hub"


def _configure_runtime_logging() -> logging.Logger:
    SERVER_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None or sys.stderr is None:
        log_fh = SERVER_LOG_PATH.open("a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = log_fh
        if sys.stderr is None:
            sys.stderr = log_fh
    else:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    logger = logging.getLogger(SERVICE_NAME)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
        file_handler = logging.FileHandler(SERVER_LOG_PATH, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        if sys.__stderr__ is not None:
            stream_handler = logging.StreamHandler(sys.stderr)
            stream_handler.setFormatter(formatter)
            logger.addHandler(stream_handler)
    return logger


class HubRuntime:
    """Thread-safe refresh coordinator and runtime paths for the local hub."""

    def __init__(
        self,
        *,
        dist_dir: Path,
        digest_path: Path,
        config_path: Path,
        logger: logging.Logger,
        db_path: Path = DEFAULT_DB_PATH,
        notify_state_path: Path = NOTIFY_STATE_PATH,
        terms_path: Path | None = None,
        daily_state_path: Path | None = None,
        hub_url: str = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}",
        notify_sender: ToastSender | None = None,
    ) -> None:
        self.dist_dir = dist_dir
        self.digest_path = digest_path
        self.terms_path = (
            terms_path if terms_path is not None else (digest_path.parent / "terms.json")
        )
        self.config_path = config_path
        self.logger = logger
        self.db_path = db_path
        self.notify_state_path = notify_state_path
        self.daily_state_path = (
            daily_state_path
            if daily_state_path is not None
            else (DAILY_STATE_PATH if db_path == DEFAULT_DB_PATH else None)
        )
        self.hub_url = hub_url
        self.notify_sender = notify_sender
        self._lock = threading.Lock()
        self._notify_lock = threading.Lock()
        self._terms_lock = threading.Lock()
        self._state: dict[str, Any] = {
            "service": SERVICE_NAME,
            "running": False,
            "last_started_at": None,
            "last_finished_at": None,
            "last_ok": None,
            "last_summary": None,
            "last_error": None,
        }

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def _run_refresh_worker(self) -> None:
        exit_code = 1
        err_text: str | None = None
        summary_text: str | None = None

        try:
            exit_code = run_pipeline(
                skip_llm=False,
                mock=False,
                skip_fetch=False,
                limit=None,
                batch_size=None,
                config_path=self.config_path,
                output_path=self.digest_path,
            )
            if exit_code == 0:
                summary_text = self._build_digest_summary()
            elif exit_code == 2:
                err_text = "未配置有效 LLM_API_KEY / LLM_MODEL，请检查项目根目录 .env 文件。"
            else:
                err_text = f"管道执行退出码异常（exit_code={exit_code}）。"
        except Exception as exc:  # noqa: BLE001 - capture background worker failures safely
            self.logger.exception("Background refresh failed")
            err_text = f"{type(exc).__name__}: {exc}"
        finally:
            with self._lock:
                self._state["running"] = False
                self._state["last_finished_at"] = utc_now_iso()
                if exit_code == 0 and err_text is None:
                    self._state["last_ok"] = True
                    self._state["last_summary"] = summary_text or "情报已更新"
                    self._state["last_error"] = None
                else:
                    self._state["last_ok"] = False
                    self._state["last_error"] = err_text or "刷新失败"

    def _build_digest_summary(self) -> str:
        try:
            data = json.loads(self.digest_path.read_text(encoding="utf-8"))
            items_cnt = len(data.get("items") or [])
            brief_cnt = len(data.get("top_briefings") or [])
            return f"已更新：共 {items_cnt} 条情报，{brief_cnt} 条速览"
        except (OSError, json.JSONDecodeError):
            return "已更新"

    def trigger_refresh(self) -> tuple[dict[str, Any], bool]:
        """Start background pipeline refresh if not already running."""
        with self._lock:
            if self._state["running"]:
                return dict(self._state), False
            self._state["running"] = True
            self._state["last_started_at"] = utc_now_iso()
            self._state["last_error"] = None
            snapshot = dict(self._state)

        thread = threading.Thread(
            target=self._run_refresh_worker,
            name="hub-refresh-worker",
            daemon=True,
        )
        thread.start()
        return snapshot, True

    def get_notify_status(self) -> dict[str, Any]:
        with self._notify_lock:
            return get_runtime_notify_status(
                config_path=self.config_path,
                db_path=self.db_path,
                state_path=self.notify_state_path,
            )

    def toggle_notify(self, enabled: bool | None = None) -> dict[str, Any]:
        config = load_config(self.config_path) if self.config_path.exists() else {}
        settings = get_notification_settings(config)
        with self._notify_lock:
            toggle_notify_state(
                state_path=self.notify_state_path,
                enabled=enabled,
                default_enabled=settings["enabled"],
            )
            return get_runtime_notify_status(
                config_path=self.config_path,
                db_path=self.db_path,
                state_path=self.notify_state_path,
            )

    def send_test_notification(self) -> dict[str, Any]:
        config = load_config(self.config_path) if self.config_path.exists() else {}
        settings = get_notification_settings(config)
        ok, err = send_test_toast(
            hub_url=self.hub_url,
            silent=settings["silent"],
            sender=self.notify_sender,
        )
        return {"ok": ok, "error": err}

    def start_notifier_loop(self) -> threading.Event:
        """Start daemon polling thread for native silent Windows notifications."""
        stop_event = threading.Event()

        def _worker() -> None:
            while not stop_event.is_set():
                interval = 60
                try:
                    if self.config_path.exists():
                        cfg = load_config(self.config_path)
                        interval = get_notification_settings(cfg)["poll_interval_seconds"]
                    with self._notify_lock:
                        summary = check_and_notify(
                            config_path=self.config_path,
                            db_path=self.db_path,
                            state_path=self.notify_state_path,
                            hub_url=self.hub_url,
                            sender=self.notify_sender,
                        )
                    if summary.get("sent_count"):
                        self.logger.info(
                            "[Notifier] 已投递桌面提醒：%s 条 (cs2=%s, music=%s)",
                            summary["sent_count"],
                            summary["cs2_sent"],
                            summary["music_sent"],
                        )
                    for err in summary.get("errors") or []:
                        self.logger.warning("[Notifier] 投递异常：%s", err)
                except Exception:  # noqa: BLE001 - keep HTTP server isolated from notifier failures
                    self.logger.exception("Background notifier poll failed")
                if stop_event.wait(interval):
                    break

        thread = threading.Thread(
            target=_worker,
            name="hub-notifier-worker",
            daemon=True,
        )
        thread.start()
        return stop_event

    def get_terms_payload(self) -> dict[str, Any]:
        with self._terms_lock:
            if self.daily_state_path is not None and self.daily_state_path.exists():
                backfill_daily_term_from_state(self.db_path, self.daily_state_path)
            return {
                "stats": get_glossary_stats(db_path=self.db_path),
                "terms": get_all_daily_terms(db_path=self.db_path),
            }

    def update_term_mastered(
        self, term_key: str, mastered: bool
    ) -> tuple[bool, dict[str, Any]]:
        with self._terms_lock:
            updated = set_term_mastered(
                term_key, mastered, db_path=self.db_path
            )
            if updated:
                export_terms_json(
                    db_path=self.db_path, output_path=self.terms_path
                )
            stats = get_glossary_stats(db_path=self.db_path)
        return updated, {
            "ok": updated,
            "term_key": term_key,
            "mastered": bool(mastered),
            "stats": stats,
        }


class HubRequestHandler(SimpleHTTPRequestHandler):
    """Serve static frontend assets, live digest.json, and refresh API endpoints."""

    def __init__(
        self,
        *args: Any,
        runtime: HubRuntime,
        **kwargs: Any,
    ) -> None:
        self.runtime = runtime
        super().__init__(*args, directory=str(runtime.dist_dir), **kwargs)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        self.runtime.logger.info("%s - %s", self.address_string(), format % args)

    def _send_json(
        self,
        status: int,
        payload: dict[str, Any],
        *,
        no_store: bool = True,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if no_store:
            self.send_header(
                "Cache-Control", "no-store, no-cache, must-revalidate"
            )
        self.end_headers()
        self.wfile.write(body)

    def _serve_live_json_file(self, file_path: Path, label: str) -> None:
        if not file_path.exists():
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"error": f"{label} not found", "path": str(file_path)},
            )
            return
        try:
            body = file_path.read_bytes()
        except OSError as exc:
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": f"Failed to read {label}: {exc}"},
            )
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server method name
        route = urlsplit(self.path).path

        if route == "/digest.json":
            self._serve_live_json_file(self.runtime.digest_path, "digest.json")
            return

        if route == "/terms.json":
            self._serve_live_json_file(self.runtime.terms_path, "terms.json")
            return

        if route == "/api/status":
            self._send_json(HTTPStatus.OK, self.runtime.get_status())
            return

        if route == "/api/notify/status":
            self._send_json(HTTPStatus.OK, self.runtime.get_notify_status())
            return

        if route == "/api/terms":
            self._send_json(HTTPStatus.OK, self.runtime.get_terms_payload())
            return

        if not self.runtime.dist_dir.exists():
            msg = (
                "前端静态构建目录 web/dist/ 不存在，请先执行 npm --prefix web run build。"
            ).encode("utf-8")
            self.send_response(HTTPStatus.SERVICE_UNAVAILABLE)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
            return

        target_path = Path(self.translate_path(self.path))
        if (
            route != "/"
            and not target_path.exists()
            and not route.startswith("/assets/")
            and (self.runtime.dist_dir / "index.html").exists()
        ):
            self.path = "/index.html"

        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802 - http.server method name
        route = urlsplit(self.path).path
        content_len = int(self.headers.get("Content-Length") or "0")
        raw_body = self.rfile.read(content_len) if content_len > 0 else b""

        if route == "/api/refresh":
            status_snapshot, started = self.runtime.trigger_refresh()
            self._send_json(
                HTTPStatus.ACCEPTED,
                {
                    "accepted": True,
                    "already_running": not started,
                    **status_snapshot,
                },
            )
            return

        if route == "/api/notify/toggle":
            desired: bool | None = None
            if raw_body.strip():
                try:
                    payload = json.loads(raw_body.decode("utf-8"))
                    if isinstance(payload, dict) and "enabled" in payload:
                        val = payload["enabled"]
                        if isinstance(val, bool):
                            desired = val
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": "Invalid JSON body"},
                    )
                    return
            self._send_json(HTTPStatus.OK, self.runtime.toggle_notify(desired))
            return

        if route == "/api/notify/test":
            res = self.runtime.send_test_notification()
            status = (
                HTTPStatus.OK if res.get("ok") else HTTPStatus.INTERNAL_SERVER_ERROR
            )
            self._send_json(status, res)
            return

        if route == "/api/terms/mastered":
            try:
                payload = json.loads(raw_body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send_json(
                    HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON body"}
                )
                return
            if not isinstance(payload, dict):
                self._send_json(
                    HTTPStatus.BAD_REQUEST, {"error": "Expected JSON object"}
                )
                return
            term_key = payload.get("term_key")
            mastered = payload.get("mastered")
            if not isinstance(term_key, str) or not term_key.strip() or not isinstance(mastered, bool):
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "Expected non-empty string 'term_key' and boolean 'mastered'"},
                )
                return
            updated, res = self.runtime.update_term_mastered(
                term_key.strip(), mastered
            )
            status = HTTPStatus.OK if updated else HTTPStatus.NOT_FOUND
            self._send_json(status, res)
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not Found"})


class ExclusiveThreadingHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that prevents multiple processes binding the same port on Windows."""

    allow_reuse_address = False

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(
                socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1
            )
        super().server_bind()


def _local_probe_host(host: str) -> str:
    cleaned = str(host or "").strip()
    if cleaned in {"", "0.0.0.0", "::", "[::]"}:
        return DEFAULT_HOST
    return cleaned


def is_hub_running(host: str, port: int, timeout: float = 1.0) -> bool:
    """Return True if our local Anti-Algorithm Hub server already responds on host:port."""
    probe_host = _local_probe_host(host)
    url = f"http://{probe_host}:{port}/api/status"
    try:
        opener = build_opener(ProxyHandler({}))
        req = Request(url, method="GET")
        with opener.open(req, timeout=timeout) as resp:
            if resp.status != 200:
                return False
            data = json.loads(resp.read().decode("utf-8"))
            return isinstance(data, dict) and data.get("service") == SERVICE_NAME
    except (URLError, OSError, json.JSONDecodeError, ValueError):
        return False


def serve_forever(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    dist_dir: Path = DEFAULT_DIST_DIR,
    digest_path: Path = DEFAULT_DIGEST_PATH,
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> int:
    """Start ThreadingHTTPServer cleanly; exit 0 if already running."""
    logger = _configure_runtime_logging()

    if is_hub_running(host, port):
        logger.info("服务已在 http://%s:%d 运行，跳过重复启动。", _local_probe_host(host), port)
        return 0

    runtime = HubRuntime(
        dist_dir=dist_dir,
        digest_path=digest_path,
        config_path=config_path,
        logger=logger,
        hub_url=f"http://{_local_probe_host(host)}:{port}",
    )
    handler_factory = partial(HubRequestHandler, runtime=runtime)

    try:
        httpd = ExclusiveThreadingHTTPServer((host, port), handler_factory)
    except OSError as exc:
        if is_hub_running(host, port):
            logger.info("服务已在 http://%s:%d 运行，跳过重复启动。", host, port)
        else:
            logger.warning(
                "端口 %s:%d 已被占用（%s），取消重复启动。", host, port, exc
            )
        return 0

    notify_stop_event = runtime.start_notifier_loop()
    logger.info(
        "反算法个人情报站本地服务已启动：http://%s:%d (dist=%s, digest=%s)",
        host,
        port,
        dist_dir,
        digest_path,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("收到停止信号，正在关闭本地服务...")
    finally:
        notify_stop_event.set()
        httpd.server_close()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Serve static web/dist assets, live digest.json, and local refresh API."
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Bind port (default 8765)")
    parser.add_argument("--dist", type=Path, default=DEFAULT_DIST_DIR, help="Path to web/dist")
    parser.add_argument(
        "--digest", type=Path, default=DEFAULT_DIGEST_PATH, help="Path to live digest.json"
    )
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Path to sources.yaml"
    )
    args = parser.parse_args()

    raise SystemExit(
        serve_forever(
            host=args.host,
            port=args.port,
            dist_dir=args.dist,
            digest_path=args.digest,
            config_path=args.config,
        )
    )


if __name__ == "__main__":
    main()
