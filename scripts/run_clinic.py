"""
scripts/run_clinic.py
=====================
Serves both dashboards and the shared session behind them.

    python -m scripts.run_clinic

    patient   http://127.0.0.1:8780/patient
    nurse     http://127.0.0.1:8780/nurse

Open them in two windows. Speak into one and watch the other.

REAL TIME, WITH THE STANDARD LIBRARY
------------------------------------
Server-Sent Events. One long-lived GET per client, the server writes a frame
whenever the session changes, the browser's EventSource reconnects on its own.

WebSockets would be the reflex choice and would be worse here. They need a
handshake, a framing layer and a dependency, and this traffic goes one way:
the session changes, both screens redraw. Client actions are ordinary POSTs.
SSE is thirty lines of `http.server` and reconnects for free, and this project
has installed nothing since Phase 1.

`ThreadingHTTPServer` because an SSE connection holds a thread for its whole
life and a single-threaded server would be blocked by the first client.

ONE SESSION, TWO VIEWS
----------------------
Both dashboards subscribe to the same ClinicSession. There is no patient copy
and no nurse copy. When a nurse changes a severity the session recomputes and
pushes to everyone, including the patient screen, which chooses to display
almost none of it.

LANGUAGE PROVIDER
-----------------
Set ANTHROPIC_API_KEY for model-based extraction. Without it the deterministic
matcher serves and the console says so in the header rather than pretending.
Set PATIENTTRIAGE_PROVIDER=local to force the offline path even with a key.
"""

from __future__ import annotations

import json
import queue
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import parse_qs, urlparse

from app.nurse import render_nurse
from app.patient import render_patient
from core.ai import describe_providers, get_provider
from core.capture_fusion import CaptureFusion
from core.notes import generate as generate_notes
from core.questions import QuestionEngine
from core.config import HospitalConfig
from core.risk_engine import RiskEngine, _load
from core.session import ClinicSession

REPO_ROOT = Path(__file__).resolve().parent.parent
WEIGHTS_FILE = REPO_ROOT / "data" / "risk_weights.json"

HOST = "127.0.0.1"
PORT = 8780
MAX_BODY = 512 * 1024


class Clinic:
    """
    Every live encounter in this process.

    One object so the nurse console can find the session a patient just opened
    without either screen knowing about the other.
    """

    def __init__(self, hospital: str = "medium_ed"):
        self.hospital = hospital
        self.provider = get_provider()
        self.sessions: Dict[str, ClinicSession] = {}
        self.fusion = CaptureFusion()
        # The question engine prices each question by re-running the risk
        # engine on every hypothetical answer, so it needs one of its own.
        self.questions = QuestionEngine(RiskEngine(HospitalConfig.load(hospital)))
        self.notes: Dict[str, str] = {}
        self._counter = 0
        self._lock = threading.Lock()

    def open(self) -> ClinicSession:
        with self._lock:
            self._counter += 1
            sid = f"ENC-{self._counter:03d}"
        session = ClinicSession(sid, self.provider, self.hospital)
        self.sessions[sid] = session
        return session

    def get(self, sid: str) -> Optional[ClinicSession]:
        return self.sessions.get(sid)

    def view(self, session: ClinicSession) -> dict:
        """
        The snapshot plus the two things only the clinic can add: the next
        question, and the notes.
        """
        snap = session.snapshot()
        question, qid, why = self.next_question(session)
        snap["next_question"] = question
        snap["next_question_id"] = qid
        snap["next_question_why"] = why
        snap["notes"] = (session.nurse_notes_override
                         or self.notes.get(session.session_id)
                         or generate_notes(snap))
        return snap

    # An opening ladder for when there is not yet enough on file for the
    # value-of-information engine to price anything. Ordered by how much a
    # triage decision usually turns on the answer. Without this the console
    # asked nothing at all until a symptom had already been extracted, which
    # is precisely the moment a patient most needs to be asked something.
    OPENING_LADDER = [
        ("no_complaint", lambda s: not s.transcript,
         "What has brought you in today?"),
        ("no_symptom", lambda s: not [e for e in s.ledger.values() if e.active],
         "Can you tell me where it hurts, or what feels wrong?"),
        ("no_onset", lambda s: not any(e.onset or e.duration_hours is not None
                                       for e in s.ledger.values() if e.active),
         "When did this start?"),
        ("no_severity", lambda s: not any(e.severity is not None
                                          for e in s.ledger.values() if e.active),
         "On a scale of nought to ten, how bad is it right now?"),
        ("no_progression", lambda s: not any(e.progression
                                             for e in s.ledger.values() if e.active),
         "Is it getting worse, getting better, or staying about the same?"),
        ("no_vitals", lambda s: not s.observations,
         "Has anyone taken your temperature or checked your pulse today?"),
        ("no_history", lambda s: s.demographics.get("history_tier") == "zero",
         "Do you take any regular medication, or have any conditions we "
         "should know about?"),
    ]

    def next_question(self, session: ClinicSession):
        """
        The next thing worth asking, and why, in three tiers.

        1. THE MODEL. It has read the whole conversation and can ask something
           specific to it. Best question when a key is configured.
        2. VALUE OF INFORMATION. Phase 11 prices every question in the bank by
           re-running the engine on each hypothetical answer and picks the one
           that could move the band furthest. Best question offline.
        3. THE OPENING LADDER. When there is not enough on file to price
           anything, ask the thing a nurse would ask. The previous version
           returned nothing here, so a patient at L1 with no symptoms yet was
           asked nothing at all -- the exact moment asking matters most.

        Returns ("", "") when the gate is open. Asking somebody who may be
        dying about onset is not thoroughness.
        """
        if not session.routine_questions_allowed:
            return "", "", ""

        asked = {q.get("id") for q in session.questions_asked}

        if session.complete:
            return "", "", ""

        model_id = f"model:{hash(session.model_question) & 0xffff:04x}"
        if session.model_question and model_id not in asked:
            return (session.model_question, model_id,
                    "generated by the language model from this conversation")
        try:
            from core.intake_bridge import build_patient
            value = self.questions.next_question(build_patient(session._payload()))
            if value is not None and value.question.id not in asked:
                span = getattr(value, "escalation_span", 0)
                return (value.question.text, value.question.id,
                        f"value of information: {value.question.id}, could move "
                        f"the band by {span}")
        except Exception:                                   # noqa: BLE001
            pass

        for qid, applies, text in self.OPENING_LADDER:
            if qid in asked:
                continue
            try:
                if applies(session):
                    return (text, qid,
                            f"opening ladder: {qid.replace('_', ' ')}")
            except Exception:                               # noqa: BLE001
                continue

        # Nothing left worth asking. That is a terminal state, not a silence:
        # the previous version returned nothing here and the patient sat
        # looking at a screen that had quietly stopped.
        return "", "", "exhausted"


class Handler(BaseHTTPRequestHandler):
    clinic: Clinic = None
    patient_page = ""
    nurse_page = ""

    def log_message(self, fmt, *args):
        if self.path.startswith("/api/") and "/stream" not in self.path:
            sys.stdout.write(f"  {self.path}\n")

    # -- plumbing ----------------------------------------------------------

    def _send(self, code, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, payload):
        self._send(code, json.dumps(payload).encode("utf-8"), "application/json")

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def _session(self, payload) -> Optional[ClinicSession]:
        return self.clinic.get(str(payload.get("session_id", "")))

    # -- GET ---------------------------------------------------------------

    def do_GET(self):
        route = urlparse(self.path)
        if route.path in ("/", "/patient"):
            self._send(200, self.patient_page.encode("utf-8"),
                       "text/html; charset=utf-8")
        elif route.path == "/nurse":
            self._send(200, self.nurse_page.encode("utf-8"),
                       "text/html; charset=utf-8")
        elif route.path == "/api/sessions":
            self._json(200, {"sessions": list(self.clinic.sessions)})
        elif route.path == "/api/stream":
            self._stream(parse_qs(route.query).get("session_id", [""])[0])
        else:
            self._send(404, b"not found", "text/plain")

    def _stream(self, session_id: str):
        """
        One SSE connection. Holds the thread until the client goes away.

        A bounded queue on purpose: a browser tab that stops reading must not
        grow a queue in the server until it falls over. When it fills, the
        oldest frame is dropped, because each frame is the WHOLE state and a
        stale one has no value once a newer one exists.
        """
        session = self.clinic.get(session_id)
        if session is None:
            self._json(404, {"error": "no such session"})
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        frames: queue.Queue = queue.Queue(maxsize=8)

        def push(_snapshot):
            try:
                frames.put_nowait(self.clinic.view(session))
            except queue.Full:
                try:
                    frames.get_nowait()
                    frames.put_nowait(self.clinic.view(session))
                except queue.Empty:
                    pass

        unsubscribe = session.subscribe(push)
        try:
            self._frame(self.clinic.view(session))
            while True:
                try:
                    self._frame(frames.get(timeout=15))
                except queue.Empty:
                    # Keep-alive comment. Proxies and browsers both drop a
                    # connection that has been silent too long.
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            unsubscribe()

    def _frame(self, payload: dict):
        self.wfile.write(b"data: " + json.dumps(payload).encode("utf-8") + b"\n\n")
        self.wfile.flush()

    # -- POST --------------------------------------------------------------

    def do_POST(self):
        route = urlparse(self.path).path
        payload = self._body()

        if route == "/api/session":
            session = self.clinic.open()
            print(f"  encounter {session.session_id} opened")
            self._json(200, {"session_id": session.session_id})
            return

        session = self._session(payload)
        if session is None:
            self._json(404, {"error": "no such session"})
            return

        try:
            self._json(200, self._dispatch(route, session, payload))
        except Exception as exc:                            # noqa: BLE001
            self._json(200, {"error": f"{type(exc).__name__}: {exc}"})

    def _dispatch(self, route: str, session: ClinicSession, p: dict) -> dict:
        clinic = self.clinic

        if route == "/api/say":
            before = session.emergency.active
            session.hear(p.get("text", ""), float(p.get("at_second", 0) or 0))
            if session.emergency.active and not before:
                triggers = ", ".join(t.trigger_id
                                     for t in session.emergency.active_triggers)
                print(f"  *** EMERGENCY  {session.session_id}  {triggers}")
            return {"ok": True}

        if route == "/api/frame":
            return self._frame_measurements(session, p)

        if route == "/api/asked":
            session.record_question(p.get("question_id", ""),
                                    p.get("text", ""), p.get("why", ""))
            return {"ok": True}

        if route == "/api/capture":
            session.set_capture(p.get("facial"), p.get("voice"))
            return {"ok": True}

        if route == "/api/observations":
            session.set_observations(**{k: v for k, v in p.items()
                                        if k != "session_id"})
            return {"ok": True}

        if route == "/api/demographics":
            session.set_demographics(**{k: v for k, v in p.items()
                                        if k != "session_id"})
            return {"ok": True}

        if route == "/api/correct":
            session.nurse_remove_symptom(p.get("term", ""),
                                         "patient says they did not say this",
                                         nurse="patient")
            return {"ok": True}

        if route == "/api/finish":
            session.finish(p.get("reason") or "the patient ended the encounter")
            snap = clinic.view(session)
            clinic.notes[session.session_id] = snap["notes"]
            return {"ok": True}

        # -- nurse actions -------------------------------------------------
        nurse = p.get("nurse", "nurse")
        if route == "/api/nurse/severity":
            session.nurse_set_severity(p.get("term", ""), p.get("severity"), nurse)
        elif route == "/api/nurse/remove":
            session.nurse_remove_symptom(p.get("term", ""), p.get("reason", ""), nurse)
        elif route == "/api/nurse/add":
            session.nurse_add_symptom(p.get("term", ""), p.get("severity"),
                                      p.get("note", ""), nurse)
        elif route == "/api/nurse/observation":
            session.nurse_review_observation(p.get("observation_id", ""),
                                             p.get("status", "confirmed"),
                                             p.get("note", ""), nurse)
        elif route == "/api/nurse/flag":
            session.nurse_review_flag(p.get("flag_id", ""),
                                      p.get("status", "reviewed"),
                                      p.get("note", ""), nurse)
        elif route == "/api/nurse/acknowledge":
            session.nurse_acknowledge_emergency(nurse)
        elif route == "/api/nurse/dismiss":
            session.nurse_dismiss_trigger(p.get("trigger_id", ""),
                                          p.get("reason", ""), nurse)
        elif route == "/api/nurse/notes":
            text = p.get("text", "")
            if text:
                session.nurse_set_notes(text, nurse)
                clinic.notes[session.session_id] = text
            else:
                session.nurse_notes_override = ""
                clinic.notes.pop(session.session_id, None)
                session._broadcast()
        else:
            return {"error": "unknown endpoint"}
        return {"ok": True}

    def _frame_measurements(self, session: ClinicSession, p: dict) -> dict:
        """
        Camera measurements from the patient page.

        The browser measures; the fuser decides whether the measurement is
        usable at all. A reading rejected for side lighting or instability
        produces no observation, because a measurement taken in conditions
        that make it meaningless is not evidence and must not reach a nurse
        dressed as one.
        """
        at = float(p.get("at_second", 0) or 0)
        camera = {
            "index": float(p.get("symmetry", 0) or 0),
            # The real frame-to-frame spread the browser measured across the
            # burst. This was a hardcoded 0.01 -- a constant chosen so the
            # stability gate would always pass, which defeated the entire
            # point of having a stability gate and let a single noisy frame
            # report "possible asymmetry". The browser now sends what it
            # actually measured, and a scan that cannot produce a spread sends
            # nothing so the fuser rejects it.
            "spread": float(p.get("spread", 1.0) or 1.0),
            "brightness": float(p.get("brightness", 0) or 0),
            "structure": float(p.get("structure", 0) or 0),
            "gradient": float(p.get("gradient", 0) or 0),
            "frames": int(p.get("frames", 0) or 0),
            "method": str(p.get("method", "luminance comparison")),
        }
        # The camera ran. Record that, whatever the reading turned out to be:
        # "not attempted" on an encounter where the patient sat in front of a
        # live camera for two minutes is simply false.
        if session.flags.get("facial_capture_status") != "ok":
            session.set_capture(facial="ok")

        method = camera.pop("method", "luminance comparison")
        result = self.clinic.fusion.fuse(camera, None)
        camera["method"] = method
        facial = result.facial

        # Only a STRONG, stable, well-lit, gradient-corrected reading raises an
        # observation. "Possible" used to raise one too, which put a facial
        # asymmetry candidate on a nurse's screen on the strength of a reading
        # the fuser itself had called too close to call.
        if facial.strength == "strong":
            already = any(o["kind"] == "asymmetry"
                          for o in session.visual_observations)
            if not already:
                session.observe_visual(
                    "asymmetry",
                    f"Facial asymmetry candidate, by {camera['method']}. "
                    f"Measured over {camera['frames']} frames, index "
                    f"{camera['index']:.3f}, frame-to-frame spread "
                    f"{camera['spread']:.3f}. Whether this is new or lifelong "
                    f"cannot be determined from an image and must be asked.",
                    at, measurements=camera)
        return {"strength": facial.strength, "reasons": facial.reasons}


def main():
    args = sys.argv[1:]
    port = int(args[args.index("--port") + 1]) if "--port" in args else PORT
    hospital = args[args.index("--hospital") + 1] if "--hospital" in args else "medium_ed"

    clinic = Clinic(hospital)
    vocabulary = sorted(_load(WEIGHTS_FILE)["symptoms"].keys())

    Handler.clinic = clinic
    Handler.patient_page = render_patient({}, use_landmarks="--landmarks" in args)
    Handler.nurse_page = render_nurse(vocabulary)

    try:
        server = ThreadingHTTPServer((HOST, port), Handler)
    except OSError as exc:
        print(f"could not bind {HOST}:{port} -- {exc}")
        return 1
    server.daemon_threads = True

    print()
    print("  PatientTriage.ai  --  live clinic")
    print("  " + "-" * 60)
    print(f"  patient     http://{HOST}:{port}/patient")
    print(f"  nurse       http://{HOST}:{port}/nurse")
    print()
    print(f"  language    {clinic.provider.describe()}")
    for entry in describe_providers():
        mark = "available" if entry["available"] else "not configured"
        print(f"                {entry['name']:<32} {mark}")
    if clinic.provider.kind != "model":
        print()
        print("  No ANTHROPIC_API_KEY set, so the deterministic matcher is")
        print("  serving. It understands common phrasings and will miss")
        print("  unusual ones. Export a key for model-based extraction.")
    print()
    print("  Open both pages side by side. There is no Assess button:")
    print("  speak, and the nurse screen updates as you do.")
    print("  Nothing captured is written to disk. Ctrl-C to stop.")
    print()

    if "--landmarks" in args:
        print("  landmarks   MediaPipe Face Landmarker (Apache 2.0), loaded from")
        print("              a CDN. Geometric asymmetry instead of a luminance")
        print("              comparison. Falls back silently if it will not load.")
        print()

    if "--no-browser" not in args:
        threading.Timer(0.6, lambda: webbrowser.open(
            f"http://{HOST}:{port}/nurse")).start()
        threading.Timer(1.2, lambda: webbrowser.open(
            f"http://{HOST}:{port}/patient")).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped. nothing was written to disk.\n")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
