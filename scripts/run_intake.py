"""
scripts/run_intake.py
=====================
Phase 17. Serves the live intake console and runs the real engine behind it.

Run from the repository root:
    python -m scripts.run_intake
    python -m scripts.run_intake --hospital small_ed
    python -m scripts.run_intake --port 9000
    python -m scripts.run_intake --no-browser

Then use Chrome or Edge. Speech recognition is a Chromium feature; every other
part of the page works anywhere, and the typed channel is always available.

WHY http.server AND NOT FLASK OR FASTAPI
----------------------------------------
Because the repository still installs nothing, and a demo that needs a package
index reachable is a demo that can fail in the room. `http.server` is in the
standard library, this file is a hundred lines, and the whole thing starts with
one command on a laptop that has never seen this project.

WHY LOOPBACK ONLY
-----------------
Bound to 127.0.0.1. The console is never exposed on a network interface, and
browsers grant camera and microphone access on a loopback origin without a
certificate, which is what makes a live sensor demo possible with no install
and no HTTPS setup.

WHAT CROSSES THE WIRE
---------------------
Confirmed clinical flags, and nothing else. No frame, no audio buffer and no
recording ever leaves the browser tab. The transcript is posted only if the
operator has left it in the box, because it becomes the chief complaint. The
server writes nothing to disk. See docs/privacy.md.
"""

from __future__ import annotations

import json
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from app.intake import render_intake
from core.config import HospitalConfig
from core.intake_bridge import (
    IntakeSession,
    PatientDataError,
    SymptomReader,
    load_intake_config,
)
from core.risk_engine import _load as load_json

REPO_ROOT = Path(__file__).resolve().parent.parent
WEIGHTS_FILE = REPO_ROOT / "data" / "risk_weights.json"

MAX_BODY = 256 * 1024


class IntakeHandler(BaseHTTPRequestHandler):
    session: IntakeSession = None       # set in main()
    page: str = ""
    reader: SymptomReader = None

    # Keep the console readable. One line per assessment, not per asset.
    def log_message(self, fmt, *args):
        if self.path in ("/assess", "/read"):
            sys.stdout.write(f"  {self.path}  {fmt % args}\n")

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # The page is same-origin and posts nothing outward. Locked down anyway.
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict):
        self._send(code, json.dumps(payload).encode("utf-8"), "application/json")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, self.page.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/health":
            self._json(200, {"ok": True, "hospital": self.session.hospital.name})
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            self._json(400, {"error": "empty or oversized request body"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            self._json(400, {"error": f"malformed JSON: {exc}"})
            return

        if self.path == "/read":
            reported, denied = self.reader.read(payload.get("text", ""))
            self._json(200, {
                "reported": reported,
                "denied": denied,
                "pain_score": self.reader.pain_score(payload.get("text", "")),
                "duration_hours": self.reader.duration_hours(payload.get("text", "")),
            })
            return

        if self.path == "/assess":
            try:
                result = self.session.assess(payload)
            except PatientDataError as exc:
                # The loader refused the payload. Surfaced verbatim rather than
                # patched over: a field the browser got wrong is a bug to see.
                self._json(200, {"error": str(exc)})
                return
            except Exception as exc:                       # noqa: BLE001
                self._json(200, {"error": f"{type(exc).__name__}: {exc}"})
                return

            print(f"    {result['band_code']} {result['band_word']:<6} "
                  f"risk {result['risk_score']:>5}  "
                  f"confidence {result['confidence_pct']}%  "
                  f"{result['changed_by']}")
            self._json(200, result)
            return

        self._json(404, {"error": "unknown endpoint"})


def _arg(args, flag, default=None):
    return args[args.index(flag) + 1] if flag in args else default


def main():
    args = sys.argv[1:]
    profile = _arg(args, "--hospital", "medium_ed")
    cfg = load_intake_config()
    server_cfg = cfg.get("server", {})
    host = server_cfg.get("host", "127.0.0.1")
    port = int(_arg(args, "--port", server_cfg.get("port", 8770)))

    weights = load_json(WEIGHTS_FILE)
    vocabulary = sorted(weights["symptoms"].keys())

    IntakeHandler.session = IntakeSession(profile)
    IntakeHandler.reader = SymptomReader(cfg, weights)
    IntakeHandler.page = render_intake(cfg, vocabulary)

    hospital = HospitalConfig.load(profile)
    url = f"http://{host}:{port}/"

    try:
        server = HTTPServer((host, port), IntakeHandler)
    except OSError as exc:
        print(f"could not bind {host}:{port} -- {exc}")
        print("another copy may already be running; try --port 8771")
        return 1

    print()
    print("  PatientTriage.ai  --  live intake console")
    print("  " + "-" * 58)
    print(f"  open        {url}")
    print(f"  hospital    {hospital.name}")
    print(f"  vocabulary  {len(vocabulary)} scoreable symptom terms")
    print(f"  engine      real pipeline: score, uncertainty, safety, ratchet")
    print()
    print("  Use Chrome or Edge for speech recognition. Camera and microphone")
    print("  are granted on this loopback origin without a certificate.")
    print("  Nothing captured is written to disk. Ctrl-C to stop.")
    print()

    if "--no-browser" not in args:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped. nothing was written to disk.\n")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
