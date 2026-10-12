"""Loopback WebUI and a background numerical-search worker; no external service."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import traceback
from urllib.parse import parse_qs, urlsplit

from .balance import BalanceSearch
from .config import ExperimentConfig
from .monitoring import DEFAULT_CAPTURE_DECISIONS, FileSearchMonitor, SearchStopped

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAXIMUM_BODY_BYTES = 1024
DEFAULT_EPISODE_LIMIT = 100
MAXIMUM_EPISODE_LIMIT = 500
STATIC_FILES = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
                "/analysis.js": ('analysis.js', 'text/javascript'), "/style.css": ("style.css", "text/css")}
WEB_DIRECTORY = Path(__file__).with_name("web")


class DashboardService:
    def __init__(self, config, settings, directory, capture_decisions=DEFAULT_CAPTURE_DECISIONS):
        self.search = BalanceSearch(config, settings)
        self.monitor = FileSearchMonitor(directory, capture_decisions)
        self.thread = None

    def start(self):
        if self.thread is not None:
            raise RuntimeError("Search worker already started")
        self.thread = threading.Thread(target=self.run, name="balance-search", daemon=True)
        self.thread.start()

    def run(self):
        try:
            self.search.run(self.monitor.directory, observer=self.monitor)
        except SearchStopped as error:
            self.monitor.publish(state="stopped", message=str(error))
        except Exception as error:
            self.monitor.publish(state="failed", error=str(error))
            traceback.print_exc()

    def episodes(self, offset, limit):
        path = self.monitor.directory / "episodes.jsonl"
        records = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # An append may still be in progress; read the completed lines only.
        return {"total": len(records), "episodes": records[offset:offset + limit]}


def handler_for(service):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def send(self, status, body, content_type="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def valid_host(self):
            allowed = {f"{HOST}:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            return self.headers.get("Host") in allowed

        def do_GET(self):
            if not self.valid_host():
                return self.send(403, {"error": "Local host required"})
            request = urlsplit(self.path)
            try:
                if request.path in STATIC_FILES:
                    name, kind = STATIC_FILES[request.path]
                    return self.send(200, (WEB_DIRECTORY / name).read_bytes(), kind)
                if request.path == "/api/status":
                    return self.send(200, service.monitor.snapshot())
                if request.path == '/api/analysis':
                    with service.monitor.lock:
                        analysis = service.monitor.analysis
                        return self.send(200, {k:v for k,v in analysis.items() if k != 'records'} if analysis else None)
                if request.path == "/api/episodes":
                    query = parse_qs(request.query)
                    offset = max(0, int(query.get("offset", [0])[0]))
                    limit = min(MAXIMUM_EPISODE_LIMIT, max(1, int(query.get("limit", [DEFAULT_EPISODE_LIMIT])[0])))
                    return self.send(200, service.episodes(offset, limit))
                if request.path.startswith("/api/frames/"):
                    episode_id = int(request.path.rsplit("/", 1)[-1])
                    if episode_id < 0:
                        raise ValueError("Episode ID must be nonnegative")
                    path = service.monitor.directory / "frames" / f"episode-{episode_id:06d}.json"
                    with service.monitor.lock:
                        data = path.read_bytes()
                    return self.send(200, data)
                return self.send(404, {"error": "Not found"})
            except FileNotFoundError:
                self.send(404, {"error": "Frame not available yet"})
            except (ValueError, json.JSONDecodeError) as error:
                self.send(400, {"error": str(error)})

        def do_POST(self):
            origin = self.headers.get("Origin")
            allowed_origins = {f"http://{HOST}:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
            if not self.valid_host() or (origin is not None and origin not in allowed_origins):
                return self.send(403, {"error": "Local origin required"})
            if self.path != "/api/control":
                return self.send(404, {"error": "Not found"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAXIMUM_BODY_BYTES:
                    raise ValueError("Invalid request size")
                action = json.loads(self.rfile.read(length))["action"]
                if service.monitor.snapshot()["state"] in ("completed", "failed", "stopped"):
                    return self.send(409, {"error": "Search is no longer running"})
                service.monitor.control.request(action)
                return self.send(200, {"requested": action})
            except (ValueError, KeyError, TypeError) as error:
                self.send(400, {"error": str(error)})

    return Handler


def main():
    parser = argparse.ArgumentParser(description="Start character-stat search with a local WebUI")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--capture-every", type=int, default=DEFAULT_CAPTURE_DECISIONS)
    args = parser.parse_args()
    # Reject an occupied run before starting any worker or opening the port.
    if args.run.exists() and any(args.run.iterdir()):
        raise FileExistsError("Use a fresh run directory; dashboard restart does not resume a stopped process")
    service = DashboardService(ExperimentConfig.load(args.config),
        json.loads(args.settings.read_text(encoding="utf-8-sig")), args.run, args.capture_every)
    server = ThreadingHTTPServer((HOST, args.port), handler_for(service))
    print(f"Dashboard: http://{HOST}:{server.server_port}", flush=True)
    service.start()
    try:
        server.serve_forever()
    finally:
        service.monitor.control.request("stop")
        if service.thread:
            service.thread.join(timeout=service.search.config.data["environment_options"].get("timeout_seconds", 60))
        server.server_close()


if __name__ == "__main__":
    main()
