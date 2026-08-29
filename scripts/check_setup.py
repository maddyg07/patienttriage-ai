"""
scripts/check_setup.py
======================
Phase 1 verification. Run this to prove the foundation works before we build
anything on top of it.

It checks four things:
  1. All three hospital profiles load from JSON.
  2. Band ordering is correct (the Ratchet Engine depends entirely on this).
  3. Age banding works.
  4. The five facial edge cases from the Round 2 brief are REPRESENTABLE, and
     the seed logic already tells acute change apart from chronic appearance.

Run from the repository root:
    python -m scripts.check_setup
"""

from core.config import HospitalConfig
from core.enums import (
    AgeBand,
    CaptureStatus,
    FacialBaselineCondition,
    TriageBand,
    Tri,
)
from core.schema import FacialSignals


def rule(title: str) -> None:
    print("\n" + "=" * 66)
    print(title)
    print("=" * 66)


# ---------------------------------------------------------------------------
# 1. Hospital configs
# ---------------------------------------------------------------------------

rule("1. HOSPITAL PROFILES")
for profile_id in HospitalConfig.list_profiles():
    print()
    print(HospitalConfig.load(profile_id).describe())

cfg = HospitalConfig.load("medium_ed")

# ---------------------------------------------------------------------------
# 2. Band ordering + score mapping
# ---------------------------------------------------------------------------

rule("2. TRIAGE BANDS  (L4 = most urgent)")
for band in TriageBand:
    print(f"  {band.code}  {band.word:<6} rank {band.value}  -- {band.meaning}")

print("\n  Ordering check (the Ratchet depends on this):")
print(f"    L3_PULL > L2_LOOK   -> {TriageBand.L3_PULL > TriageBand.L2_LOOK}")
print(f"    L4_CODE > L3_PULL   -> {TriageBand.L4_CODE > TriageBand.L3_PULL}")
print(f"    L1_WATCH > L4_CODE  -> {TriageBand.L1_WATCH > TriageBand.L4_CODE}")

print("\n  Score -> band  (medium_ed thresholds):")
for score in (12, 25, 49, 50, 74, 75, 92):
    band = cfg.thresholds.band_for_score(score)
    gap = cfg.thresholds.distance_to_next_band(score)
    print(f"    score {score:>3}  ->  {band}   ({gap:.0f} pts below next band)")

# ---------------------------------------------------------------------------
# 3. Age bands
# ---------------------------------------------------------------------------

rule("3. AGE BANDING")
for age in (0.5, 4, 14, 34, 71):
    band = AgeBand.from_age(age)
    tag = "pediatric" if band.is_pediatric else "not pediatric"
    print(f"    age {age:>4}  ->  {str(band):<12} ({tag})")

# ---------------------------------------------------------------------------
# 4. The facial edge cases  --  the fairness core
# ---------------------------------------------------------------------------

rule("4. FACIAL EDGE CASES  (Round 2 brief, cases A-E)")

case_a = FacialSignals(
    capture_status=CaptureStatus.OK,
    asymmetry_observed=Tri.YES,
    droop_observed=Tri.YES,
    baseline_known=Tri.YES,
    baseline_asymmetry_present=Tri.NO,
    baseline_condition=FacialBaselineCondition.NONE,
    change_reported_as_new=Tri.YES,
    speech_abnormality=Tri.YES,
    unilateral_weakness=Tri.YES,
)

case_b = FacialSignals(
    capture_status=CaptureStatus.OK,
    asymmetry_observed=Tri.YES,
    droop_observed=Tri.NO,
    baseline_known=Tri.YES,
    baseline_asymmetry_present=Tri.YES,
    baseline_condition=FacialBaselineCondition.CONGENITAL,
    change_reported_as_new=Tri.NO,
    speech_abnormality=Tri.NO,
    unilateral_weakness=Tri.NO,
)

case_c = FacialSignals(
    capture_status=CaptureStatus.OK,
    asymmetry_observed=Tri.YES,
    droop_observed=Tri.NO,
    baseline_known=Tri.YES,
    baseline_asymmetry_present=Tri.YES,
    baseline_condition=FacialBaselineCondition.BURN_OR_ACID_INJURY,
    change_reported_as_new=Tri.NO,
    speech_abnormality=Tri.NO,
    unilateral_weakness=Tri.NO,
)

case_d = FacialSignals(
    capture_status=CaptureStatus.OK,
    asymmetry_observed=Tri.YES,
    droop_observed=Tri.YES,
    baseline_known=Tri.YES,
    baseline_asymmetry_present=Tri.YES,
    baseline_condition=FacialBaselineCondition.POST_STROKE,
    change_reported_as_new=Tri.NO,
    speech_abnormality=Tri.NO,
    unilateral_weakness=Tri.NO,
)

case_e = FacialSignals(
    capture_status=CaptureStatus.OK,
    asymmetry_observed=Tri.YES,
    droop_observed=Tri.YES,
    baseline_known=Tri.UNKNOWN,
    baseline_asymmetry_present=Tri.UNKNOWN,
    baseline_condition=FacialBaselineCondition.UNKNOWN,
    change_reported_as_new=Tri.UNKNOWN,
    speech_abnormality=Tri.UNKNOWN,
    unilateral_weakness=Tri.UNKNOWN,
)

case_f = FacialSignals(capture_status=CaptureStatus.FAILED)

cases = [
    ("A  acute droop, symmetric baseline", case_a, Tri.YES),
    ("B  congenital asymmetry, stable", case_b, Tri.NO),
    ("C  acid-attack scarring, stable", case_c, Tri.NO),
    ("D  chronic post-stroke weakness", case_d, Tri.NO),
    ("E  zero history, baseline unknown", case_e, Tri.UNKNOWN),
    ("F  camera failed entirely", case_f, Tri.UNKNOWN),
]

print(f"  {'case':<38} {'acute change?':<15} {'stroke cluster?'}")
print("  " + "-" * 62)
all_ok = True
for label, signals, expected in cases:
    result = signals.acute_change()
    ok = result is expected
    all_ok = all_ok and ok
    mark = "ok" if ok else "MISMATCH"
    print(
        f"  {label:<38} {str(result):<15} "
        f"{str(signals.has_stroke_cluster()):<8} {mark}"
    )

print()
print("  Read cases B, C and D carefully: asymmetry IS observed, yet acute")
print("  change is NO. That is the entire fairness argument, in data.")
print("  Cases E and F return UNKNOWN, never NO -- absence of evidence is")
print("  never treated as evidence of absence.")

rule("PHASE 1 RESULT")
print("  Foundation is sound." if all_ok else "  SOMETHING IS WRONG -- see MISMATCH above.")
print("  All values above are SIMULATED DEMONSTRATION DATA.")
print("  Nothing in this repository is clinically validated.\n")
