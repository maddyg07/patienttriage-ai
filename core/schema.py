"""
core/schema.py
==============
The data contract for PatientTriage.ai.

Every later module -- risk engine, uncertainty engine, facial module, ratchet,
dashboard, tests -- reads and writes these structures. This file is therefore
the one place where a mistake is expensive, and the one place worth reading
slowly.

DESIGN RULE RUNNING THROUGH THE WHOLE FILE:
    We never let "we don't know" quietly become "no" or "normal".
    Unknown is represented explicitly (Tri.UNKNOWN, None, CaptureStatus),
    and every structure can report what is missing.

SAFETY NOTE: all values are SIMULATED DEMONSTRATION DATA. Nothing here is
clinically validated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:                      # avoids a cycle: safety_rules imports us
    from core.safety_rules import RuleFiring

from core.enums import (
    AgeBand,
    CaptureStatus,
    ChangedBy,
    Consciousness,
    FacialBaselineCondition,
    HistoryTier,
    TriageBand,
    Tri,
)


# ---------------------------------------------------------------------------
# VITAL SIGNS
# ---------------------------------------------------------------------------

VITAL_FIELDS = (
    "heart_rate",
    "respiratory_rate",
    "spo2",
    "temperature_c",
    "systolic_bp",
    "diastolic_bp",
)


@dataclass
class VitalSigns:
    """
    One set of measurements taken at one moment.

    A value of None means NOT MEASURED -- not 'normal'. The data quality layer
    (Phase 5) reads missing_fields() and pushes confidence down accordingly.

    `measured_at_minute` is on the SIMULATED clock (minutes since the shift
    started), which lets us compute staleness: a 90-minute-old SpO2 reading is
    weaker evidence than one taken 2 minutes ago.
    """

    heart_rate: Optional[float] = None
    respiratory_rate: Optional[float] = None
    spo2: Optional[float] = None
    temperature_c: Optional[float] = None
    systolic_bp: Optional[float] = None
    diastolic_bp: Optional[float] = None
    measured_at_minute: Optional[int] = None

    def present_fields(self) -> List[str]:
        return [f for f in VITAL_FIELDS if getattr(self, f) is not None]

    def missing_fields(self) -> List[str]:
        return [f for f in VITAL_FIELDS if getattr(self, f) is None]

    def completeness(self) -> float:
        """0.0 - 1.0. How much of the expected vital set we actually have."""
        return len(self.present_fields()) / len(VITAL_FIELDS)

    def staleness_minutes(self, now_minute: int) -> Optional[int]:
        if self.measured_at_minute is None:
            return None
        return max(0, now_minute - self.measured_at_minute)


# ---------------------------------------------------------------------------
# FACIAL SIGNALS  --  the fairness / baseline-awareness core
# ---------------------------------------------------------------------------

@dataclass
class FacialSignals:
    """
    Observable facial findings PLUS the context needed to interpret them.

    The whole point of this structure is to separate two questions that a naive
    system fuses together:

        "What does this patient look like?"      <- appearance
        "What has CHANGED acutely?"              <- clinical signal

    A patient with congenital asymmetry, acid-attack scarring or chronic
    post-stroke weakness looks unusual and is NOT having an emergency. The
    fields below make that distinction representable in data, which is the
    prerequisite for reasoning about it in Phase 6.
    """

    capture_status: CaptureStatus = CaptureStatus.NOT_ATTEMPTED

    # --- what we observe right now ---
    asymmetry_observed: Tri = Tri.UNKNOWN
    droop_observed: Tri = Tri.UNKNOWN
    visible_distress: Tri = Tri.UNKNOWN

    # --- what we know about this patient's normal appearance ---
    baseline_known: Tri = Tri.UNKNOWN
    baseline_asymmetry_present: Tri = Tri.UNKNOWN
    baseline_condition: FacialBaselineCondition = FacialBaselineCondition.UNKNOWN

    # --- was the change reported as new? (patient, family, or record) ---
    change_reported_as_new: Tri = Tri.UNKNOWN

    # --- associated neurological findings, which change everything ---
    speech_abnormality: Tri = Tri.UNKNOWN
    unilateral_weakness: Tri = Tri.UNKNOWN

    def acute_change(self) -> Tri:
        """
        Is the appearance ACUTELY changed?

        Phase 1 implemented this inline as the seed of the reasoning layer.
        Phase 6 promoted the logic to core/facial.py, which added baseline
        provenance, a recorded decision path and an executable fairness test.
        Every branch of the original survives there; this method is kept
        because it reads naturally at the call sites and because the Phase 1
        harness asserts against it.

        The import is deliberately deferred. core/facial.py imports this
        module for its types, so a top-level import here would be circular.
        One implementation, one direction of dependency, one convenient
        accessor.

        It returns UNKNOWN whenever it genuinely cannot tell, and Phase 7
        treats UNKNOWN as a reason to raise uncertainty and recommend faster
        human review -- never as a reason to relax.
        """
        from core.facial import acute_change as _acute_change
        return _acute_change(self)

    def has_stroke_cluster(self) -> bool:
        """
        Droop/asymmetry + speech + one-sided weakness appearing together.

        Delegates to core/facial.py for the same reason acute_change() does:
        one implementation. Note the module never calls this pattern a
        diagnosis in any user-facing output -- it reports that three findings
        appeared together, and Phase 7 acts on the combination.
        """
        from core.facial import _cluster_components
        return len(_cluster_components(self)) >= 3


# ---------------------------------------------------------------------------
# VOICE + OBSERVED SIGNALS
# ---------------------------------------------------------------------------

@dataclass
class VoiceSignals:
    """Simulated acoustic features (Round 1 'Voice Biomarker Bus')."""

    capture_status: CaptureStatus = CaptureStatus.NOT_ATTEMPTED
    slurred_speech: Tri = Tri.UNKNOWN
    breathlessness_between_words: Tri = Tri.UNKNOWN
    unable_to_speak_full_sentence: Tri = Tri.UNKNOWN


@dataclass
class ObservedSignals:
    """What a human (or the doorway sensor grid) can see without asking."""

    capture_status: CaptureStatus = CaptureStatus.NOT_ATTEMPTED
    gait_abnormal: Tri = Tri.UNKNOWN
    consciousness: Consciousness = Consciousness.UNKNOWN
    visible_bleeding: Tri = Tri.UNKNOWN
    skin_pallor_or_cyanosis: Tri = Tri.UNKNOWN


# ---------------------------------------------------------------------------
# SELF-REPORT
# ---------------------------------------------------------------------------

@dataclass
class SelfReport:
    """
    What the patient tells us.

    `denies` matters as much as `symptoms`. When a patient denies something the
    objective data supports, we do NOT discard their statement and we do NOT
    lower risk. We record the CONFLICT, raise uncertainty, and flag it for the
    nurse. (Round 1 called this 'body over words'; Round 2 phrasing is
    'denial does not cancel evidence'.)
    """

    chief_complaint: str = ""
    symptoms: List[str] = field(default_factory=list)
    denies: List[str] = field(default_factory=list)
    pain_score: Optional[int] = None            # 0-10, None = not asked / refused
    symptom_duration_hours: Optional[float] = None
    can_communicate: Tri = Tri.UNKNOWN

    def has_symptom(self, name: str) -> bool:
        return name.lower() in [s.lower() for s in self.symptoms]

    def denies_symptom(self, name: str) -> bool:
        return name.lower() in [d.lower() for d in self.denies]


# ---------------------------------------------------------------------------
# HISTORY
# ---------------------------------------------------------------------------

@dataclass
class PatientHistory:
    """
    Prior record, which may be entirely absent.

    INVARIANT: tier == ZERO raises uncertainty. It never lowers risk.
    """

    tier: HistoryTier = HistoryTier.ZERO
    conditions: List[str] = field(default_factory=list)
    medications: List[str] = field(default_factory=list)
    previous_visits: Optional[int] = None
    baseline_notes: str = ""

    def has_condition(self, name: str) -> bool:
        return name.lower() in [c.lower() for c in self.conditions]

    def completeness(self) -> float:
        """Crude 0.0 - 1.0 measure used by the data quality layer."""
        return {
            HistoryTier.RICH: 1.0,
            HistoryTier.PARTIAL: 0.5,
            HistoryTier.ZERO: 0.0,
        }[self.tier]


# ---------------------------------------------------------------------------
# TIMED UPDATES  --  what happens to a patient WHILE THEY WAIT
# ---------------------------------------------------------------------------

@dataclass
class TimedUpdate:
    """
    A scheduled change to a patient, fired by the simulation clock.

    This is what makes triage continuous rather than a snapshot. A patient
    arrives, gets scored, and then the world keeps moving: their SpO2 drifts
    down, they answer a question, a family member arrives with history.

    Phase 2 only LOADS these. Phase 10 wires them to the clock and re-runs the
    pipeline each time one fires.
    """

    at_minute: int
    note: str = ""
    vitals: Optional["VitalSigns"] = None
    observed: Optional["ObservedSignals"] = None
    facial: Optional["FacialSignals"] = None
    new_symptoms: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# PATIENT
# ---------------------------------------------------------------------------

@dataclass
class Patient:
    """
    One person in the ED, at one point in the simulation.

    `scenario_label` and `expected_behaviour` are prototype-only fields. They
    document what each hand-authored patient is meant to demonstrate, and the
    Phase 15 tests assert against them. They would not exist in production.
    """

    patient_id: str
    age_years: float
    sex: str = "unspecified"
    arrival_minute: int = 0

    self_report: SelfReport = field(default_factory=SelfReport)
    vitals: VitalSigns = field(default_factory=VitalSigns)
    facial: FacialSignals = field(default_factory=FacialSignals)
    voice: VoiceSignals = field(default_factory=VoiceSignals)
    observed: ObservedSignals = field(default_factory=ObservedSignals)
    history: PatientHistory = field(default_factory=PatientHistory)

    # What happens to this patient while they wait (Phase 10 consumes this).
    trajectory: List[TimedUpdate] = field(default_factory=list)

    scenario_label: str = ""
    expected_behaviour: str = ""
    demonstrates: List[str] = field(default_factory=list)

    @property
    def age_band(self) -> AgeBand:
        return AgeBand.from_age(self.age_years)

    def wait_minutes(self, now_minute: int) -> int:
        return max(0, now_minute - self.arrival_minute)

    def summary_line(self) -> str:
        """Compact one-liner for logs and the queue table."""
        return (
            f"{self.patient_id} | {int(self.age_years)}{self.sex[:1].upper()} "
            f"| {self.age_band} | {self.self_report.chief_complaint}"
        )


# ---------------------------------------------------------------------------
# ASSESSMENT OUTPUT
# ---------------------------------------------------------------------------

@dataclass
class Contribution:
    """
    One line of the explainability panel.

        Contribution("SpO2 91% (low for adult)", 20.0, "vitals", "respiratory")

    `source` is the MODALITY the signal came from (vitals, voice, facial).
    `domain` is the CLINICAL SYSTEM it speaks to (respiratory, neurological).
    They are different questions, and the domain is what gets capped: a
    breathless patient can trip seven respiratory signals, but that is one
    clinical problem, not seven.

    The risk engine builds the score BY appending these, so the explanation is
    not reconstructed after the fact -- it IS the calculation. That is why we
    chose a transparent weighted engine over a trained model.
    """

    label: str
    points: float
    source: str = ""
    domain: str = "general"

    def __str__(self) -> str:
        sign = "+" if self.points >= 0 else ""
        return f"{sign}{self.points:.0f}  {self.label}"


# ---------------------------------------------------------------------------
# DATA QUALITY  --  Phase 5
# ---------------------------------------------------------------------------

@dataclass
class QualityDriver:
    """
    One named reason our confidence is not 100%.

    Confidence is useless as a bare number. "62%" tells a nurse nothing they
    can act on. "62%, because four vitals are missing and the face has no
    documented baseline" tells them exactly which gap to close first, which is
    also what makes the Phase 11 adaptive questions possible: the largest
    penalty is the best question to ask next.
    """

    name: str
    quality: float                                  # 0.0 - 1.0, 1.0 = perfect
    weight: float                                   # its share of confidence
    reasons: List[str] = field(default_factory=list)

    @property
    def penalty(self) -> float:
        """Confidence points this driver removed."""
        return self.weight * (1.0 - self.quality)

    @property
    def quality_pct(self) -> int:
        return int(round(self.quality * 100))


@dataclass
class DataQuality:
    """
    The complete uncertainty picture for one assessment.

    `score_low` / `score_high` bracket the risk score. The bracket is
    ASYMMETRIC by design: it reaches much further upward than downward, because
    information we do not have can hide danger but cannot create safety.
    """

    confidence: float = 1.0
    drivers: List[QualityDriver] = field(default_factory=list)
    score_low: float = 0.0
    score_high: float = 0.0

    @property
    def confidence_pct(self) -> int:
        return int(round(self.confidence * 100))

    def dominant_driver(self) -> Optional[QualityDriver]:
        """The single biggest reason we are unsure. Phase 11 asks about this."""
        scored = [d for d in self.drivers if d.penalty > 0]
        return max(scored, key=lambda d: d.penalty) if scored else None

    def all_reasons(self) -> List[str]:
        """Every named reason, worst driver first."""
        out: List[str] = []
        for d in sorted(self.drivers, key=lambda d: -d.penalty):
            out.extend(d.reasons)
        return out


@dataclass
class Assessment:
    """
    The full result of running the pipeline once for one patient.

    Populated progressively across phases:
        Phase 3  -> risk_score, contributions, proposed_band
        Phase 5  -> confidence, plausible_bands, missing_fields
        Phase 6  -> the facial verdict feeding contributions
        Phase 7  -> rule_firings, band_floor, floor_reason
        Phase 8  -> final_band, changed_by, escalated
    """

    patient_id: str
    at_minute: int

    risk_score: float = 0.0
    contributions: List[Contribution] = field(default_factory=list)

    confidence: float = 1.0                              # 0.0 - 1.0
    plausible_bands: List[TriageBand] = field(default_factory=list)
    uncertainty_drivers: List[str] = field(default_factory=list)
    data_completeness: float = 1.0
    missing_fields: List[str] = field(default_factory=list)
    quality: Optional[DataQuality] = None                # Phase 5 detail

    @property
    def band(self) -> Optional[TriageBand]:
        """
        The band to display. Falls back to the proposal until the ratchet has
        seen this assessment, so no caller can accidentally render a None where
        a patient's acuity should be.
        """
        return self.final_band if self.final_band is not None else self.proposed_band

    @property
    def band_was_held(self) -> bool:
        """The engine wanted to go lower and the ratchet refused."""
        return (self.final_band is not None and self.proposed_band is not None
                and self.final_band > self.proposed_band)

    @property
    def band_was_floored(self) -> bool:
        """True when a hard rule, not the score, decided this band."""
        return bool(self.floor_reason)

    @property
    def band_is_certain(self) -> bool:
        """True when the uncertainty interval cannot reach another band."""
        return len(self.plausible_bands) <= 1

    @property
    def worst_plausible_band(self) -> Optional[TriageBand]:
        """
        The most urgent band our uncertainty can reach.

        This is the number the Phase 7 safety guard reasons about. The proposed
        band answers "what do we think"; this answers "what could we be missing",
        and in a safety-biased system the second question is the one that
        triggers action.
        """
        return max(self.plausible_bands) if self.plausible_bands else self.proposed_band

    cap_notes: List[str] = field(default_factory=list)          # domain caps
    safety_rules_fired: List[str] = field(default_factory=list)  # Phase 7
    rule_firings: List["RuleFiring"] = field(default_factory=list)
    band_floor: Optional[TriageBand] = None
    floor_reason: str = ""

    proposed_band: Optional[TriageBand] = None           # before the ratchet
    final_band: Optional[TriageBand] = None              # after the ratchet
    previous_band: Optional[TriageBand] = None
    changed_by: ChangedBy = ChangedBy.SYSTEM_INITIAL
    change_reason: str = ""

    @property
    def escalated(self) -> bool:
        return (
            self.previous_band is not None
            and self.final_band is not None
            and self.final_band > self.previous_band
        )

    @property
    def confidence_pct(self) -> int:
        return int(round(self.confidence * 100))
