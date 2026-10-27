import json
import os
import shutil
import tempfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


APP_DIR = Path(__file__).resolve().parent
LOCAL_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "SDP Sales Tracker"
DATA_FILE = LOCAL_DATA_DIR / "sdp-data.json"
BACKUP_FILE = LOCAL_DATA_DIR / "sdp-data.json.bak"
MAX_BODY_BYTES = 16 * 1024 * 1024


class SdpRequestHandler(SimpleHTTPRequestHandler):
    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/data":
            if not DATA_FILE.exists() and not BACKUP_FILE.exists():
                self._send_json(404, {"missing": True, "path": str(DATA_FILE)})
                return

            recovered_from_backup = False
            try:
                with DATA_FILE.open("r", encoding="utf-8") as data_file:
                    payload = json.load(data_file)
            except (OSError, json.JSONDecodeError):
                try:
                    with BACKUP_FILE.open("r", encoding="utf-8") as backup_file:
                        payload = json.load(backup_file)
                    recovered_from_backup = True
                except (OSError, json.JSONDecodeError):
                    self._send_json(500, {"error": "Saved data and backup could not be read."})
                    return

            if not isinstance(payload, dict) or not isinstance(payload.get("customers"), list) or not isinstance(payload.get("provinces"), list):
                self._send_json(500, {"error": "Saved data has an invalid format."})
                return

            self._send_json(200, {"data": payload, "path": str(DATA_FILE), "recoveredFromBackup": recovered_from_backup})
            return

        if path.startswith("/api/"):
            self._send_json(404, {"error": "Not found"})
            return

        super().do_GET()

    def do_PUT(self):
        if urlsplit(self.path).path != "/api/data":
            self._send_json(404, {"error": "Not found"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "Invalid Content-Length"})
            return

        if content_length <= 0 or content_length > MAX_BODY_BYTES:
            self._send_json(413, {"error": "Request body is empty or too large"})
            return

        try:
            payload = json.loads(self.rfile.read(content_length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "Invalid JSON"})
            return

        if not isinstance(payload, dict) or not isinstance(payload.get("customers"), list) or not isinstance(payload.get("provinces"), list):
            self._send_json(400, {"error": "Expected customers and provinces arrays"})
            return

        LOCAL_DATA_DIR.mkdir(parents=True, exist_ok=True)
        temporary_path = None
        try:
            if DATA_FILE.exists():
                try:
                    with DATA_FILE.open("r", encoding="utf-8") as data_file:
                        previous = json.load(data_file)
                    if isinstance(previous, dict) and isinstance(previous.get("customers"), list) and isinstance(previous.get("provinces"), list):
                        shutil.copy2(DATA_FILE, BACKUP_FILE)
                except (OSError, json.JSONDecodeError):
                    pass

            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=LOCAL_DATA_DIR,
                prefix="sdp-data-",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                json.dump(payload, temporary_file, ensure_ascii=False, indent=2)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
                temporary_path = Path(temporary_file.name)

            os.replace(temporary_path, DATA_FILE)
        except OSError as error:
            if temporary_path and temporary_path.exists():
                temporary_path.unlink(missing_ok=True)
            self._send_json(500, {"error": str(error)})
            return

        self._send_json(200, {"ok": True, "path": str(DATA_FILE), "customerCount": len(payload["customers"])})


if __name__ == "__main__":
    os.chdir(APP_DIR)
    server = ThreadingHTTPServer(("127.0.0.1", 8000), SdpRequestHandler)
    print("SDP Tracker: http://localhost:8000/sdp.html")
    print(f"Data file: {DATA_FILE}")
    server.serve_forever()