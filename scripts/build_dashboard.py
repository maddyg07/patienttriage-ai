"""
scripts/build_dashboard.py
==========================
Phase 12. Generates the department board as a single self-contained HTML file.

Run from the repository root:
    python -m scripts.build_dashboard                    # simulated shift, medium_ed
    python -m scripts.build_dashboard --at 120           # the board at minute 120
    python -m scripts.build_dashboard --intake           # arrival state, no clock
    python -m scripts.build_dashboard --worked           # after a nurse works the board
    python -m scripts.build_dashboard --hospital small_ed
    python -m scripts.build_dashboard --out board.html

The output has no external references -- no CDN, no fonts, no scripts. Open it
by double-clicking, on any machine, offline.
"""

import sys
from pathlib import Path

from app.dashboard import render_html
from app.view_model import build_board, build_board_from_clock
from core.config import HospitalConfig
from core.patient_loader import load_patients
from core.questions import QuestionEngine
from core.ratchet import Ratchet, explain_history
from core.workflow import Workflow
from core.risk_engine import RiskEngine, explain
from core.safety_rules import explain_rules
from core.uncertainty import explain_confidence
from simulation.clock import SimulationClock

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "board.html"


def _arg(args, flag, default=None):
    if flag in args:
        return args[args.index(flag) + 1]
    return default


def main():
    args = sys.argv[1:]
    profile = _arg(args, "--hospital", "medium_ed")
    out = Path(_arg(args, "--out", str(DEFAULT_OUT)))
    at = _arg(args, "--at")
    intake = "--intake" in args
    worked = "--worked" in args

    engine = RiskEngine(HospitalConfig.load(profile))
    questions = QuestionEngine(engine)
    patients = load_patients()

    if intake:
        # Every patient scored on the state they arrived in. Useful as a
        # comparison, and NOT the default: it is exactly the snapshot view this
        # project exists to argue against, and P014 shows as WATCH on it.
        minute = int(at) if at else max(p.arrival_minute for p in patients)
        board = build_board(engine, patients, at_minute=minute,
                            ratchet=Ratchet(), question_engine=questions)
        source = f"intake state at minute {minute}"
    else:
        clock = SimulationClock(engine, patients)
        timeline = clock.run(until_minute=int(at) if at else None)
        workflow = None
        if worked:
            # A short worked example so the seen panel has something in it.
            # Deliberately behind a flag: the default board is the department
            # as the clock left it, with nobody having been to see anyone.
            workflow = Workflow(engine, ratchet=clock.ratchet)
            staged = build_board_from_clock(clock, timeline, questions,
                                            at_minute=int(at) if at else None)
            for card in staged.overdue()[:3]:
                workflow.mark_seen(card.patient, card.assessment,
                                   "RN-2210", staged.at_minute)
        board = build_board_from_clock(
            clock, timeline, questions, workflow=workflow,
            at_minute=int(at) if at else None)
        source = (f"simulated shift to minute {board.at_minute}, "
                  f"{len(timeline.records)} assessments")
        if worked:
            source += ", 3 patients seen by RN-2210"

    page = render_html(board, explain, explain_confidence, explain_rules,
                       explain_history)
    out.write_text(page, encoding="utf-8")

    counts = {b.word: n for b, n in board.band_counts().items()}
    print(f"wrote {out}  ({len(page) / 1024:.0f} KB, no external references)")
    print(f"  source     : {source}")
    print(f"  hospital   : {board.hospital.name}")
    print(f"  bands      : {counts}")
    print(f"  overdue    : {len(board.overdue())} past their care target")
    print(f"  seen       : {len(board.seen())} by a clinician")
    print(f"  questions  : {len(board.questions())} shown, "
          f"{board.questions_withheld()} withheld by the cap")


if __name__ == "__main__":
    main()
