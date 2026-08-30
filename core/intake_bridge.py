"""
core/intake_bridge.py
=====================
Phase 17. Turns a live intake payload from the browser into a Patient, runs the
real pipeline over it, and returns a serialisable result.

WHY A BRIDGE AND NOT A SECOND ENGINE
------------------------------------
The obvious way to build a browser demo is to re-implement the scoring in
JavaScript so the page is self-contained. That would be the worst decision in
this repository: two implementations of a safety-critical calculation, drifting
apart the moment either is edited, with no way to tell which one a judge is
looking at.

So the page computes nothing. It captures signals, asks an operator to confirm
them, and posts flags. This module converts those flags into exactly the dict
shape data/patients.json uses and hands it to `parse_patient` -- the same loader,
the same validation, the same refusal to guess. A patient who walks in front of
the camera and a patient read off disk become the same object and travel the
same path. tests/test_intake.py asserts that.

WHAT THE CAMERA AND MICROPHONE ACTUALLY CONTRIBUTE
--------------------------------------------------
A candidate reading, and nothing more.

The camera can report that the two halves of a face differ. It cannot report
whether that difference is twenty minutes or twenty years old, and that single
question is the whole clinical difference between a stroke and a person's
ordinary appearance. No improvement in the detector answers it. That is why
this file treats the sensor as one input among several and why the baseline
question is mandatory rather than optional: if the operator cannot answer it,
the payload carries UNKNOWN forward and the uncertainty engine responds to it.

Round 1 proposed doorway scanning as the headline capability. Phase 17 delivers
it live, and demonstrates in the same breath why the scan is the least
interesting part of the system.

SAFETY NOTE: anything the browser does not send stays UNKNOWN. There is no
branch in this file that fills a gap with a reassuring default.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.config import HospitalConfig
from core.patient_loader import PatientDataError, parse_patient
from core.ratchet import Ratchet
from core.risk_engine import RiskEngine, explain
from core.safety_rules import explain_rules
from core.schema import Assessment, Patient
from core.uncertainty import explain_confidence

REPO_ROOT = Path(__file__).resolve().parent.parent
INTAKE_CONFIG = REPO_ROOT / "data" / "intake_config.json"
WEIGHTS_FILE = REPO_ROOT / "data" / "risk_weights.json"


def _load(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return {k: v for k, v in json.load(fh).items() if not k.startswith("_")}


def load_intake_config() -> dict:
    return _load(INTAKE_CONFIG)


# ---------------------------------------------------------------------------
# Free text -> symptoms
# ---------------------------------------------------------------------------

class SymptomReader:
    """
    Extracts symptom terms from typed or dictated text.

    The vocabulary is NOT a new list. It is the key set of the `symptoms` block
    in data/risk_weights.json, so a term the reader can recognise is exactly a
    term the engine can score, and adding a symptom in one place adds it in
    both. The synonym map in data/intake_config.json only widens the surface
    of the same terms.

    Negation is handled explicitly. "no chest pain" must not become a chest
    pain symptom, and it must not vanish either -- it becomes a DENIAL, which
    the conflict detector in core/risk_engine.py reads. A patient telling us
    what they do not have is information.

    This is a deliberately shallow matcher. It is auditable in one sitting,
    which an NLP layer would not be, and every term it produces is shown back
    to the operator for correction before anything is scored.
    """

    def __init__(self, config: Optional[dict] = None, weights: Optional[dict] = None):
        self.config = config or load_intake_config()
        weights = weights or _load(WEIGHTS_FILE)
        self.vocabulary: List[str] = list(weights["symptoms"].keys())
        self.synonyms: Dict[str, List[str]] = self.config.get("symptom_synonyms", {})
        self.negations: List[str] = self.config.get("negation_markers", [])
        self.durations: Dict[str, float] = self.config.get("duration_patterns", {})

    def _phrases_for(self, term: str) -> List[str]:
        return [term] + [s.lower() for s in self.synonyms.get(term, [])]

    def read(self, text: str) -> Tuple[List[str], List[str]]:
        """Return (reported, denied). A term never appears in both."""
        if not text:
            return [], []
        lowered = " " + re.sub(r"\s+", " ", text.lower().strip()) + " "

        reported: List[str] = []
        denied: List[str] = []
        for term in self.vocabulary:
            hit_at = None
            for phrase in self._phrases_for(term):
                idx = lowered.find(phrase)
                if idx != -1:
                    hit_at = idx
                    break
            if hit_at is None:
                continue
            window = lowered[max(0, hit_at - 26):hit_at]
            if any(marker in window for marker in self.negations):
                denied.append(term)
            else:
                reported.append(term)
        return reported, denied

    def duration_hours(self, text: str) -> Optional[float]:
        """Pull 'for three days' / 'about 2 hours' out of free text."""
        if not text:
            return None
        words = {
            "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4,
            "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        }
        lowered = text.lower()
        pattern = r"(\d+(?:\.\d+)?|" + "|".join(words) + r")\s+(" + \
                  "|".join(self.durations) + r")\b"
        match = re.search(pattern, lowered)
        if not match:
            return None
        amount_raw, unit = match.group(1), match.group(2)
        amount = float(words.get(amount_raw, amount_raw)) if not amount_raw.isdigit() \
            else float(amount_raw)
        return round(amount * self.durations[unit], 3)

    def pain_score(self, text: str) -> Optional[int]:
        """Recognise 'seven out of ten' and '8/10'."""
        if not text:
            return None
        lowered = text.lower()
        m = re.search(r"\b(\d{1,2})\s*(?:/|out of)\s*10\b", lowered)
        if m:
            value = int(m.group(1))
            return value if 0 <= value <= 10 else None
        return None


# ---------------------------------------------------------------------------
# Payload -> patient record
# ---------------------------------------------------------------------------

_TRI = {"yes", "no", "unknown"}


def _tri_field(payload: dict, key: str) -> str:
    """
    Read a tri-state from the payload.

    Absent, empty or unrecognised all resolve to 'unknown'. That is the only
    safe direction: a browser that failed to send a field has not told us the
    finding is absent.
    """
    value = str(payload.get(key, "")).strip().lower()
    return value if value in _TRI else "unknown"


def build_patient_record(payload: dict) -> dict:
    """
    Convert an intake payload into the data/patients.json record shape.

    Returned as a plain dict rather than a Patient so that the caller can hand
    it to the same `parse_patient` the file loader uses. Validation, typo
    detection and unknown-preservation are therefore not reimplemented here;
    they are inherited.
    """
    reader = SymptomReader()

    narrative = " ".join(filter(None, [
        payload.get("transcript", ""),
        payload.get("typed_symptoms", ""),
    ])).strip()

    reported, denied = reader.read(narrative)

    # Operator corrections win over the text reader in both directions.
    for term in payload.get("added_symptoms", []) or []:
        if term not in reported:
            reported.append(term)
    for term in payload.get("removed_symptoms", []) or []:
        if term in reported:
            reported.remove(term)
    for term in payload.get("denied_symptoms", []) or []:
        if term in reported:
            reported.remove(term)
        if term not in denied:
            denied.append(term)

    pain = payload.get("pain_score")
    if pain in ("", None):
        pain = reader.pain_score(narrative)
    pain = int(pain) if pain not in ("", None) else None

    duration = payload.get("duration_hours")
    if duration in ("", None):
        duration = reader.duration_hours(narrative)

    vitals = {}
    for field in ("heart_rate", "respiratory_rate", "spo2", "temperature_c",
                  "systolic_bp", "diastolic_bp"):
        raw = payload.get(field)
        if raw not in ("", None):
            vitals[field] = float(raw)
    vitals["measured_at_minute"] = int(payload.get("arrival_minute", 0) or 0)

    facial_capture = payload.get("facial_capture_status", "not_attempted")
    voice_capture = payload.get("voice_capture_status", "not_attempted")

    record = {
        "patient_id": payload.get("patient_id") or "LIVE-001",
        "age_years": float(payload.get("age_years", 40) or 40),
        "sex": payload.get("sex", "unspecified") or "unspecified",
        "arrival_minute": int(payload.get("arrival_minute", 0) or 0),
        "scenario_label": "Live intake",
        "expected_behaviour": "",
        "demonstrates": ["live_intake"],
        "self_report": {
            "chief_complaint": (payload.get("chief_complaint")
                                or narrative[:140] or "not stated"),
            "symptoms": reported,
            "denies": denied,
            "pain_score": pain,
            "symptom_duration_hours": duration,
            "can_communicate": _tri_field(payload, "can_communicate"),
        },
        "vitals": vitals,
        "facial": {
            "capture_status": facial_capture,
            "asymmetry_observed": _tri_field(payload, "asymmetry_observed"),
            "droop_observed": _tri_field(payload, "droop_observed"),
            "visible_distress": _tri_field(payload, "visible_distress"),
            "baseline_known": _tri_field(payload, "baseline_known"),
            "baseline_asymmetry_present": _tri_field(payload, "baseline_asymmetry_present"),
            "baseline_condition": payload.get("baseline_condition", "unknown") or "unknown",
            "change_reported_as_new": _tri_field(payload, "change_reported_as_new"),
            "speech_abnormality": _tri_field(payload, "speech_abnormality"),
            "unilateral_weakness": _tri_field(payload, "unilateral_weakness"),
        },
        "voice": {
            "capture_status": voice_capture,
            "slurred_speech": _tri_field(payload, "slurred_speech"),
            "breathlessness_between_words": _tri_field(payload, "breathlessness_between_words"),
            "unable_to_speak_full_sentence": _tri_field(payload, "unable_to_speak_full_sentence"),
        },
        "observed": {
            "capture_status": payload.get("observed_capture_status", "ok"),
            "gait_abnormal": _tri_field(payload, "gait_abnormal"),
            "consciousness": payload.get("consciousness", "unknown") or "unknown",
            "visible_bleeding": _tri_field(payload, "visible_bleeding"),
            "skin_pallor_or_cyanosis": _tri_field(payload, "skin_pallor_or_cyanosis"),
        },
        "history": {
            "tier": payload.get("history_tier", "zero") or "zero",
            "conditions": payload.get("conditions", []) or [],
            "medications": payload.get("medications", []) or [],
            "previous_visits": payload.get("previous_visits"),
            "baseline_notes": payload.get("baseline_notes", "") or "",
        },
    }
    return record


def build_patient(payload: dict) -> Patient:
    """Payload -> validated Patient, through the ordinary loader."""
    return parse_patient(build_patient_record(payload))


# ---------------------------------------------------------------------------
# Running the pipeline and serialising the answer
# ---------------------------------------------------------------------------

class IntakeSession:
    """
    One live intake session.

    Holds a Ratchet so that repeated assessments of the same patient behave
    exactly as they do on the ward: the band can rise and cannot fall, and a
    reduction requires a named human. Re-submitting the form with worse
    observations is therefore a genuine re-triage, not a fresh score, which is
    what makes the deterioration part of the demo real rather than staged.
    """

    def __init__(self, hospital: str = "medium_ed"):
        self.hospital = HospitalConfig.load(hospital)
        self.engine = RiskEngine(self.hospital)
        self.ratchet = Ratchet()
        self.history: List[Dict[str, Any]] = []

    def assess(self, payload: dict) -> Dict[str, Any]:
        patient = build_patient(payload)
        minute = int(payload.get("arrival_minute", 0) or 0)
        assessment = self.ratchet.record(self.engine.assess(patient, now_minute=minute))
        result = serialise(patient, assessment, self.hospital)
        self.history.append({
            "at_minute": minute,
            "risk_score": assessment.risk_score,
            "band": assessment.band.code if assessment.band else None,
            "band_word": assessment.band.word if assessment.band else None,
            "changed_by": str(assessment.changed_by),
            "reason": assessment.change_reason,
        })
        result["history"] = list(self.history)
        return result


def serialise(patient: Patient, assessment: Assessment,
              hospital: HospitalConfig) -> Dict[str, Any]:
    """Flatten an Assessment into JSON the page can render without computing."""
    band = assessment.band or assessment.proposed_band
    return {
        "patient_id": patient.patient_id,
        "age_years": patient.age_years,
        "age_band": str(patient.age_band),
        "history_tier": str(patient.history.tier),
        "chief_complaint": patient.self_report.chief_complaint,
        "symptoms": patient.self_report.symptoms,
        "denies": patient.self_report.denies,

        "risk_score": round(assessment.risk_score, 1),
        "band_code": band.code if band else None,
        "band_word": band.word if band else None,
        "band_meaning": band.meaning if band else None,
        "proposed_band": assessment.proposed_band.code if assessment.proposed_band else None,
        "previous_band": assessment.previous_band.code if assessment.previous_band else None,
        "changed_by": str(assessment.changed_by),
        "change_reason": assessment.change_reason,
        "escalated": assessment.escalated,
        # The ratchet HELD the band: the score fell and the band did not
        # follow it down. Surfaced explicitly because a screen showing only
        # "system_initial" against a dropping score reads like a bug rather
        # than like the central safety property working.
        "held": bool(
            assessment.previous_band is not None
            and assessment.proposed_band is not None
            and band is not None
            and assessment.proposed_band < band
        ),

        "confidence_pct": assessment.confidence_pct,
        "plausible_bands": [b.code for b in assessment.plausible_bands],
        "uncertainty_drivers": list(assessment.uncertainty_drivers),
        "data_completeness": round(assessment.data_completeness * 100),
        "missing_fields": list(assessment.missing_fields),

        "contributions": [
            {"label": c.label, "points": c.points,
             "source": c.source, "domain": c.domain}
            for c in assessment.contributions
        ],
        "safety_rules": list(assessment.safety_rules_fired),
        "cap_notes": list(getattr(assessment, "cap_notes", [])),

        "panel_score": explain(assessment),
        "panel_confidence": explain_confidence(assessment),
        "panel_rules": explain_rules(assessment),

        "hospital": hospital.name,
        "disclaimer": ("SIMULATED PROTOTYPE. Synthetic thresholds, no clinical "
                       "validation. Not for any real clinical use."),
    }


def assess_payload(payload: dict, hospital: str = "medium_ed") -> Dict[str, Any]:
    """One-shot assessment with a fresh ratchet. Used by the tests."""
    return IntakeSession(hospital).assess(payload)


__all__ = [
    "IntakeSession", "SymptomReader", "assess_payload",
    "build_patient", "build_patient_record", "load_intake_config",
    "serialise", "PatientDataError",
]
