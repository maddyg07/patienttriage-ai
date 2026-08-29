"""
core/facial.py
==============
The facial signal module, and the fairness argument of this project expressed
as code rather than as a paragraph.

THE ONE QUESTION THIS MODULE ASKS
---------------------------------
    Has this face CHANGED?

It never asks whether a face is normal, typical, symmetric or unremarkable. It
has no concept of a correct face. That is not a stylistic choice, it is the
whole design: a system that scores appearance will, with total consistency,
penalise people for congenital differences, for burn scarring, for old strokes,
for surgery. Those patients arrive at emergency departments more often than
average, not less, and they are exactly the population an appearance-based
triage model would systematically over-flag.

So the module is built around a comparison, and where it has nothing to compare
against, it says UNKNOWN and stops. UNKNOWN is a real answer here. It is never
quietly rounded to NO.

WHAT PHASE 6 ADDS OVER THE PHASE 1 SEED
---------------------------------------
`FacialSignals.acute_change()` was a seed method living in the schema. It
worked, and every branch of it survives below. Phase 6 promotes it to a module
and adds the three things a seed could not carry:

  1. BASELINE PROVENANCE. Not just "do we have a baseline" but "where did it
     come from". A face documented across three prior visits is a stronger
     claim than the patient's own recollection, which still beats nothing.

  2. A DECISION PATH. Every verdict records the ladder it climbed, in order, in
     plain English. The nurse sees WHICH branch fired and why, so they can
     disagree with a specific step instead of with a black box.

  3. A FAIRNESS COUNTERFACTUAL. `fairness_counterfactual()` re-runs a patient
     against every possible baseline condition and checks the facial points
     never move. Documented asymmetry scores zero whether it came from birth,
     an acid attack, surgery or an old stroke. That is asserted, not asserted-
     to-be-true-in-a-comment.

THE ASYMMETRY THAT MATTERS MOST
-------------------------------
A weak baseline lowers CONFIDENCE. It never raises the SCORE.

That direction is deliberate and it is worth defending out loud. The obvious
alternative -- "we cannot verify this patient's baseline, so treat the finding
as possibly acute and escalate" -- sounds cautious and is quietly discriminatory:
it would escalate hardest on undocumented patients, who are disproportionately
people without regular care, without records, without an accompanying relative.
The correct response to a missing baseline is to say we do not know, loudly,
and to go and find out (Phase 11), not to convert ignorance into points.

SAFETY NOTE: every value is a SIMULATED DEMONSTRATION VALUE loaded from
data/. Nothing here is clinically validated. This module does not diagnose
anything and does not use diagnostic language in its findings.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from core.enums import CaptureStatus, FacialBaselineCondition, HistoryTier, Tri
from core.schema import Contribution, FacialSignals, Patient

REPO_ROOT = Path(__file__).resolve().parent.parent
FACIAL_CONFIG_FILE = REPO_ROOT / "data" / "facial_config.json"


def _load(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return {k: v for k, v in json.load(fh).items() if not k.startswith("_")}


CONFIG = _load(FACIAL_CONFIG_FILE)

# Provenance keys, ordered strongest first. Used for lookup and for display.
DOCUMENTED_RECORD = "documented_record"
PRIOR_ENCOUNTER = "prior_encounter"
COLLATERAL_REPORT = "collateral_report"
PATIENT_REPORT = "patient_report"
NO_BASELINE = "none"


# ---------------------------------------------------------------------------
# Baseline provenance
# ---------------------------------------------------------------------------

@dataclass
class Baseline:
    """
    What we know about this patient's normal appearance, and how we know it.

    `reliability` feeds Phase 5 confidence only. Nothing in this dataclass is
    permitted to change a score.
    """

    source: str
    reliability: float
    label: str
    asymmetry_expected: Tri = Tri.UNKNOWN
    condition: FacialBaselineCondition = FacialBaselineCondition.UNKNOWN
    notes: str = ""

    @property
    def is_usable(self) -> bool:
        return self.source != NO_BASELINE

    def __str__(self) -> str:
        return f"{self.label} (reliability {self.reliability:.0%})"


def resolve_baseline(patient: Patient) -> Baseline:
    """
    Work out where our picture of this patient's normal face came from.

    We derive provenance rather than asking the intake form for it, because in
    a real department nobody types "documented_record" into a field. What
    actually exists is a record tier and a set of baseline notes, and those
    imply the strength of the claim.

    The ladder, strongest first:
      * A rich record with documented baseline notes  -> documented_record
      * A rich or partial record, baseline asserted   -> prior_encounter
      * Baseline asserted with a thin record, patient
        able to speak for themselves                  -> patient_report
      * Baseline asserted, patient cannot communicate
        (so somebody else told us)                    -> collateral_report
      * Nothing asserted at all                       -> none
    """
    prov = CONFIG["baseline_provenance"]
    f = patient.facial
    h = patient.history

    def build(key: str) -> Baseline:
        spec = prov[key]
        return Baseline(
            source=key,
            reliability=float(spec["reliability"]),
            label=spec["label"],
            asymmetry_expected=f.baseline_asymmetry_present,
            condition=f.baseline_condition,
            notes=h.baseline_notes,
        )

    if not f.baseline_known.is_yes:
        return build(NO_BASELINE)

    documented = h.tier is HistoryTier.RICH and bool(h.baseline_notes.strip())
    if documented:
        return build(DOCUMENTED_RECORD)

    if h.tier.is_available:
        return build(PRIOR_ENCOUNTER)

    # Baseline claimed but no record behind it: somebody told us. Who?
    if patient.self_report.can_communicate.is_no:
        return build(COLLATERAL_REPORT)
    return build(PATIENT_REPORT)


# ---------------------------------------------------------------------------
# The verdict
# ---------------------------------------------------------------------------

@dataclass
class FacialVerdict:
    """
    The complete facial finding: what we concluded, and the ladder we climbed
    to get there.

    `decision_path` is not logging. It is the output. A nurse who disagrees
    should be able to point at one line and say "that step is wrong", which is
    a conversation you cannot have with a probability.
    """

    acute_change: Tri = Tri.UNKNOWN
    looks_abnormal: bool = False
    baseline: Optional[Baseline] = None
    cluster_components: List[str] = field(default_factory=list)
    decision_path: List[str] = field(default_factory=list)
    finding: str = ""
    weight_key: Optional[str] = None

    @property
    def has_full_cluster(self) -> bool:
        return len(self.cluster_components) >= len(CONFIG["cluster"]["components"])

    @property
    def has_partial_cluster(self) -> bool:
        return (len(self.cluster_components)
                >= int(CONFIG["cluster"]["partial_cluster_min_components"]))


def classify(patient_or_signals) -> FacialVerdict:
    """
    Run the ladder. Accepts a Patient (so provenance can be resolved) or bare
    FacialSignals (so the Phase 1 schema seed and the check_setup harness keep
    working unchanged).

    Read the branches in order. The camera saw the same thing in P011, P012,
    P013, P015 and P016 -- an asymmetric face. Everything that separates them
    happens below, and none of it is about how the face looks.
    """
    if isinstance(patient_or_signals, FacialSignals):
        f, baseline = patient_or_signals, None
    else:
        f = patient_or_signals.facial
        baseline = resolve_baseline(patient_or_signals)

    v = FacialVerdict(baseline=baseline)
    path = v.decision_path

    # --- Step 0: did we get an image at all? ---
    if not f.capture_status.has_data:
        path.append(f"no usable capture ({f.capture_status}) -> cannot compare")
        v.acute_change = Tri.UNKNOWN
        v.finding = f"facial signal unavailable ({f.capture_status})"
        v.weight_key = None
        return v

    v.looks_abnormal = f.asymmetry_observed.is_yes or f.droop_observed.is_yes

    # --- Step 1: is there anything to explain? ---
    if f.asymmetry_observed.is_no and f.droop_observed.is_no:
        path.append("no asymmetry and no droop observed")
        v.acute_change = Tri.NO
        v.finding = ""
        return v

    if not v.looks_abnormal:
        # Observed fields are UNKNOWN rather than NO. Nothing seen, nothing
        # ruled out either.
        path.append("appearance not established (findings unknown)")
        v.acute_change = Tri.UNKNOWN
        v.finding = ""
        return v

    seen = "droop and asymmetry" if (f.asymmetry_observed.is_yes
                                     and f.droop_observed.is_yes) else "asymmetry"
    path.append(f"{seen} observed -> is it NEW?")

    # --- Step 2: do we have a baseline to compare against? ---
    if not f.baseline_known.is_yes:
        path.append("no baseline on record: cannot tell new from lifelong")
        path.append("refusing to guess in either direction")
        v.acute_change = Tri.UNKNOWN
        v.weight_key = "unknown_baseline"
        v.finding = ("facial asymmetry with UNKNOWN baseline, "
                     "cannot tell if acute")
        return v

    if baseline is not None:
        path.append(f"baseline available: {baseline}")

    # --- Step 3: does the baseline already contain this appearance? ---
    if f.baseline_asymmetry_present.is_yes:
        condition = f.baseline_condition
        path.append(f"baseline already shows asymmetry ({condition})")

        if f.change_reported_as_new.is_yes:
            path.append("BUT the change is reported as new -> acute on chronic")
            v.acute_change = Tri.YES
        elif f.change_reported_as_new.is_no:
            path.append("and it is reported unchanged -> chronic, not acute")
            v.acute_change = Tri.NO
            v.weight_key = "chronic_baseline_explains_it"
            v.finding = (f"facial asymmetry present but CHRONIC "
                         f"({condition}), not an acute finding")
            return v
        else:
            path.append("nobody can say whether it changed -> unknown")
            v.acute_change = Tri.UNKNOWN
            v.weight_key = "unknown_baseline"
            v.finding = ("known chronic asymmetry, but unable to confirm "
                         "whether it has changed")
            return v

    elif f.baseline_asymmetry_present.is_no:
        path.append("baseline documented SYMMETRIC -> this finding is new")
        v.acute_change = Tri.YES

    else:
        path.append("baseline exists but does not record the face -> unknown")
        v.acute_change = Tri.UNKNOWN
        v.weight_key = "unknown_baseline"
        v.finding = "facial asymmetry with UNKNOWN baseline, cannot tell if acute"
        return v

    # --- Step 4: acute change confirmed. What came with it? ---
    v.cluster_components = _cluster_components(f)
    if v.has_full_cluster:
        path.append("acute change with speech abnormality AND one-sided "
                    "weakness: all three appeared together")
        v.weight_key = "acute_change_with_stroke_cluster"
        v.finding = "ACUTE facial change with speech and one-sided weakness"
    else:
        others = [c for c in v.cluster_components if c != "facial_change"]
        extra = f" (also: {', '.join(others)})" if others else " in isolation"
        path.append(f"acute change{extra}")
        v.weight_key = "acute_change_alone"
        v.finding = "ACUTE facial change from known baseline"
    return v


def _cluster_components(f: FacialSignals) -> List[str]:
    """
    Which components of the named pattern are present.

    Deliberately NOT called a diagnosis and deliberately not named after one in
    the output. We report that three findings appeared together. Phase 7 acts
    on that combination with a hard floor. Naming a condition here would be the
    module claiming something it has no business claiming.
    """
    present = []
    if f.asymmetry_observed.is_yes or f.droop_observed.is_yes:
        present.append("facial_change")
    if f.speech_abnormality.is_yes:
        present.append("speech_abnormality")
    if f.unilateral_weakness.is_yes:
        present.append("unilateral_weakness")
    return present


def acute_change(signals: FacialSignals) -> Tri:
    """
    The Phase 1 seed's contract, preserved exactly. `FacialSignals.acute_change`
    delegates here, so there is now one implementation and the schema keeps its
    convenient method.
    """
    return classify(signals).acute_change


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_facial(patient: Patient, weights: dict) -> List[Contribution]:
    """
    Turn a verdict into contribution lines. Called by RiskEngine.

    Note what determines the points: `verdict.weight_key`, which is set by the
    CHANGE branch that fired. Appearance never selects a weight key. There is
    no path through this function where how a face looks earns points on its
    own.
    """
    w = weights["facial"]
    verdict = classify(patient)

    if verdict.weight_key is None:
        # Either nothing abnormal, or no capture at all. Both score zero; only
        # the second one is worth a visible line.
        if not patient.facial.capture_status.has_data:
            return [Contribution(verdict.finding, 0.0, "facial", "neurological")]
        return []

    spec = w[verdict.weight_key]
    return [Contribution(verdict.finding, float(spec["points"]),
                         "facial", spec["domain"])]


# ---------------------------------------------------------------------------
# The fairness test, as executable code
# ---------------------------------------------------------------------------

@dataclass
class CounterfactualResult:
    patient_id: str
    points_by_condition: dict
    verdicts_by_condition: dict

    @property
    def is_fair(self) -> bool:
        """True when the cause of a documented difference changes nothing."""
        return len(set(self.points_by_condition.values())) == 1

    @property
    def points(self) -> float:
        return next(iter(self.points_by_condition.values()))


def fairness_counterfactual(patient: Patient, weights: dict) -> CounterfactualResult:
    """
    Re-score one patient once per possible baseline condition and compare.

    This is the claim "we do not penalise people for how their face looks",
    converted into something that can fail. If a burn survivor scored one point
    more than a congenital-asymmetry patient with identical findings, this
    returns is_fair = False and the Phase 15 suite goes red.

    Only `baseline_condition` is varied. Every observation, every vital, every
    reported symptom stays fixed, so any difference in points could only have
    come from the cause of the difference -- which is precisely the thing that
    must not matter.
    """
    import copy

    points, verdicts = {}, {}
    for name in CONFIG["fairness"]["counterfactual_conditions"]:
        variant = copy.deepcopy(patient)
        variant.facial.baseline_condition = FacialBaselineCondition(name)
        contribs = score_facial(variant, weights)
        points[name] = sum(c.points for c in contribs)
        verdicts[name] = classify(variant).acute_change
    return CounterfactualResult(patient.patient_id, points, verdicts)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def explain_facial(patient: Patient) -> str:
    """The facial reasoning panel: the ladder, step by step, in order."""
    verdict = classify(patient)
    lines = [f"    capture      : {patient.facial.capture_status}",
             f"    appearance   : "
             f"{'abnormal' if verdict.looks_abnormal else 'nothing observed'}"]
    if verdict.baseline:
        lines.append(f"    baseline     : {verdict.baseline}")
        if verdict.baseline.notes:
            note = verdict.baseline.notes
            lines.append(f"                   \"{note[:64]}...\"" if len(note) > 64
                         else f"                   \"{note}\"")
    lines.append(f"    ACUTE CHANGE : {verdict.acute_change}")
    lines.append("    " + "-" * 58)
    lines.append("    how we got there:")
    for i, step in enumerate(verdict.decision_path, 1):
        lines.append(f"      {i}. {step}")
    if verdict.cluster_components:
        lines.append(f"    pattern      : "
                     f"{' + '.join(verdict.cluster_components)}")
    return "\n".join(lines)
