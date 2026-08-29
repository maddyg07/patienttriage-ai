"""
core/age_rules.py
=================
Everything the engine knows about age lives here.

TWO KINDS OF AGE-AWARENESS
--------------------------
Most projects stop at the first one. Both matter.

1. THRESHOLDS. The same number means different things at different ages. A
   heart rate of 168 is normal in an eight-month-old and a crisis in a
   58-year-old. Handled by selecting a table from data/clinical_thresholds.json.

2. CONTEXT RULES. Some concerns only exist, or only matter, at certain ages,
   and they are invisible to any threshold table:

     * An infant who is lethargic and feeding poorly is seriously unwell even
       when every individual vital sits inside the normal range.
     * An 82-year-old on a rate-limiting medication cannot mount the
       tachycardia that would normally announce blood loss. A "normal" heart
       rate in that patient is not reassurance, it is a masked signal.
     * An older patient on an anticoagulant who has struck their head carries
       a bleeding risk that no vital sign will show for hours.
     * Older patients often present atypically, with no pain and no fever, and
       are under-triaged for exactly that reason.

   These are the cases where a threshold-only system quietly fails, so they
   are implemented as explicit, named, individually testable rules.

SAFETY NOTE: every threshold and weight is a SIMULATED DEMONSTRATION VALUE.
The rules below are simplified reasoning patterns written for a prototype, not
clinical protocols, and they have not been reviewed by a clinician.
"""

from __future__ import annotations

from typing import List

from core.enums import AgeBand, Consciousness
from core.schema import Contribution, Patient

# Medications that blunt the heart-rate response to physiological stress.
# Simulated list for demonstration; a production system would resolve these
# from a coded drug dictionary rather than by name matching.
RATE_LIMITING_DRUGS = (
    "bisoprolol", "atenolol", "metoprolol", "propranolol", "carvedilol",
    "diltiazem", "verapamil", "ivabradine",
)

ANTICOAGULANTS = (
    "apixaban", "rivaroxaban", "edoxaban", "dabigatran", "warfarin",
    "clopidogrel", "ticagrelor", "prasugrel",
)

HEAD_INJURY_TERMS = ("head strike", "head injury", "hit my head", "fall", "fell")


def thresholds_for(age_band: AgeBand, tables: dict) -> dict:
    """
    Select the threshold table for an age band.

    Falls back to adult only if a table is genuinely absent, and that fallback
    is a defect to be fixed, not a feature. Phase 3 relied on it for every
    patient; from Phase 4 onward all five tables exist.
    """
    return tables.get(age_band.value, tables["adult"])


def _text(items) -> str:
    return " ".join(items).lower()


def context_rules(patient: Patient, weights: dict) -> List[Contribution]:
    """
    Age-specific reasoning that no threshold table can express.

    Each rule is small, named, and returns its own Contribution so it appears
    in the explanation panel by name. A nurse should be able to read
    'anticoagulated and struck head' and immediately agree or disagree.
    """
    cfg = weights.get("age_context", {})
    band = patient.age_band
    out: List[Contribution] = []

    symptoms = _text(patient.self_report.symptoms + [patient.self_report.chief_complaint])
    meds = _text(patient.history.medications)
    v = patient.vitals

    # ---- INFANT -----------------------------------------------------------
    if band is AgeBand.INFANT:
        unwell_signs = any([
            "poor feeding" in symptoms,
            "not feeding" in symptoms,
            "lethargy" in symptoms or "floppy" in symptoms,
            patient.observed.consciousness not in
            (Consciousness.ALERT, Consciousness.UNKNOWN),
        ])
        if unwell_signs:
            spec = cfg["infant_reduced_feeding_or_responsiveness"]
            out.append(Contribution(
                "infant with reduced feeding or responsiveness "
                "(concerning regardless of individual vitals)",
                float(spec["points"]), "age_rule", spec["domain"]))

    # ---- PEDIATRIC GENERALLY ---------------------------------------------
    if band.is_pediatric and patient.self_report.can_communicate.is_no:
        spec = cfg["pediatric_cannot_self_report"]
        out.append(Contribution(
            "cannot self-report symptoms (history is second-hand)",
            float(spec["points"]), "age_rule", spec["domain"]))

    # ---- GERIATRIC --------------------------------------------------------
    if band is AgeBand.GERIATRIC:
        on_anticoagulant = any(d in meds for d in ANTICOAGULANTS)
        head_injury = any(t in symptoms for t in HEAD_INJURY_TERMS)
        if on_anticoagulant and head_injury:
            spec = cfg["geriatric_anticoagulated_head_injury"]
            drug = next(d for d in ANTICOAGULANTS if d in meds)
            out.append(Contribution(
                f"anticoagulated ({drug}) and struck head "
                f"(delayed bleeding risk, vitals may stay normal for hours)",
                float(spec["points"]), "age_rule", spec["domain"]))

        on_rate_limiter = any(d in meds for d in RATE_LIMITING_DRUGS)
        hr_looks_normal = v.heart_rate is not None and 50 <= v.heart_rate <= 105
        other_concern = any([
            v.systolic_bp is not None and v.systolic_bp < 110,
            v.spo2 is not None and v.spo2 < 94,
            head_injury,
        ])
        if on_rate_limiter and hr_looks_normal and other_concern:
            spec = cfg["geriatric_rate_limited_masking"]
            drug = next(d for d in RATE_LIMITING_DRUGS if d in meds)
            out.append(Contribution(
                f"heart rate {v.heart_rate:g} may be blunted by {drug} "
                f"(normal rate is not reassurance here)",
                float(spec["points"]), "age_rule", spec["domain"]))

        pain = patient.self_report.pain_score
        no_pain = pain is not None and pain <= 1
        no_fever = v.temperature_c is not None and v.temperature_c < 37.8
        abnormal_something = any([
            v.spo2 is not None and v.spo2 < 94,
            v.systolic_bp is not None and v.systolic_bp < 110,
            v.respiratory_rate is not None and v.respiratory_rate > 20,
            v.temperature_c is not None and v.temperature_c < 36.0,
            patient.observed.consciousness not in
            (Consciousness.ALERT, Consciousness.UNKNOWN),
        ])
        if no_pain and no_fever and abnormal_something:
            spec = cfg["geriatric_atypical_presentation"]
            out.append(Contribution(
                "atypical presentation: no pain, no fever, but objective "
                "findings present (classic under-triage pattern)",
                float(spec["points"]), "age_rule", spec["domain"]))

    return out


def describe_band(age_band: AgeBand, tables: dict) -> str:
    """Human-readable summary of which table a patient is being judged against."""
    table = thresholds_for(age_band, tables)
    span = table.get("_span", "")
    hr = table.get("heart_rate", {})
    return (f"{age_band.value} ({span}): "
            f"heart rate normal range {hr.get('low')}-{hr.get('high')}")
