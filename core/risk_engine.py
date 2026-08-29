"""
core/risk_engine.py
===================
Turns a Patient into a risk score between 0 and 100, plus a full line-by-line
account of how that score was reached.

THE CENTRAL DESIGN CHOICE
-------------------------
The score is not computed and then explained. The score is computed BY building
the explanation. Every scorer appends Contribution objects, and the total is
exactly their sum:

    +22  spo2 89 (low, severe)                        [respiratory]
    +18  reports: chest pain                          [circulatory]
    +14  cannot complete a full sentence              [respiratory]
    -21  respiratory domain capped at 45              [respiratory]
    ----
     51  ->  L3 PULL

There is no separate explainability module and no post-hoc attribution, so the
explanation cannot drift from what actually happened.

WHY DOMAIN CAPS EXIST
---------------------
Naive summation double-counts. A breathless patient trips a low SpO2, a raised
respiratory rate, a 'breathlessness' symptom, a 'wheeze' symptom, an inability
to finish a sentence, and audible breathlessness between words: six signals for
ONE clinical problem. Summed linearly, a moderate asthma attack outscores a
cardiac arrest. We found exactly that on the first run of this engine.

So signals are grouped into clinical DOMAINS and each domain is capped. When a
cap bites it is recorded as its own visible line, which keeps the score exactly
equal to the sum of the panel.

WHAT THIS FILE DELIBERATELY DOES NOT DO
---------------------------------------
  * No age-specific thresholds yet -- Phase 4.
  * No uncertainty or confidence -- Phase 5.
  * No hard clinical safety rules -- Phase 7. This matters: the scorer is not
    meant to catch everything. Some patterns, such as an acute stroke cluster,
    should be floored at L4 by a RULE regardless of score, precisely because a
    scoring model must never be the final authority.
  * No ratchet -- Phase 8.

The band produced here is therefore `proposed_band`, never `final_band`.

SAFETY NOTE: every threshold and weight is a SIMULATED DEMONSTRATION VALUE
loaded from data/. Nothing here is clinically validated.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.config import HospitalConfig
from core.enums import AgeBand, Consciousness
from core.schema import VITAL_FIELDS, Assessment, Contribution, Patient

REPO_ROOT = Path(__file__).resolve().parent.parent
THRESHOLDS_FILE = REPO_ROOT / "data" / "clinical_thresholds.json"
WEIGHTS_FILE = REPO_ROOT / "data" / "risk_weights.json"

MAX_SCORE = 100.0


def _load(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return {k: v for k, v in json.load(fh).items() if not k.startswith("_")}


class RiskEngine:
    """
    Stateless scorer. Give it a patient, get an Assessment back.

    Stateless matters: the same patient state must always produce the same
    score. Anything that changes over time (wait duration, new vitals, an
    answered question) arrives as a NEW patient state from the simulation, not
    as hidden memory inside the engine. That is what makes it testable.
    """

    def __init__(
        self,
        hospital: HospitalConfig,
        thresholds: Optional[dict] = None,
        weights: Optional[dict] = None,
    ):
        self.hospital = hospital
        self.thresholds = thresholds or _load(THRESHOLDS_FILE)
        self.weights = weights or _load(WEIGHTS_FILE)
        self.caps: Dict[str, float] = self.weights["domain_caps"]

    # -----------------------------------------------------------------------
    # Public entry point
    # -----------------------------------------------------------------------

    def assess(self, patient: Patient, now_minute: Optional[int] = None) -> Assessment:
        now = now_minute if now_minute is not None else patient.arrival_minute

        raw: List[Contribution] = []
        raw += self._score_vitals(patient)
        raw += self._score_symptoms(patient)
        raw += self._score_pain(patient)
        raw += self._score_observed(patient)
        raw += self._score_voice(patient)
        raw += self._score_facial(patient)
        raw += self._detect_conflicts(patient)

        contributions, cap_notes = self._apply_domain_caps(raw)
        total = sum(c.points for c in contributions)
        score = max(0.0, min(MAX_SCORE, total))

        assessment = Assessment(
            patient_id=patient.patient_id,
            at_minute=now,
            risk_score=score,
            contributions=sorted(contributions, key=lambda c: -c.points),
            data_completeness=patient.vitals.completeness(),
            missing_fields=patient.vitals.missing_fields(),
        )
        assessment.proposed_band = self.hospital.thresholds.band_for_score(score)
        assessment.safety_rules_fired.extend(cap_notes)

        if total > MAX_SCORE:
            assessment.safety_rules_fired.append(
                f"score_saturated (total {total:.0f} clamped to {MAX_SCORE:.0f})")
        return assessment

    # -----------------------------------------------------------------------
    # Domain caps
    # -----------------------------------------------------------------------

    def _apply_domain_caps(
        self, raw: List[Contribution]
    ) -> Tuple[List[Contribution], List[str]]:
        """
        Cap each clinical domain, recording any reduction as its own line so
        the panel still sums exactly to the final score.
        """
        totals: Dict[str, float] = defaultdict(float)
        for c in raw:
            totals[c.domain] += c.points

        out = list(raw)
        notes: List[str] = []
        for domain, total in sorted(totals.items()):
            cap = self.caps.get(domain)
            if cap is None or total <= cap:
                continue
            out.append(Contribution(
                f"{domain} domain capped at {cap:.0f} "
                f"(raw {total:.0f}, overlapping signals)",
                cap - total, "cap", domain))
            notes.append(f"domain_cap:{domain} {total:.0f}->{cap:.0f}")
        return out, notes

    # -----------------------------------------------------------------------
    # Vitals
    # -----------------------------------------------------------------------

    def _threshold_band(self, age_band: AgeBand) -> dict:
        """
        Phase 3: adult values for everyone.

        Phase 4 replaces this method body with a real per-age lookup. Nothing
        else in the engine will change, which is why the lookup lives here
        rather than inline below.
        """
        return self.thresholds.get(age_band.value, self.thresholds["adult"])

    def _score_vitals(self, patient: Patient) -> List[Contribution]:
        table = self._threshold_band(patient.age_band)
        w = self.weights["vitals"]
        domains = w["domains"]
        out: List[Contribution] = []

        for field_name in VITAL_FIELDS:
            domain = domains.get(field_name, "general")
            value = getattr(patient.vitals, field_name)

            if value is None:
                # NOT MEASURED. Zero points, recorded so it stays visible. It
                # must never contribute NEGATIVE points: a missing measurement
                # is not evidence of wellness.
                out.append(Contribution(
                    f"{field_name}: not measured", 0.0, "vitals", domain))
                continue

            limits = table.get(field_name)
            if not limits:
                continue

            severity, direction = self._classify(value, limits)
            if severity is None:
                continue

            points = w["severe_deviation"] if severity == "severe" else w["mild_deviation"]
            out.append(Contribution(
                f"{field_name} {value:g} ({direction}, {severity})",
                float(points), "vitals", domain))
        return out

    @staticmethod
    def _classify(value: float, limits: dict) -> Tuple[Optional[str], Optional[str]]:
        """Return (severity, direction), or (None, None) if within range."""
        cl, lo = limits.get("critical_low"), limits.get("low")
        hi, ch = limits.get("high"), limits.get("critical_high")

        if cl is not None and value < cl:
            return "severe", "low"
        if ch is not None and value > ch:
            return "severe", "high"
        if lo is not None and value < lo:
            return "mild", "low"
        if hi is not None and value > hi:
            return "mild", "high"
        return None, None

    # -----------------------------------------------------------------------
    # Symptoms and pain
    # -----------------------------------------------------------------------

    def _score_symptoms(self, patient: Patient) -> List[Contribution]:
        """
        Substring match, one award per weight key.

        'mild breathlessness' and 'breathlessness' both hit the same key and
        award once. Crude on purpose: a prototype matcher anyone can audit
        beats an NLP layer nobody can.
        """
        reported = [s.lower() for s in patient.self_report.symptoms]
        out: List[Contribution] = []
        for key, spec in self.weights["symptoms"].items():
            if any(key in s for s in reported):
                out.append(Contribution(
                    f"reports: {key}", float(spec["points"]),
                    "symptoms", spec["domain"]))
        return out

    def _score_pain(self, patient: Patient) -> List[Contribution]:
        cfg = self.weights["pain"]
        domain = cfg["domain"]
        pain = patient.self_report.pain_score

        if pain is None:
            return [Contribution("pain score: not obtained", 0.0, "symptoms", domain)]
        if pain >= cfg["severe_pain_threshold"]:
            return [Contribution(f"severe pain reported ({pain}/10)",
                                 float(cfg["severe_pain_points"]), "symptoms", domain)]
        if pain >= cfg["moderate_pain_threshold"]:
            return [Contribution(f"moderate pain reported ({pain}/10)",
                                 float(cfg["moderate_pain_points"]), "symptoms", domain)]
        return []

    # -----------------------------------------------------------------------
    # Observed and voice
    # -----------------------------------------------------------------------

    def _score_observed(self, patient: Patient) -> List[Contribution]:
        w = self.weights["observed"]
        obs = patient.observed
        out: List[Contribution] = []

        conscious_map = {
            Consciousness.UNRESPONSIVE: ("consciousness_unresponsive", "unresponsive"),
            Consciousness.PAIN: ("consciousness_responds_to_pain", "responds only to pain"),
            Consciousness.VOICE: ("consciousness_responds_to_voice", "responds only to voice"),
        }
        if obs.consciousness in conscious_map:
            key, label = conscious_map[obs.consciousness]
            spec = w[key]
            out.append(Contribution(label, float(spec["points"]), "observed", spec["domain"]))

        for attr, key, label in [
            ("skin_pallor_or_cyanosis", "skin_pallor_or_cyanosis",
             "pallor or cyanosis observed"),
            ("visible_bleeding", "visible_bleeding", "visible bleeding"),
            ("gait_abnormal", "gait_abnormal", "abnormal gait"),
        ]:
            if getattr(obs, attr).is_yes:
                spec = w[key]
                out.append(Contribution(
                    label, float(spec["points"]), "observed", spec["domain"]))
        return out

    def _score_voice(self, patient: Patient) -> List[Contribution]:
        w = self.weights["voice"]
        v = patient.voice

        if not v.capture_status.has_data:
            return [Contribution(
                f"voice signal unavailable ({v.capture_status})",
                0.0, "voice", "general")]

        out: List[Contribution] = []
        for attr, key, label in [
            ("slurred_speech", "slurred_speech", "slurred speech heard"),
            ("unable_to_speak_full_sentence", "unable_to_speak_full_sentence",
             "cannot complete a full sentence"),
            ("breathlessness_between_words", "breathlessness_between_words",
             "breathless between words"),
        ]:
            if getattr(v, attr).is_yes:
                spec = w[key]
                out.append(Contribution(
                    label, float(spec["points"]), "voice", spec["domain"]))
        return out

    # -----------------------------------------------------------------------
    # Facial  --  the baseline-aware scorer
    # -----------------------------------------------------------------------

    def _score_facial(self, patient: Patient) -> List[Contribution]:
        """
        Points come from ACUTE CHANGE, never from appearance.

        Read the branches below carefully. A patient whose face is asymmetric
        because of a congenital difference, a burn injury or an old stroke
        scores ZERO here. The camera saw exactly what it saw in the acute-droop
        patient. The difference is entirely in the baseline context, which is
        the point of the whole module.
        """
        w = self.weights["facial"]
        f = patient.facial

        def make(key: str, label: str) -> List[Contribution]:
            spec = w[key]
            return [Contribution(label, float(spec["points"]), "facial", spec["domain"])]

        if not f.capture_status.has_data:
            # Degrade, do not guess. Zero points; Phase 5 raises uncertainty.
            return [Contribution(
                f"facial signal unavailable ({f.capture_status})",
                0.0, "facial", "neurological")]

        change = f.acute_change()
        looks_abnormal = f.asymmetry_observed.is_yes or f.droop_observed.is_yes

        if change.is_yes:
            if f.has_stroke_cluster():
                return make("acute_change_with_stroke_cluster",
                            "ACUTE facial change with speech and one-sided weakness")
            return make("acute_change_alone", "ACUTE facial change from known baseline")

        if change.is_no:
            if looks_abnormal:
                return make("chronic_baseline_explains_it",
                            f"facial asymmetry present but CHRONIC "
                            f"({f.baseline_condition}), not an acute finding")
            return []

        # UNKNOWN. Zero points, recorded loudly. Phase 5 turns this into a
        # confidence penalty; Phase 11 turns it into a question worth asking.
        if looks_abnormal:
            return make("unknown_baseline",
                        "facial asymmetry with UNKNOWN baseline, cannot tell if acute")
        return []

    # -----------------------------------------------------------------------
    # Conflicts between what is said and what is measured
    # -----------------------------------------------------------------------

    def _detect_conflicts(self, patient: Patient) -> List[Contribution]:
        """
        Round 1 called this 'body over words'. The Round 2 rule is narrower and
        far more defensible: DENIAL DOES NOT CANCEL EVIDENCE.

        A denial never subtracts points. When a denial is contradicted by
        objective findings ABOUT THAT SAME THING, we add a small amount and,
        more importantly, name the conflict so the nurse sees it.

        The rule is deliberately specific. Denying chest pain while breathless
        is not a conflict: it is a patient accurately describing an asthma
        attack. Our first version flagged exactly that and was wrong. Only a
        denial the evidence directly contradicts counts, and it needs two
        independent findings, not one.

        The reverse conflict, a patient reporting severe symptoms with no
        objective corroboration, is flagged at ZERO points. We record that we
        noticed. We do not reduce their risk for it. A system that only doubts
        patients in the direction that frees up beds is not reasoning.
        """
        spec = self.weights["conflict"]["denial_contradicted_by_evidence"]
        sr = patient.self_report
        v = patient.vitals
        out: List[Contribution] = []

        evidence = [
            f"SpO2 {v.spo2:g}" if v.spo2 is not None and v.spo2 < 94 else None,
            f"RR {v.respiratory_rate:g}"
            if v.respiratory_rate is not None and v.respiratory_rate > 22 else None,
            "cannot finish a sentence"
            if patient.voice.unable_to_speak_full_sentence.is_yes else None,
            "pallor or cyanosis"
            if patient.observed.skin_pallor_or_cyanosis.is_yes else None,
        ]
        evidence = [e for e in evidence if e]

        if sr.denies_symptom("breathlessness") and len(evidence) >= 2:
            out.append(Contribution(
                f"CONFLICT: denies breathlessness but {', '.join(evidence)}",
                float(spec["points"]), "conflict", spec["domain"]))

        table = self._threshold_band(patient.age_band)
        vitals_all_normal = not any(
            self._classify(getattr(v, f), table.get(f, {}))[0]
            for f in VITAL_FIELDS if getattr(v, f) is not None
        )
        if (sr.pain_score is not None and sr.pain_score >= 8
                and vitals_all_normal
                and patient.observed.skin_pallor_or_cyanosis.is_no
                and patient.facial.visible_distress.is_no):
            out.append(Contribution(
                "NOTE: high reported pain without objective corroboration "
                "(recorded, not discounted)", 0.0, "conflict", "conflict"))

        return out


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def explain(assessment: Assessment) -> str:
    """Render the contribution trace as the nurse-facing explanation panel."""
    lines = []
    for c in assessment.contributions:
        if c.points != 0:
            lines.append(f"    {str(c):<58}[{c.domain}]")

    zero = [c for c in assessment.contributions if c.points == 0]
    if zero:
        lines.append("    " + "-" * 58)
        lines.append("    Recorded, scoring zero:")
        for c in zero:
            lines.append(f"      {c.label}")

    lines.append("    " + "-" * 58)
    lines.append(f"    {'TOTAL':<10}{assessment.risk_score:>5.0f}/100"
                 f"   ->  {assessment.proposed_band}")
    return "\n".join(lines)
