"""
scripts/run_triage.py
=====================
Phase 3 verification. Scores all 24 synthetic patients and prints a ranked
queue plus explanation panels.

Run from the repository root:
    python -m scripts.run_triage               # ranked queue
    python -m scripts.run_triage P002          # one patient, full explanation
    python -m scripts.run_triage --facial      # the five facial patients side by side
    python -m scripts.run_triage --age-problem # what Phase 4 has to fix
    python -m scripts.run_triage --hospital small_ed
"""

import sys

from core.config import HospitalConfig
from core.patient_loader import load_patient, load_patients, patients_demonstrating
from core.risk_engine import RiskEngine, explain


def rule(title):
    print("\n" + "=" * 76)
    print(title)
    print("=" * 76)


def build_engine(profile="medium_ed"):
    return RiskEngine(HospitalConfig.load(profile))


def show_queue(engine, patients):
    rule(f"TRIAGE QUEUE  --  {engine.hospital.name}")
    scored = [(p, engine.assess(p)) for p in patients]
    scored.sort(key=lambda pair: -pair[1].risk_score)

    print(f"  {'rank':<6}{'ID':<7}{'age':>4}  {'band':<11}{'risk':>5}   top driver")
    print("  " + "-" * 72)
    for i, (p, a) in enumerate(scored, 1):
        drivers = [c for c in a.contributions if c.points > 0]
        top = drivers[0].label if drivers else "nothing abnormal detected"
        if len(top) > 38:
            top = top[:35] + "..."
        marker = " *" if a.proposed_band.value >= 3 else "  "
        print(f" {marker}{i:<4}{p.patient_id:<7}{int(p.age_years):>4}  "
              f"{str(a.proposed_band):<11}{a.risk_score:>5.0f}   {top}")

    print()
    counts = {}
    for _, a in scored:
        counts[a.proposed_band] = counts.get(a.proposed_band, 0) + 1
    summary = "  ".join(
        f"{b.word} {counts.get(b, 0)}" for b in sorted(counts, reverse=True))
    print(f"  band distribution: {summary}")
    return scored


def show_patient(engine, patient):
    a = engine.assess(patient)
    rule(f"{patient.patient_id}  --  {patient.scenario_label}")
    print(f"  {int(patient.age_years)} year old {patient.sex}, "
          f"age band {patient.age_band}, history {patient.history.tier}")
    print(f"  complaint: {patient.self_report.chief_complaint}")
    print("\n  WHY THIS SCORE")
    print(explain(a))
    print("\n  EXPECTED BEHAVIOUR (authored in Phase 2)")
    for line in _wrap(patient.expected_behaviour, 70):
        print(f"    {line}")


def show_facial_comparison(engine):
    rule("THE FIVE FACIAL PATIENTS, SCORED SIDE BY SIDE")
    ids = ["P011", "P012", "P013", "P015", "P016"]
    print(f"  {'ID':<6}{'baseline':<16}{'acute?':<10}{'facial pts':>11}"
          f"{'total':>7}  band")
    print("  " + "-" * 72)
    for pid in ids:
        p = load_patient(pid)
        a = engine.assess(p)
        facial_pts = sum(c.points for c in a.contributions if c.source == "facial")
        print(f"  {p.patient_id:<6}{str(p.facial.baseline_condition):<16}"
              f"{str(p.facial.acute_change()):<10}{facial_pts:>11.0f}"
              f"{a.risk_score:>7.0f}  {a.proposed_band}")

    print()
    print("  All five have an abnormal-looking face. Only P011 earns facial points.")
    print("  P012, P013 and P015 score ZERO from the face because their")
    print("  asymmetry is chronic and documented. P016 scores zero because we")
    print("  cannot tell -- which Phase 5 turns into low confidence, not into")
    print("  false reassurance.")


def show_age_problem(engine):
    rule("THE PROBLEM PHASE 4 EXISTS TO FIX")
    print("  Phase 3 applies ADULT thresholds to every patient, including")
    print("  children. Here is what that costs us:\n")
    for pid in ["P004", "P005", "P002"]:
        p = load_patient(pid)
        a = engine.assess(p)
        hr_lines = [c for c in a.contributions if c.label.startswith("heart_rate")]
        note = hr_lines[0].label if hr_lines else "heart rate within adult range"
        print(f"  {p.patient_id}  {int(p.age_years) if p.age_years >= 1 else p.age_years:>4} "
              f"yr  {str(p.age_band):<11}HR {p.vitals.heart_rate:<6.0f} -> {note}")

    print()
    print("  P004 is a six-year-old with a fever. A heart rate of 122 is")
    print("  UNREMARKABLE at that age. Our adult table calls it a deviation and")
    print("  charges her points for being a child.")
    print()
    print("  P005 is an eight-month-old. A heart rate of 168 is within normal")
    print("  range for an infant. The adult table calls it severe.")
    print()
    print("  Neither is a bug in the code. Both are the documented consequence")
    print("  of a single adult-calibrated table, which is exactly why Round 2")
    print("  makes age-aware triage mandatory. Phase 4 replaces one method:")
    print("  RiskEngine._threshold_band().")


def _wrap(text, width):
    words, line, out = text.split(), "", []
    for w in words:
        if len(line) + len(w) + 1 > width:
            out.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        out.append(line)
    return out


def main():
    args = sys.argv[1:]
    profile = "medium_ed"
    if "--hospital" in args:
        profile = args[args.index("--hospital") + 1]
        args = [a for a in args if a != "--hospital" and a != profile]

    engine = build_engine(profile)

    if args and args[0].startswith("P"):
        show_patient(engine, load_patient(args[0]))
        return
    if args and args[0] == "--facial":
        show_facial_comparison(engine)
        return
    if args and args[0] == "--age-problem":
        show_age_problem(engine)
        return

    patients = load_patients()
    show_queue(engine, patients)
    show_facial_comparison(engine)
    show_age_problem(engine)

    rule("PHASE 3 RESULT, AND TWO THINGS IT GETS WRONG")
    print("  Every patient now has a score, a proposed band, and a full")
    print("  contribution trace. Two known gaps, both left visible on purpose:")
    print()
    print("  1. P011, the acute stroke, lands at L3 rather than L4. The")
    print("     neurological domain cap that stops correlated signals from")
    print("     double-counting also stops a genuine emergency from reaching")
    print("     CODE on score alone. The fix is NOT to inflate the weights")
    print("     until it happens to work. It is a hard clinical rule in")
    print("     Phase 7 that floors an acute stroke cluster at L4 regardless")
    print("     of score. That is the architecture working as designed: the")
    print("     scoring model is not the final authority.")
    print()
    print("  2. P004 and P005 are scored against ADULT thresholds. Phase 4.")
    print()
    print("  Still missing: age awareness (4), confidence (5), safety rules")
    print("  (7), the ratchet (8). The band above is a PROPOSAL, not a")
    print("  decision. ALL VALUES ARE SIMULATED and clinically unvalidated.\n")


if __name__ == "__main__":
    main()
