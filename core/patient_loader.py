"""
core/patient_loader.py
======================
Turns data/patients.json into typed Patient objects.

WHY THIS IS A SEPARATE FILE
---------------------------
core/schema.py describes WHAT a patient is. This file describes HOW we read one
off disk. Keeping them apart means the engine never has to care whether patients
came from JSON, a database, or a live EHR feed -- which is exactly the swap we
promise in the production architecture.

VALIDATION PHILOSOPHY
---------------------
Loud, not lenient. If a JSON file contains "asymetry_observed" (typo) or an
unrecognised value like "maybe", we raise immediately with the patient ID
attached. A triage system that silently defaults a misspelled field to a safe-
looking value is exactly the failure mode this project exists to prevent.

The ONE deliberate exception: a field that is absent entirely falls back to its
schema default, which for every clinical field is UNKNOWN. Absent means unknown.
It never means normal.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.enums import (
    CaptureStatus,
    Consciousness,
    FacialBaselineCondition,
    HistoryTier,
    Tri,
)
from core.schema import (
    FacialSignals,
    ObservedSignals,
    Patient,
    PatientHistory,
    SelfReport,
    TimedUpdate,
    VitalSigns,
    VoiceSignals,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
PATIENTS_FILE = REPO_ROOT / "data" / "patients.json"


class PatientDataError(ValueError):
    """Raised when patients.json contains something we refuse to guess about."""


# ---------------------------------------------------------------------------
# Small parsing helpers -- each one fails loudly
# ---------------------------------------------------------------------------

def _tri(raw: Optional[str], where: str) -> Tri:
    if raw is None:
        return Tri.UNKNOWN
    try:
        return Tri(raw)
    except ValueError:
        raise PatientDataError(
            f"{where}: '{raw}' is not a valid tri-state. Use yes / no / unknown."
        )


def _enum(enum_cls, raw: Optional[str], default, where: str):
    if raw is None:
        return default
    try:
        return enum_cls(raw)
    except ValueError:
        allowed = ", ".join(m.value for m in enum_cls)
        raise PatientDataError(f"{where}: '{raw}' is invalid. Allowed: {allowed}.")


def _check_keys(section: Dict[str, Any], allowed: set, where: str) -> None:
    """Catch typos. An unrecognised key is an error, not something to ignore."""
    unknown = set(section) - allowed
    if unknown:
        raise PatientDataError(
            f"{where}: unrecognised field(s) {sorted(unknown)}. "
            f"Check spelling against core/schema.py."
        )


# ---------------------------------------------------------------------------
# Section parsers
# ---------------------------------------------------------------------------

_VITAL_KEYS = {
    "heart_rate", "respiratory_rate", "spo2", "temperature_c",
    "systolic_bp", "diastolic_bp", "measured_at_minute",
}


def _parse_vitals(raw: Optional[dict], where: str) -> VitalSigns:
    if not raw:
        return VitalSigns()
    _check_keys(raw, _VITAL_KEYS, f"{where}.vitals")
    return VitalSigns(**raw)


_FACIAL_KEYS = {
    "capture_status", "asymmetry_observed", "droop_observed", "visible_distress",
    "baseline_known", "baseline_asymmetry_present", "baseline_condition",
    "change_reported_as_new", "speech_abnormality", "unilateral_weakness",
}


def _parse_facial(raw: Optional[dict], where: str) -> FacialSignals:
    if not raw:
        return FacialSignals()
    _check_keys(raw, _FACIAL_KEYS, f"{where}.facial")
    w = f"{where}.facial"
    return FacialSignals(
        capture_status=_enum(CaptureStatus, raw.get("capture_status"),
                             CaptureStatus.NOT_ATTEMPTED, w),
        asymmetry_observed=_tri(raw.get("asymmetry_observed"), w),
        droop_observed=_tri(raw.get("droop_observed"), w),
        visible_distress=_tri(raw.get("visible_distress"), w),
        baseline_known=_tri(raw.get("baseline_known"), w),
        baseline_asymmetry_present=_tri(raw.get("baseline_asymmetry_present"), w),
        baseline_condition=_enum(FacialBaselineCondition,
                                 raw.get("baseline_condition"),
                                 FacialBaselineCondition.UNKNOWN, w),
        change_reported_as_new=_tri(raw.get("change_reported_as_new"), w),
        speech_abnormality=_tri(raw.get("speech_abnormality"), w),
        unilateral_weakness=_tri(raw.get("unilateral_weakness"), w),
    )


_VOICE_KEYS = {
    "capture_status", "slurred_speech",
    "breathlessness_between_words", "unable_to_speak_full_sentence",
}


def _parse_voice(raw: Optional[dict], where: str) -> VoiceSignals:
    if not raw:
        return VoiceSignals()
    _check_keys(raw, _VOICE_KEYS, f"{where}.voice")
    w = f"{where}.voice"
    return VoiceSignals(
        capture_status=_enum(CaptureStatus, raw.get("capture_status"),
                             CaptureStatus.NOT_ATTEMPTED, w),
        slurred_speech=_tri(raw.get("slurred_speech"), w),
        breathlessness_between_words=_tri(raw.get("breathlessness_between_words"), w),
        unable_to_speak_full_sentence=_tri(raw.get("unable_to_speak_full_sentence"), w),
    )


_OBSERVED_KEYS = {
    "capture_status", "gait_abnormal", "consciousness",
    "visible_bleeding", "skin_pallor_or_cyanosis",
}


def _parse_observed(raw: Optional[dict], where: str) -> ObservedSignals:
    if not raw:
        return ObservedSignals()
    _check_keys(raw, _OBSERVED_KEYS, f"{where}.observed")
    w = f"{where}.observed"
    return ObservedSignals(
        capture_status=_enum(CaptureStatus, raw.get("capture_status"),
                             CaptureStatus.NOT_ATTEMPTED, w),
        gait_abnormal=_tri(raw.get("gait_abnormal"), w),
        consciousness=_enum(Consciousness, raw.get("consciousness"),
                            Consciousness.UNKNOWN, w),
        visible_bleeding=_tri(raw.get("visible_bleeding"), w),
        skin_pallor_or_cyanosis=_tri(raw.get("skin_pallor_or_cyanosis"), w),
    )


_SELF_REPORT_KEYS = {
    "chief_complaint", "symptoms", "denies", "pain_score",
    "symptom_duration_hours", "can_communicate", "stated_concerns",
}


def _parse_self_report(raw: Optional[dict], where: str) -> SelfReport:
    if not raw:
        return SelfReport()
    _check_keys(raw, _SELF_REPORT_KEYS, f"{where}.self_report")
    return SelfReport(
        chief_complaint=raw.get("chief_complaint", ""),
        symptoms=raw.get("symptoms", []) or [],
        denies=raw.get("denies", []) or [],
        pain_score=raw.get("pain_score"),
        symptom_duration_hours=raw.get("symptom_duration_hours"),
        can_communicate=_tri(raw.get("can_communicate"), f"{where}.self_report"),
        stated_concerns=raw.get("stated_concerns", []) or [],
    )


_HISTORY_KEYS = {
    "tier", "conditions", "medications", "previous_visits", "baseline_notes",
}


def _parse_history(raw: Optional[dict], where: str) -> PatientHistory:
    if not raw:
        return PatientHistory()
    _check_keys(raw, _HISTORY_KEYS, f"{where}.history")
    return PatientHistory(
        tier=_enum(HistoryTier, raw.get("tier"), HistoryTier.ZERO,
                   f"{where}.history"),
        conditions=raw.get("conditions", []) or [],
        medications=raw.get("medications", []) or [],
        previous_visits=raw.get("previous_visits"),
        baseline_notes=raw.get("baseline_notes", ""),
    )


def _parse_trajectory(raw: Optional[list], where: str) -> List[TimedUpdate]:
    if not raw:
        return []
    updates = []
    for i, step in enumerate(raw):
        w = f"{where}.trajectory[{i}]"
        if "at_minute" not in step:
            raise PatientDataError(f"{w}: missing required field 'at_minute'.")
        updates.append(
            TimedUpdate(
                at_minute=step["at_minute"],
                note=step.get("note", ""),
                vitals=_parse_vitals(step.get("vitals"), w),
                observed=_parse_observed(step.get("observed"), w) if step.get("observed") else None,
                facial=_parse_facial(step.get("facial"), w) if step.get("facial") else None,
                new_symptoms=step.get("new_symptoms", []) or [],
            )
        )
    return sorted(updates, key=lambda u: u.at_minute)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_patient(raw: dict) -> Patient:
    pid = raw.get("patient_id")
    if not pid:
        raise PatientDataError("A patient record is missing 'patient_id'.")

    return Patient(
        patient_id=pid,
        age_years=raw["age_years"],
        sex=raw.get("sex", "unspecified"),
        arrival_minute=raw.get("arrival_minute", 0),
        self_report=_parse_self_report(raw.get("self_report"), pid),
        vitals=_parse_vitals(raw.get("vitals"), pid),
        facial=_parse_facial(raw.get("facial"), pid),
        voice=_parse_voice(raw.get("voice"), pid),
        observed=_parse_observed(raw.get("observed"), pid),
        history=_parse_history(raw.get("history"), pid),
        trajectory=_parse_trajectory(raw.get("trajectory"), pid),
        scenario_label=raw.get("scenario_label", ""),
        expected_behaviour=raw.get("expected_behaviour", ""),
        demonstrates=raw.get("demonstrates", []) or [],
    )


def load_patients(path: Path = PATIENTS_FILE) -> List[Patient]:
    """Load and validate every patient. Raises on the first problem found."""
    if not path.exists():
        raise FileNotFoundError(f"Patient file not found: {path}")

    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)

    patients = [parse_patient(p) for p in raw["patients"]]

    ids = [p.patient_id for p in patients]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise PatientDataError(f"Duplicate patient IDs: {sorted(duplicates)}")

    return patients


def load_patient(patient_id: str, path: Path = PATIENTS_FILE) -> Patient:
    for p in load_patients(path):
        if p.patient_id == patient_id:
            return p
    raise PatientDataError(f"No patient with id '{patient_id}'.")


def patients_demonstrating(tag: str, path: Path = PATIENTS_FILE) -> List[Patient]:
    """
    Find every patient carrying a given 'demonstrates' tag.

    The Phase 15 test suite uses this so tests read as intent rather than as
    hard-coded IDs:

        for p in patients_demonstrating("no_false_emergency"):
            assert engine.assess(p).final_band < TriageBand.L4_CODE
    """
    return [p for p in load_patients(path) if tag in p.demonstrates]
