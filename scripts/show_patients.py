"""
scripts/show_patients.py
========================
Phase 2 verification. Loads all 24 synthetic patients, validates them, and
prints the roster.

Run from the repository root:
    python -m scripts.show_patients            # roster overview
    python -m scripts.show_patients P014       # one patient in full
    python -m scripts.show_patients --coverage # requirement coverage check
"""

import sys

from core.enums import CaptureStatus, HistoryTier, Tri
from core.patient_loader import load_patient, load_patients
from core.schema import VITAL_FIELDS


# Every behaviour the Round 2 brief requires us to demonstrate.
REQUIRED_COVERAGE = [
    ("Facial case A, acute change", "facial_case_A"),
    ("Facial case B, congenital", "facial_case_B"),
    ("Facial case C, scarring", "facial_case_C"),
    ("Facial case D, chronic stroke", "facial_case_D"),
    ("Facial case E, unknown baseline", "facial_case_E"),
    ("Zero history", "zero_history"),
    ("Rich history", "rich_history"),
    ("Partial history", "partial_history"),
    ("Ambiguous presentation", "ambiguous_presentation"),
    ("Age awareness", "age_awareness"),
    ("Geriatric", "geriatric"),
    ("Under-reporting patient", "denial_does_not_cancel_evidence"),
    ("Contradictory signals", "conflict_detection"),
    ("Deteriorates while waiting", "deterioration_while_waiting"),
    ("Stable while waiting, control", "stable_while_waiting"),
    ("Nurse override", "nurse_override"),
    ("Adaptive question changes risk", "adaptive_question"),
    ("Surge mode", "surge_mode"),
    ("Sensor failure, fail-safe", "fail_safe_degradation"),
    ("Missing vitals", "missing_vitals"),
]


def rule(title):
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)


def show_roster(patients):
    rule(f"PATIENT ROSTER  ({len(patients)} synthetic patients)")
    print(f"  {'ID':<6}{'age':>5}  {'band':<11}{'hist':<9}{'vit':>5}  scenario")
    print("  " + "-" * 70)
    for p in patients:
        completeness = f"{int(p.vitals.completeness() * 100)}%"
        label = p.scenario_label
        if len(label) > 38:
            label = label[:35] + "..."
        print(
            f"  {p.patient_id:<6}{p.age_years:>5}  {str(p.age_band):<11}"
            f"{str(p.history.tier):<9}{completeness:>5}  {label}"
        )


def show_facial_summary(patients):
    rule("FACIAL REASONING ACROSS THE WHOLE ROSTER")
    print("  Patients where asymmetry or droop IS observed:")
    print(f"\n  {'ID':<6}{'baseline condition':<22}{'acute change?':<15}cluster?")
    print("  " + "-" * 62)
    for p in patients:
        f = p.facial
        if not (f.asymmetry_observed.is_yes or f.droop_observed.is_yes):
            continue
        print(
            f"  {p.patient_id:<6}{str(f.baseline_condition):<22}"
            f"{str(f.acute_change()):<15}{f.has_stroke_cluster()}"
        )
    print()
    print("  Four patients present with an abnormal-looking face.")
    print("  Only ONE of them is an acute change. That is the whole argument.")


def show_missingness(patients):
    rule("DATA COMPLETENESS AND MISSING MODALITIES")
    print(f"  {'ID':<6}{'vitals':<9}{'missing vitals':<34}{'facial capture'}")
    print("  " + "-" * 70)
    for p in patients:
        missing = ", ".join(p.vitals.missing_fields()) or "none"
        if len(missing) > 32:
            missing = missing[:29] + "..."
        flag = "" if p.facial.capture_status.has_data else "  <-- degraded"
        print(
            f"  {p.patient_id:<6}{int(p.vitals.completeness()*100):>3}%     "
            f"{missing:<34}{str(p.facial.capture_status)}{flag}"
        )


def show_trajectories(patients):
    rule("DETERIORATION TRAJECTORIES  (Phase 10 will fire these)")
    any_found = False
    for p in patients:
        if not p.trajectory:
            continue
        any_found = True
        print(f"\n  {p.patient_id}  {p.scenario_label}")
        print(f"    t={p.arrival_minute:<4} arrival   "
              f"HR {p.vitals.heart_rate}  RR {p.vitals.respiratory_rate}  "
              f"SpO2 {p.vitals.spo2}")
        for u in p.trajectory:
            if u.vitals and u.vitals.heart_rate is not None:
                v = u.vitals
                print(f"    t={u.at_minute:<4} update    "
                      f"HR {v.heart_rate}  RR {v.respiratory_rate}  SpO2 {v.spo2}")
            else:
                print(f"    t={u.at_minute:<4} update    {u.note[:52]}")
            if u.new_symptoms:
                print(f"    {'':9}   new: {', '.join(u.new_symptoms)}")
    if not any_found:
        print("  none")


def show_coverage(patients):
    rule("ROUND 2 REQUIREMENT COVERAGE")
    all_tags = {tag for p in patients for tag in p.demonstrates}
    missing = []
    for label, tag in REQUIRED_COVERAGE:
        holders = [p.patient_id for p in patients if tag in p.demonstrates]
        if holders:
            print(f"  ok        {label:<36} {', '.join(holders)}")
        else:
            print(f"  MISSING   {label:<36} -")
            missing.append(label)
    print()
    print(f"  {len(REQUIRED_COVERAGE) - len(missing)} of {len(REQUIRED_COVERAGE)} "
          f"requirements have at least one patient.")
    print(f"  {len(all_tags)} distinct behaviours tagged across the roster.")
    return not missing


def show_one(patient):
    p = patient
    rule(f"{p.patient_id}  --  {p.scenario_label}")
    print(f"  {int(p.age_years) if p.age_years >= 1 else p.age_years} "
          f"year old {p.sex}, age band {p.age_band}")
    print(f"  arrived at minute {p.arrival_minute}")

    print(f"\n  COMPLAINT")
    print(f"    {p.self_report.chief_complaint}")
    print(f"    reports : {', '.join(p.self_report.symptoms) or 'none'}")
    print(f"    denies  : {', '.join(p.self_report.denies) or 'none'}")
    print(f"    pain    : {p.self_report.pain_score}")

    print(f"\n  VITALS  ({int(p.vitals.completeness()*100)}% complete)")
    for fname in VITAL_FIELDS:
        val = getattr(p.vitals, fname)
        print(f"    {fname:<22}{val if val is not None else 'NOT MEASURED'}")

    print(f"\n  FACIAL  (capture {p.facial.capture_status})")
    print(f"    asymmetry observed    {p.facial.asymmetry_observed}")
    print(f"    droop observed        {p.facial.droop_observed}")
    print(f"    baseline known        {p.facial.baseline_known}")
    print(f"    baseline condition    {p.facial.baseline_condition}")
    print(f"    reported as new       {p.facial.change_reported_as_new}")
    print(f"    -> ACUTE CHANGE       {p.facial.acute_change()}")
    print(f"    -> stroke cluster     {p.facial.has_stroke_cluster()}")

    print(f"\n  HISTORY  ({p.history.tier})")
    print(f"    conditions   {', '.join(p.history.conditions) or 'none on file'}")
    print(f"    medications  {', '.join(p.history.medications) or 'none on file'}")
    if p.history.baseline_notes:
        print(f"    baseline     {p.history.baseline_notes}")

    if p.trajectory:
        print(f"\n  TRAJECTORY")
        for u in p.trajectory:
            print(f"    t={u.at_minute}  {u.note}")

    print(f"\n  DEMONSTRATES")
    for tag in p.demonstrates:
        print(f"    - {tag}")
    print(f"\n  EXPECTED BEHAVIOUR")
    for line in _wrap(p.expected_behaviour, 68):
        print(f"    {line}")


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

    if args and args[0].startswith("P"):
        show_one(load_patient(args[0]))
        return

    patients = load_patients()

    if args and args[0] == "--coverage":
        complete = show_coverage(patients)
        print("\n  Roster is complete." if complete else "\n  Gaps remain.")
        return

    show_roster(patients)
    show_facial_summary(patients)
    show_missingness(patients)
    show_trajectories(patients)
    show_coverage(patients)

    rule("PHASE 2 RESULT")
    print(f"  {len(patients)} patients loaded and validated. No engine yet.")
    print("  ALL DATA IS SYNTHETIC. No real person or record is represented.")
    print("  Try:  python -m scripts.show_patients P014\n")


if __name__ == "__main__":
    main()
