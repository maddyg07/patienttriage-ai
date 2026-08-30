"""
scripts/run_harness.py
======================
Phase 21. Injects a scripted multimodal encounter and prints what every stage
of the pipeline actually did.

WHY THIS EXISTS
---------------
The complaint that opened Phase 21 was not "the analysis is wrong". It was
"the analysis appears to be happening when some stages are only collecting
measurements or asking the operator". You cannot fix that by reading the code,
because the code looks like it runs -- every stage has a function, every
function returns something, and the console renders the something.

So this harness reports one extra thing per stage that no other view in the
repository reports: RAN or DID NOT RUN, and when it did not, why. A stage that
returns an empty result because it was never given input is not the same as a
stage that examined its input and found nothing, and a console that draws them
identically is how you end up believing in analysis that is not occurring.

    ANALYSED      the stage received input and reasoned over it
    MEASURED      numbers were collected; no interpretation was applied
    NOT RUN       the stage never executed, with the reason
    NO INPUT      the stage ran and had nothing to work with

HONESTY NOTE
------------
This harness injects observations. It does not open a camera or a microphone.
Where a stage is marked MEASURED rather than ANALYSED, that is the truth about
this repository today, and the point of printing it is that the truth should
be visible in the tool rather than discovered on stage.

USAGE
    python -m scripts.run_harness                 # every scenario
    python -m scripts.run_harness negation        # one scenario by name
    python -m scripts.run_harness --list
"""

from __future__ import annotations

import sys
from typing import Dict, List, Optional

from core.ai.local_provider import LocalProvider
from core.emergency import EmergencyGate
from core.session import (MIN_PERSISTENT_OBSERVATIONS,
                          MULTIMODAL_WINDOW_SECONDS, ClinicSession)

BAR = "=" * 74
RULE = "-" * 74


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------
# Each is a list of (kind, payload) steps replayed in order against one
# session. `expect` is what a reader should see, written down so the harness
# doubles as documentation of intended behaviour.

SCENARIOS: Dict[str, dict] = {
    "emergency": {
        "why": "A genuine, current, first-person emergency.",
        "expect": "EMERGENCY declared; routine questions stop.",
        "steps": [("say", "I can't breathe and I'm about to pass out")],
    },
    "negation": {
        "why": "A dangerous phrase the patient is denying.",
        "expect": "No emergency. The suppression is recorded with its reason.",
        "steps": [("say", "I am not having a heart attack, I just feel anxious")],
    },
    "history": {
        "why": "A catastrophic event in the patient's past.",
        "expect": "No emergency. Today's complaint is the headache.",
        "steps": [("say", "I had a heart attack five years ago but I'm here "
                          "today because of a mild headache")],
    },
    "third-person": {
        "why": "Something that happened to somebody else.",
        "expect": "No emergency. The patient is not the subject.",
        "steps": [("say", "My father had a heart attack last year")],
    },
    "hypothetical": {
        "why": "A question, not a report.",
        "expect": "No emergency.",
        "steps": [("say", "If someone had a heart attack, what would it feel like?")],
    },
    "flicker": {
        "why": "One frame of apparent grimacing against a denial of distress.",
        "expect": "NO contradiction. Logged as a transient candidate.",
        "steps": [("see", ("grimacing", "possible grimace", 20.0)),
                  ("say", "I'm fine", 21.0)],
    },
    "discrepancy": {
        "why": "Both channels disagree with the statement, independently.",
        "expect": "Contradiction -> review flag. NOT an emergency, and "
                  "questioning continues.",
        "steps": [("see", ("distress", "apparent facial discomfort", 20.0)),
                  ("hear_obs", ("strain", "strained voice", 21.0)),
                  ("say", "I'm completely fine", 22.0)],
    },
    "mechanism": {
        "why": "The Phase 17 regression: an emergency that is not a symptom.",
        "expect": "EMERGENCY via the mechanism layer.",
        "steps": [("say", "I've been in a car accident and my leg is amputated")],
    },
}


# ---------------------------------------------------------------------------
# Stage reporting
# ---------------------------------------------------------------------------

def _status(ran: bool, analysed: bool, reason: str = "") -> str:
    if not ran:
        return f"NOT RUN    ({reason})" if reason else "NOT RUN"
    if not analysed:
        return f"MEASURED   ({reason})" if reason else "MEASURED"
    return "ANALYSED"


def _stage(name: str, status: str, detail: str = "") -> None:
    print(f"  {name:<26} {status}")
    if detail:
        for line in detail.splitlines():
            print(f"  {'':<26} {line}")


def report(session: ClinicSession, said: List[str]) -> None:
    """Walk the pipeline and print what each stage did to this encounter."""
    text = " ".join(said)

    # -- 1. speech recognition ------------------------------------------
    _stage("1 speech recognition",
           _status(True, False,
                   "text injected by harness; no microphone in this "
                   "environment"),
           f"heard: {len(session.transcript)} fragment(s)")

    # -- 2. emergency gate: context -------------------------------------
    gate = EmergencyGate()
    triggers = gate.evaluate(text=text)
    suppressed = gate.last_suppressions
    detail = []
    for t in triggers:
        detail.append(f"FIRED   {t.trigger_id} [{t.layer}] -- {t.why}")
    for s in suppressed:
        detail.append(f"SUPPRESSED {s['trigger_id']} -- {s['reason']} "
                      f"(marker: {s['marker']!r})")
    if not detail:
        detail.append("no trigger phrase present")
    _stage("2 emergency gate", _status(True, True), "\n".join(detail))

    # -- 3. clinical concept extraction ---------------------------------
    active = [e for e in session.ledger.values() if e.active]
    scored = [e.term for e in active if e.scoreable]
    unscored = [e.term for e in active if not e.scoreable]
    extract_detail = [f"scoreable:   {scored or '-'}",
                      f"recognised, no scoring rule: {unscored or '-'}",
                      f"denials:     {list(session.denials) or '-'}"]
    _stage("3 concept extraction",
           _status(True, True, ""),
           "\n".join(extract_detail))
    print(f"  {'':<26} provider: {session.provider.name}"
          f"{'  [DEGRADED]' if session.degraded else ''}")

    # -- 4. facial analysis ---------------------------------------------
    vis = session.visual_observations
    _stage("4 facial analysis",
           _status(bool(vis), False,
                   "observations injected; no frame capture or landmark "
                   "model in this repository")
           if vis else _status(False, False, "no visual input"),
           "\n".join(f"{v['at_second']:>5.1f}s  {v['kind']}: {v['description']}"
                     for v in vis))

    # -- 5. audio analysis ----------------------------------------------
    aud = session.audio_observations
    _stage("5 audio analysis",
           _status(bool(aud), False,
                   "observations injected; no acoustic feature extraction "
                   "in this repository")
           if aud else _status(False, False, "no audio input"),
           "\n".join(f"{a['at_second']:>5.1f}s  {a['kind']}: {a['description']}"
                     for a in aud))

    # -- 6. temporal alignment / fusion ---------------------------------
    contra = [e for e in session.events if e.kind == "contradiction"]
    trans = [e for e in session.events if e.kind == "transient_candidate"]
    fusion_detail = []
    if contra:
        d = contra[-1].detail
        fusion_detail.append(
            f"CONTRADICTION  corroborated={d.get('corroborated')} "
            f"sustained={d.get('sustained')}")
        fusion_detail.append(
            f"               visual x{d.get('visual_observations')}, "
            f"audio x{d.get('audio_observations')} "
            f"within {d.get('window_seconds')}s")
    if trans:
        d = trans[-1].detail
        fusion_detail.append(
            f"TRANSIENT      rejected: visual x{d.get('visual_seen')}, "
            f"audio x{d.get('audio_seen')}, "
            f"needed {d.get('needed')} or both channels")
    if not fusion_detail:
        fusion_detail.append("nothing to align")
    _stage("6 temporal fusion",
           _status(bool(vis or aud), True,
                   f"window {MULTIMODAL_WINDOW_SECONDS}s, "
                   f"persistence >= {MIN_PERSISTENT_OBSERVATIONS}")
           if (vis or aud) else _status(False, False, "no observations"),
           "\n".join(fusion_detail))

    # -- 7. risk engine / ratchet ---------------------------------------
    result = session.last_result or {}
    _stage("7 risk engine",
           _status(bool(result), True),
           f"score {result.get('risk_score', '-')}/100   "
           f"band {result.get('band_word', '-')}   "
           f"confidence {result.get('confidence_pct', '-')}%\n"
           f"plausible bands: {result.get('plausible_bands', '-')}")

    # -- 8. final state --------------------------------------------------
    print(RULE)
    print(f"  status                   : {session.status.upper()}")
    print(f"  emergency active         : {session.emergency.active}")
    print(f"  routine questions allowed: {session.routine_questions_allowed}")
    print(f"  review flags             : {len(session.review_flags)}")
    if session.emergency.active_triggers:
        print("  triggers                 : "
              + ", ".join(t.trigger_id for t in session.emergency.active_triggers))


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run(name: str, spec: dict) -> None:
    print()
    print(BAR)
    print(f"  SCENARIO: {name}")
    print(f"  {spec['why']}")
    print(f"  EXPECT: {spec['expect']}")
    print(BAR)

    session = ClinicSession(f"HARNESS-{name}", LocalProvider())
    said: List[str] = []

    for step in spec["steps"]:
        kind, payload = step[0], step[1]
        at = step[2] if len(step) > 2 else 0.0
        if kind == "say":
            at = payload[1] if isinstance(payload, tuple) else at
            text = payload if isinstance(payload, str) else payload[0]
            # allow ("say", "text", 21.0)
            if len(step) > 2:
                at = step[2]
            said.append(text)
            print(f'  >> patient ({at:.0f}s): "{text}"')
            session.hear(text, at_second=at)
        elif kind == "see":
            k, desc, at = payload
            print(f"  >> camera  ({at:.0f}s): {k} -- {desc}")
            session.observe_visual(k, desc, at_second=at)
        elif kind == "hear_obs":
            k, desc, at = payload
            print(f"  >> mic     ({at:.0f}s): {k} -- {desc}")
            session.observe_audio(k, desc, at_second=at)

    print(RULE)
    report(session, said)
    print()


def main(argv: Optional[List[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])

    if "--list" in args:
        for name, spec in SCENARIOS.items():
            print(f"  {name:<14} {spec['why']}")
        return 0

    names = [a for a in args if not a.startswith("-")] or list(SCENARIOS)
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown:
        print(f"unknown scenario(s): {', '.join(unknown)}")
        print(f"available: {', '.join(SCENARIOS)}")
        return 2

    print()
    print("PatientTriage.ai -- Phase 21 multimodal harness")
    print("Stages are labelled ANALYSED / MEASURED / NOT RUN so that a stage")
    print("which only collects numbers cannot be mistaken for one that reasons.")

    for name in names:
        run(name, SCENARIOS[name])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
