"""
core/uncertainty.py
===================
Attaches an honest confidence figure, a named reason for it, and a set of
plausible bands to every assessment.

WHAT CONFIDENCE MEANS HERE, AND WHAT IT DOES NOT
------------------------------------------------
Confidence is a statement about OUR INFORMATION, not about the patient.

    40% confidence  does NOT mean  "probably fine"
    40% confidence  DOES mean      "we are reasoning from a thin, stale or
                                    contradictory picture -- a human should
                                    look sooner, not later"

This distinction is the whole phase. A system that reports low confidence and
then quietly de-prioritises the patient has built a machine for losing people.
So one rule is enforced structurally rather than by good intentions:

    THE UNCERTAINTY ENGINE NEVER TOUCHES risk_score.

It reads the score. It cannot write it. Confidence widens the range of bands we
admit are possible, and the widening is ASYMMETRIC -- much further up than down,
because information we do not have can conceal danger but cannot create safety.

THE FOUR DRIVERS
----------------
Confidence starts at 1.0 and four named drivers subtract from it. Each reports
its own quality figure and its own plain-English reasons.

  1. COMPLETENESS   How much of the expected picture do we hold? Missing
                    vitals, failed sensors, no history, unasked questions.
  2. AGREEMENT      Do the modalities tell the same story? A patient who says
                    they are fine while their SpO2 says otherwise is not a
                    high-confidence assessment in either direction.
  3. BASELINE       Do we know what NORMAL looks like for THIS patient? This is
                    the P016 driver: a face we cannot interpret because we have
                    never seen this person before.
  4. STALENESS      How old is the evidence? A 90-minute-old observation of a
                    waiting patient is a weaker claim than a fresh one, and
                    saying so is what makes re-triage (Phase 10) meaningful.

WHY THIS IS NOT CALLED A CONFORMAL PREDICTOR
--------------------------------------------
Round 1 proposed a "Conformal Risk Guard". Conformal prediction gives a set
with a coverage guarantee, and that guarantee comes from calibration against
real labelled outcomes. We have synthetic patients and no outcomes, so we
cannot honestly claim coverage, and we do not.

What this file implements is a monotone uncertainty widening rule: the worse
the input data, the wider the plausible set, always in the safe direction. The
band-set INTERFACE is deliberately conformal-shaped so that a calibrated
predictor can be dropped in later without changing a single consumer. Claiming
the guarantee now would be the easy version and the dishonest one.

SAFETY NOTE: every weight is a SIMULATED DEMONSTRATION VALUE loaded from
data/uncertainty_config.json. Nothing here is clinically validated.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Tuple

from core.config import HospitalConfig
from core.enums import CaptureStatus, Consciousness, HistoryTier, TriageBand, Tri
from core.schema import Assessment, DataQuality, Patient, QualityDriver

REPO_ROOT = Path(__file__).resolve().parent.parent
UNCERTAINTY_FILE = REPO_ROOT / "data" / "uncertainty_config.json"

# Modality verdicts used by the agreement driver.
CONCERNING = "concerning"
REASSURING = "reassuring"
SILENT = "silent"


def _load(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return {k: v for k, v in json.load(fh).items() if not k.startswith("_")}


class UncertaintyEngine:
    """
    Stateless. Takes a patient and the assessment already produced for them,
    and returns the same assessment with its uncertainty fields populated.

    Kept as a separate stage rather than folded into RiskEngine so that the
    Phase 15 tests can assert the invariant that matters most: running this
    engine leaves risk_score bit-for-bit identical.
    """

    def __init__(self, config: Optional[dict] = None):
        self.cfg = config or _load(UNCERTAINTY_FILE)
        self.weights = self.cfg["driver_weights"]

    # -----------------------------------------------------------------------
    # Public entry point
    # -----------------------------------------------------------------------

    def apply(
        self,
        patient: Patient,
        assessment: Assessment,
        hospital: HospitalConfig,
        now_minute: Optional[int] = None,
    ) -> Assessment:
        now = now_minute if now_minute is not None else assessment.at_minute
        score_before = assessment.risk_score

        drivers = [
            self._completeness(patient),
            self._agreement(patient, assessment),
            self._baseline(patient),
            self._staleness(patient, now),
        ]

        deficit = sum(d.penalty for d in drivers)
        confidence = max(float(self.cfg["confidence_floor"]), 1.0 - deficit)

        low, high = self._score_interval(assessment.risk_score, confidence)
        bands = self._bands_between(low, high, hospital)

        quality = DataQuality(
            confidence=confidence, drivers=drivers, score_low=low, score_high=high
        )

        assessment.quality = quality
        assessment.confidence = confidence
        assessment.plausible_bands = bands
        assessment.uncertainty_drivers = quality.all_reasons()

        # The invariant, checked rather than promised.
        assert assessment.risk_score == score_before, (
            "uncertainty engine modified the risk score -- this must never happen"
        )
        return assessment

    # -----------------------------------------------------------------------
    # Driver 1: completeness
    # -----------------------------------------------------------------------

    def _completeness(self, patient: Patient) -> QualityDriver:
        """
        How much of the expected picture we actually hold.

        Note what is NOT here: nothing in this driver looks at whether the data
        we have is reassuring. A patient with two vitals that both look perfect
        is still an incomplete patient.
        """
        sub = self.cfg["completeness"]
        reasons: List[str] = []

        # --- vitals ---
        vitals_q = patient.vitals.completeness()
        missing = patient.vitals.missing_fields()
        if missing:
            reasons.append(
                f"{len(missing)} of 6 vitals not measured ({', '.join(missing)})")

        # --- sensor modalities ---
        modalities = [
            ("facial", patient.facial.capture_status),
            ("voice", patient.voice.capture_status),
            ("observed", patient.observed.capture_status),
        ]
        ok = [name for name, status in modalities if status.has_data]
        modality_q = len(ok) / len(modalities)
        for name, status in modalities:
            if status.has_data:
                continue
            if status is CaptureStatus.FAILED:
                reasons.append(f"{name} capture failed (sensor error)")
            elif status is CaptureStatus.REFUSED:
                reasons.append(f"{name} capture declined by patient")
            else:
                reasons.append(f"{name} capture not attempted")

        # --- history ---
        history_q = patient.history.completeness()
        if patient.history.tier is HistoryTier.ZERO:
            reasons.append("no prior record on file (first-time patient)")
        elif patient.history.tier is HistoryTier.PARTIAL:
            reasons.append("prior record is partial")

        # --- self report ---
        answered = 0
        if patient.self_report.pain_score is not None:
            answered += 1
        else:
            reasons.append("pain score not obtained")
        if patient.self_report.can_communicate.is_known:
            answered += 1
        if patient.self_report.symptoms or patient.self_report.chief_complaint:
            answered += 1
        self_report_q = answered / 3

        quality = (
            sub["vitals_weight"] * vitals_q
            + sub["modality_weight"] * modality_q
            + sub["history_weight"] * history_q
            + sub["self_report_weight"] * self_report_q
        )
        return QualityDriver(
            "completeness", quality, self.weights["completeness"], reasons)

    # -----------------------------------------------------------------------
    # Driver 2: signal agreement
    # -----------------------------------------------------------------------

    def _agreement(self, patient: Patient, assessment: Assessment) -> QualityDriver:
        """
        Do the modalities tell the same story?

        Each modality returns concerning / reassuring / silent. SILENT IS NOT
        AGREEMENT -- a modality with no data neither supports nor contradicts
        anything, and its absence is already charged to completeness. Charging
        it twice would make missing data look like conflict, which it is not.

        Disagreement is not an error to be resolved by picking a winner. It is
        a fact about the patient that the nurse should see. P010 denies
        breathlessness while desaturating; P020 reports severe pain with a
        completely unremarkable examination. Both are real presentations, both
        are genuinely uncertain, and in both cases the honest output is a wide
        band and a named conflict -- not a confident score in either direction.
        """
        cfg = self.cfg["agreement"]
        verdicts = self._modality_verdicts(patient)
        reasons: List[str] = []

        concerning = [n for n, v in verdicts if v == CONCERNING]
        reassuring = [n for n, v in verdicts if v == REASSURING]

        quality = 1.0
        if concerning and reassuring:
            split = min(len(concerning), len(reassuring)) / (
                len(concerning) + len(reassuring))
            quality = 1.0 - split
            reasons.append(
                f"modalities disagree: {', '.join(concerning)} concerning "
                f"vs {', '.join(reassuring)} reassuring")

        # Named conflicts already found by the risk engine count on top.
        named = [c for c in assessment.contributions
                 if c.label.startswith(("CONFLICT", "NOTE"))]
        if named:
            penalty = min(
                float(cfg["max_named_conflict_penalty"]),
                len(named) * float(cfg["named_conflict_penalty"]))
            quality = max(0.0, quality - penalty)
            for c in named:
                reasons.append(c.label.split(":", 1)[-1].strip().lower())

        return QualityDriver("agreement", quality, self.weights["agreement"], reasons)

    @staticmethod
    def _modality_verdicts(patient: Patient) -> List[Tuple[str, str]]:
        """
        Reduce each modality to one word. Deliberately crude and readable: a
        judge should be able to check any line of this by hand.
        """
        out: List[Tuple[str, str]] = []
        v = patient.vitals

        # --- vitals: use plainly abnormal ranges, adult-agnostic on purpose.
        # This is a coarse agreement check, not a second scoring engine; the
        # age-aware thresholds already did the scoring.
        flags = [
            v.spo2 is not None and v.spo2 < 94,
            v.respiratory_rate is not None and v.respiratory_rate > 24,
            v.systolic_bp is not None and v.systolic_bp < 100,
            v.temperature_c is not None and (v.temperature_c > 38.5 or v.temperature_c < 36.0),
        ]
        if any(flags):
            out.append(("vitals", CONCERNING))
        elif v.completeness() >= 0.8:
            out.append(("vitals", REASSURING))
        else:
            out.append(("vitals", SILENT))

        # --- self report ---
        sr = patient.self_report
        if sr.pain_score is not None and sr.pain_score >= 7:
            out.append(("self-report", CONCERNING))
        elif sr.pain_score is not None and sr.pain_score <= 2 and not sr.symptoms:
            out.append(("self-report", REASSURING))
        elif sr.symptoms:
            out.append(("self-report", CONCERNING if sr.pain_score is None
                        or sr.pain_score >= 5 else SILENT))
        else:
            out.append(("self-report", SILENT))

        # --- voice ---
        vo = patient.voice
        if not vo.capture_status.has_data:
            out.append(("voice", SILENT))
        elif any(x.is_yes for x in (vo.slurred_speech,
                                    vo.breathlessness_between_words,
                                    vo.unable_to_speak_full_sentence)):
            out.append(("voice", CONCERNING))
        elif all(x.is_no for x in (vo.slurred_speech,
                                   vo.breathlessness_between_words,
                                   vo.unable_to_speak_full_sentence)):
            out.append(("voice", REASSURING))
        else:
            out.append(("voice", SILENT))

        # --- observed ---
        ob = patient.observed
        if not ob.capture_status.has_data:
            out.append(("observed", SILENT))
        # Systemic findings only. Visible bleeding is deliberately excluded:
        # a patient who reports a cut and is visibly bleeding is two signals
        # AGREEING, and our first version scored that as a conflict (P001).
        elif (ob.consciousness not in (Consciousness.ALERT, Consciousness.UNKNOWN)
                or ob.skin_pallor_or_cyanosis.is_yes):
            out.append(("observed", CONCERNING))
        elif ob.consciousness is Consciousness.ALERT:
            out.append(("observed", REASSURING))
        else:
            out.append(("observed", SILENT))

        # --- facial: only the ACUTE CHANGE verdict, never appearance ---
        change = patient.facial.acute_change()
        if change.is_yes:
            out.append(("facial", CONCERNING))
        elif change.is_no:
            out.append(("facial", REASSURING))
        else:
            out.append(("facial", SILENT))

        return out

    # -----------------------------------------------------------------------
    # Driver 3: baseline knowledge
    # -----------------------------------------------------------------------

    def _baseline(self, patient: Patient) -> QualityDriver:
        """
        Do we know what normal looks like for THIS person?

        This is the driver that fixes P016. She has an asymmetric face and no
        record anywhere. The risk engine correctly refuses to score her face,
        because scoring it either way would be a guess. That left her sitting
        last in the queue at 4/100 looking like the safest patient in the
        department, when the truth is that she is the patient we understand
        least. Phase 5 does not move her score -- it says so out loud.

        Penalties are multiplicative: an undocumented face AND no record is a
        worse epistemic position than either alone.
        """
        cfg = self.cfg["baseline"]
        f = patient.facial
        quality = 1.0
        reasons: List[str] = []

        looks_abnormal = f.asymmetry_observed.is_yes or f.droop_observed.is_yes

        if f.capture_status.has_data and looks_abnormal and f.acute_change() is Tri.UNKNOWN:
            quality *= 1.0 - float(cfg["facial_abnormal_but_baseline_unknown"])
            reasons.append(
                "facial asymmetry present with NO documented baseline: "
                "cannot tell acute change from lifelong appearance")
        elif not f.capture_status.has_data:
            quality *= 1.0 - float(cfg["facial_capture_unavailable"])
            reasons.append("no facial baseline comparison possible (no capture)")

        if patient.history.tier is HistoryTier.ZERO:
            quality *= 1.0 - float(cfg["history_zero"])
            reasons.append("nothing on file: no baseline vitals, conditions or meds")
        elif patient.history.tier is HistoryTier.PARTIAL:
            quality *= 1.0 - float(cfg["history_partial"])
            reasons.append("partial record: baseline is incomplete")

        return QualityDriver("baseline", quality, self.weights["baseline"], reasons)

    # -----------------------------------------------------------------------
    # Driver 4: staleness
    # -----------------------------------------------------------------------

    def _staleness(self, patient: Patient, now_minute: int) -> QualityDriver:
        """
        How old is our evidence?

        A patient scored on arrival and left for ninety minutes has not been
        assessed for ninety minutes. Most triage systems keep displaying the
        original number at full strength, which is precisely how a waiting room
        deterioration goes unnoticed. Here the confidence decays until someone
        takes a fresh set of observations, so the queue shows a patient getting
        LESS certain as they wait, not more settled.
        """
        cfg = self.cfg["staleness"]
        fresh, stale = float(cfg["fresh_minutes"]), float(cfg["stale_minutes"])
        age = patient.vitals.staleness_minutes(now_minute)

        if age is None:
            return QualityDriver(
                "staleness", float(cfg["untimestamped_quality"]),
                self.weights["staleness"],
                ["vitals carry no timestamp: cannot tell how old they are"])

        if age <= fresh:
            return QualityDriver("staleness", 1.0, self.weights["staleness"], [])

        if age >= stale:
            return QualityDriver(
                "staleness", 0.0, self.weights["staleness"],
                [f"vitals are {age} minutes old (no fresh observations)"])

        quality = 1.0 - (age - fresh) / (stale - fresh)
        return QualityDriver(
            "staleness", quality, self.weights["staleness"],
            [f"vitals are {age} minutes old"])

    # -----------------------------------------------------------------------
    # From confidence to a set of plausible bands
    # -----------------------------------------------------------------------

    def _score_interval(self, score: float, confidence: float) -> Tuple[float, float]:
        """
        Asymmetric on purpose, and this is the most important six lines in the
        file. What we do not know can hide danger. It cannot manufacture safety.
        So the interval reaches four times further up than down, and at perfect
        confidence it collapses to the score itself.
        """
        cfg = self.cfg["plausible_bands"]
        spread = (1.0 - confidence) * float(cfg["upward_span_points"])
        up = spread
        down = spread * float(cfg["downward_fraction"])
        return max(0.0, score - down), min(100.0, score + up)

    @staticmethod
    def _bands_between(low: float, high: float,
                       hospital: HospitalConfig) -> List[TriageBand]:
        lo = hospital.thresholds.band_for_score(low)
        hi = hospital.thresholds.band_for_score(high)
        return [b for b in TriageBand if lo <= b <= hi]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def explain_confidence(assessment: Assessment) -> str:
    """Render the uncertainty panel that sits under the score panel."""
    q = assessment.quality
    if q is None:
        return "    (no uncertainty assessment attached)"

    lines = [f"    CONFIDENCE {q.confidence_pct}%"
             f"    plausible bands: "
             f"{', '.join(b.word for b in assessment.plausible_bands)}"]
    lines.append(f"    score {assessment.risk_score:.0f}, "
                 f"uncertainty interval {q.score_low:.0f} - {q.score_high:.0f}")
    lines.append("    " + "-" * 58)

    for d in sorted(q.drivers, key=lambda d: -d.penalty):
        marker = "!" if d.penalty > 0.10 else " "
        lines.append(f"  {marker} {d.name:<14}{d.quality_pct:>4}%"
                     f"   (-{d.penalty * 100:.0f} confidence pts)")
        for r in d.reasons:
            lines.append(f"        - {r}")

    dominant = q.dominant_driver()
    if dominant:
        lines.append("    " + "-" * 58)
        lines.append(f"    biggest gap: {dominant.name}"
                     f"  ->  Phase 11 asks about this first")
    return "\n".join(lines)
