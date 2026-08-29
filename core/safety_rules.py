"""
core/safety_rules.py
====================
The hard clinical rules, and the point in the pipeline where the scoring model
stops being the final authority.

THE ONE-WAY PROPERTY
--------------------
A safety rule sets a FLOOR on the band. It can raise. It cannot lower.

This is not a convention the rules agree to follow; there is no code path in
this file capable of lowering a band. `apply()` computes `max(score_band,
highest_floor)` and nothing else. A rule that wanted to say "this patient is
less sick than the score suggests" has no way to express it, which is the
correct expressive limit for an automated system. De-escalation belongs to a
nurse with a logged reason, and that arrives in Phase 8.

WHY RULES INSTEAD OF BETTER WEIGHTS
-----------------------------------
P011 has been sitting at L3 since Phase 3, and every phase since has explained
why without fixing it. He has an acute facial change, slurred speech and
one-sided weakness on a documented symmetric baseline. He scores 64. CODE
starts at 75.

The tempting fix is to raise the facial and speech weights until he crosses.
That fix is wrong, and it is worth being precise about why: those weights are
shared by every other patient. Tuning them to force one patient over one line
silently re-ranks the entire board to fix a single case, and the distortion is
invisible because the arithmetic still looks principled. It is overfitting with
extra steps.

The honest fix is to say what is actually true: this pattern is not a matter of
degree. Three time-critical findings appearing together is not "quite a lot of
points", it is a floor. So the weights stay exactly where they were, the score
stays 64, and a named rule raises the band with its own evidence trail.

Note what that costs us, deliberately: P011's score and P011's band now
disagree. The panel shows 64/100 and L4 CODE side by side. That looks wrong
until you understand it, and we would rather explain it than hide it, because
the alternative is a system whose score you cannot trust to mean what it says.

RESTRAINT IS PART OF THE DESIGN
-------------------------------
Every rule added here fires on patients nobody has thought about. A guard that
fires on most of the board has replaced the ranking engine with a lookup table.
So the suite is small, and `--rules` reports how many firings were BINDING --
how many actually moved a band, rather than agreeing with a score that had
already got there. A rule that never binds is dead weight; a guard where most
firings bind means the scorer is not doing its job. Both are visible.

SAFETY NOTE: every rule is a SIMULATED DEMONSTRATION PATTERN loaded from
data/safety_rules.json. None has been reviewed by a clinician and none is a
clinical protocol.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from core.age_rules import ANTICOAGULANTS, HEAD_INJURY_TERMS, thresholds_for
from core.enums import AgeBand, Consciousness, TriageBand, Tri
from core.facial import classify
from core.schema import Assessment, Patient

REPO_ROOT = Path(__file__).resolve().parent.parent
SAFETY_RULES_FILE = REPO_ROOT / "data" / "safety_rules.json"


def _load(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return {k: v for k, v in json.load(fh).items() if not k.startswith("_")}


@dataclass
class SafetyRule:
    rule_id: str
    name: str
    floor: TriageBand
    rationale: str
    enabled: bool = True


@dataclass
class RuleFiring:
    """
    One rule that matched, and what it actually achieved.

    `binding` is the honest field. A rule that fires on a patient the score had
    already placed at or above its floor has changed nothing, and reporting
    that separately is what stops the guard from taking credit for the engine's
    work.
    """

    rule: SafetyRule
    evidence: List[str] = field(default_factory=list)
    binding: bool = False

    def __str__(self) -> str:
        mark = "BINDING" if self.binding else "agrees with score"
        return f"{self.rule.rule_id} -> floor {self.rule.floor.word} ({mark})"


class SafetyGuard:
    """
    Stateless. Reads a patient and the assessment built for them, and returns
    the assessment with any floors applied.

    Runs AFTER the uncertainty engine, because one rule (R7) needs the
    confidence figure. That ordering is the only coupling between the two, and
    it runs in the safe direction: uncertainty informs escalation, and never
    the reverse.
    """

    def __init__(self, config: Optional[dict] = None):
        cfg = config or _load(SAFETY_RULES_FILE)
        self.thresholds = cfg["thresholds"]
        self.rules = {
            r["rule_id"]: SafetyRule(
                rule_id=r["rule_id"],
                name=r["name"],
                floor=TriageBand[r["floor"]],
                rationale=r["rationale"],
                enabled=r.get("enabled", True),
            )
            for r in cfg["rules"]
        }

    # -----------------------------------------------------------------------
    # Public entry point
    # -----------------------------------------------------------------------

    def apply(self, patient: Patient, assessment: Assessment,
              clinical_thresholds: dict) -> Assessment:
        score_band = assessment.proposed_band
        firings: List[RuleFiring] = []

        checks: List[Callable] = [
            self._r1_acute_neuro_cluster,
            self._r2_altered_consciousness,
            self._r3_critical_hypoxia,
            self._r4_shock_pattern,
            self._r5_infant_serious_signs,
            self._r6_anticoagulated_head_injury,
            self._r7_unresolved_finding_low_confidence,
            self._r8_denial_contradicted,
        ]
        for check in checks:
            firing = check(patient, assessment, clinical_thresholds)
            if firing is not None and firing.rule.enabled:
                firing.binding = firing.rule.floor > score_band
                firings.append(firing)

        assessment.rule_firings = firings
        assessment.safety_rules_fired.extend(str(f) for f in firings)

        if firings:
            highest = max(f.rule.floor for f in firings)
            assessment.band_floor = highest
            # THE ONE-WAY GATE. max() is the entire mechanism, and there is no
            # branch anywhere that can produce a band below score_band.
            assessment.proposed_band = max(score_band, highest)
            binding = [f for f in firings if f.binding]
            if binding:
                top = max(binding, key=lambda f: f.rule.floor)
                assessment.floor_reason = (
                    f"{top.rule.rule_id}: {top.rule.name}")

        assert assessment.proposed_band >= score_band, (
            "a safety rule lowered a band -- this must never be possible")
        return assessment

    # -----------------------------------------------------------------------
    # The rules
    # -----------------------------------------------------------------------

    def _fire(self, rule_id: str, evidence: List[str]) -> RuleFiring:
        return RuleFiring(self.rules[rule_id], evidence)

    def _r1_acute_neuro_cluster(self, patient, assessment, tables):
        """
        The rule P011 has been waiting four phases for.

        Deliberately reuses core/facial.py rather than re-reading the raw
        fields. The guard must agree with the facial module by construction: a
        rule that could fire on a patient the facial module considers chronic
        would re-introduce, in the safety layer, exactly the appearance-based
        escalation Phase 6 removed from the scorer.
        """
        verdict = classify(patient)
        if verdict.acute_change.is_yes and verdict.has_full_cluster:
            return self._fire("R1_acute_neuro_cluster", [
                "acute facial change confirmed against a documented baseline",
                "speech abnormality present",
                "one-sided weakness present",
                "all three appeared together",
            ])
        return None

    def _r2_altered_consciousness(self, patient, assessment, tables):
        level = patient.observed.consciousness
        if level in (Consciousness.UNRESPONSIVE, Consciousness.PAIN):
            return self._fire("R2_altered_consciousness",
                              [f"consciousness: {level}"])
        return None

    def _r3_critical_hypoxia(self, patient, assessment, tables):
        spo2 = patient.vitals.spo2
        if spo2 is None:
            return None
        limit = thresholds_for(patient.age_band, tables).get("spo2", {}).get(
            "critical_low")
        if limit is not None and spo2 < limit:
            return self._fire("R3_critical_hypoxia", [
                f"SpO2 {spo2:g}, below the {patient.age_band} critical "
                f"threshold of {limit:g}"])
        return None

    def _r4_shock_pattern(self, patient, assessment, tables):
        v = patient.vitals
        table = thresholds_for(patient.age_band, tables)
        limit = table.get("systolic_bp", {}).get("critical_low")
        evidence = []

        if v.systolic_bp is not None and limit is not None and v.systolic_bp < limit:
            evidence.append(
                f"systolic BP {v.systolic_bp:g}, below the {patient.age_band} "
                f"critical threshold of {limit:g}")

        combo = self.thresholds["hypotension_with_tachycardia"]
        if (v.systolic_bp is not None and v.heart_rate is not None
                and v.systolic_bp < combo["systolic_bp_below"]
                and v.heart_rate > combo["heart_rate_above"]):
            evidence.append(
                f"systolic BP {v.systolic_bp:g} with heart rate "
                f"{v.heart_rate:g}: neither is critical alone, the "
                f"combination is")

        return self._fire("R4_shock_pattern", evidence) if evidence else None

    def _r5_infant_serious_signs(self, patient, assessment, tables):
        if patient.age_band is not AgeBand.INFANT:
            return None
        symptoms = " ".join(patient.self_report.symptoms
                            + [patient.self_report.chief_complaint]).lower()
        serious = (
            "poor feeding" in symptoms or "not feeding" in symptoms
            or "lethargy" in symptoms or "floppy" in symptoms
            or patient.observed.consciousness not in
            (Consciousness.ALERT, Consciousness.UNKNOWN))
        if not serious:
            return None

        table = thresholds_for(patient.age_band, tables)
        deviations = []
        for name in ("heart_rate", "respiratory_rate", "spo2", "temperature_c"):
            value = getattr(patient.vitals, name)
            limits = table.get(name, {})
            if value is None or not limits:
                continue
            lo, hi = limits.get("low"), limits.get("high")
            if (lo is not None and value < lo) or (hi is not None and value > hi):
                deviations.append(f"{name} {value:g}")
        if not deviations:
            return None
        return self._fire("R5_infant_serious_signs", [
            "infant with reduced feeding or responsiveness",
            f"alongside: {', '.join(deviations)}"])

    def _r6_anticoagulated_head_injury(self, patient, assessment, tables):
        meds = " ".join(patient.history.medications).lower()
        symptoms = " ".join(patient.self_report.symptoms
                            + [patient.self_report.chief_complaint]).lower()
        drug = next((d for d in ANTICOAGULANTS if d in meds), None)
        if drug and any(t in symptoms for t in HEAD_INJURY_TERMS):
            return self._fire("R6_anticoagulated_head_injury", [
                f"anticoagulated ({drug})",
                "head strike reported",
                "delayed bleeding may show no abnormal vital for hours"])
        return None

    def _r7_unresolved_finding_low_confidence(self, patient, assessment, tables):
        """
        The rule that stops 'we cannot tell' from resolving into 'nothing here'.

        Requires BOTH halves. Thin information on its own is not a reason to
        escalate anybody -- that would escalate hardest on the least documented
        patients, which Phase 6 spent a whole module refusing to do. What makes
        this different is that there is a specific concerning finding sitting
        unresolved, and the reason it is unresolved is that we do not have
        enough to resolve it.
        """
        limit = float(self.thresholds["low_confidence"])
        if assessment.confidence >= limit:
            return None

        unresolved = []
        verdict = classify(patient)
        if verdict.looks_abnormal and verdict.acute_change is Tri.UNKNOWN:
            unresolved.append(
                "facial asymmetry that we cannot classify as acute or lifelong")
        if patient.voice.slurred_speech.is_yes and verdict.acute_change is Tri.UNKNOWN:
            unresolved.append("slurred speech with no baseline to compare against")
        if not unresolved:
            return None

        driver = assessment.quality.dominant_driver() if assessment.quality else None
        gap = f"biggest gap: {driver.name} at {driver.quality_pct}%" if driver else ""
        return self._fire("R7_unresolved_finding_low_confidence",
                          unresolved + [
                              f"confidence {assessment.confidence_pct}%, "
                              f"below the {limit:.0%} threshold",
                              gap] if gap else unresolved)

    def _r8_denial_contradicted(self, patient, assessment, tables):
        conflicts = [c for c in assessment.contributions
                     if c.label.startswith("CONFLICT")]
        if not conflicts:
            return None
        return self._fire("R8_denial_contradicted",
                          [c.label.replace("CONFLICT: ", "") for c in conflicts])


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def explain_rules(assessment: Assessment) -> str:
    """The safety panel: which rules fired, on what evidence, and what changed."""
    firings = assessment.rule_firings
    if not firings:
        return "    no safety rule fired -- the band is the score's own answer"

    lines = []
    for f in firings:
        head = "BINDING" if f.binding else "fired, agrees with score"
        lines.append(f"    [{f.rule.rule_id}]  floor {f.rule.floor}   ({head})")
        lines.append(f"      {f.rule.name}")
        for e in f.evidence:
            lines.append(f"        - {e}")
    lines.append("    " + "-" * 58)
    if assessment.band_floor:
        lines.append(f"    highest floor {assessment.band_floor}   "
                     f"band {assessment.proposed_band}")
    if assessment.floor_reason:
        lines.append(f"    band raised by {assessment.floor_reason}")
    return "\n".join(lines)
