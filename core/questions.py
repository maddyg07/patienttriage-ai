"""
core/questions.py
=================
Value-of-information questioning. The phase where "we do not know that" stops
being a disclosure and starts being a task.

WHAT PHASE 5 SET UP AND NEVER SPENT
-----------------------------------
Since Phase 5 every assessment has carried a confidence figure with a named
dominant driver -- the single largest reason we are unsure. P016 has been
sitting at 18% baseline knowledge for six phases with the system saying, very
honestly and completely uselessly, that it cannot tell whether her face has
changed. Naming a gap is not the same as closing one.

This file closes them, by answering one question about the questions: of
everything we could ask, which one is worth asking?

VALUE IS MEASURED IN DECISIONS, NOT IN INFORMATION
--------------------------------------------------
The obvious ranking is by information gained: ask whatever raises confidence
most. It is the wrong objective and it is worth being precise about why.

A triage system exists to decide what happens to a patient next. A question
that raises confidence from 72% to 95% and leaves the patient in exactly the
band they were already in has changed nothing about their care. It has tidied
our records. Meanwhile a question with two possible answers that put the
patient in two different bands is the entire job, even if it barely moves the
percentage.

So every candidate question is scored primarily on BAND MOVEMENT: can any
answer to it change what we do? Confidence is a secondary term, and cost is a
tie-breaker that can demote a question but can never rescue one that cannot
change a decision.

HOW THE VALUE IS COMPUTED
-------------------------
By counterfactual, not by a model. For each candidate question we take each
possible answer, apply it to a copy of the patient, and re-run the entire
pipeline -- score, age rules, facial module, uncertainty, safety rules. The
value of the question is the spread of the outcomes that come back.

This is slow and completely transparent, which is the same trade the risk
engine makes. There is no learned acquisition function to explain, and the
answer to "why did it ask that?" is a table showing exactly what each answer
would have done.

WHY THIS IS NOT EXPECTED VALUE OF INFORMATION
---------------------------------------------
Textbook VOI weights each outcome by the probability of that answer. We do not
have those probabilities. Nothing in this project has been calibrated against
real patients, so any prior we wrote over "how likely is this patient to say
their face is new?" would be invented -- and it would be the invented number
driving the entire ranking, which is the worst place to hide one.

So we compute the RANGE of outcomes rather than their expectation: a question
is valuable if some answer to it changes the band. That is a possibility
measure, not an expectation, and it is deliberately biased toward asking. It
will sometimes rank a question highly because of an answer the patient was
never likely to give. We would rather over-ask than silently weight a
life-changing answer down to nothing on a prior we made up.

Same posture as the conformal renaming in Phase 5: the interface is
expectation-shaped, so a calibrated answer model drops in later without
changing a single consumer.

ASKING CANNOT LOWER A BAND
--------------------------
Worth stating because it falls out of the design rather than being enforced
here. Every answer produces a fresh assessment which goes through the ratchet
like any other, and the ratchet computes max(proposed, previous). So the only
thing answering a question can do to a patient already in the queue is get them
seen sooner. There is no answer, and no refusal to answer, that moves anyone
down. That makes the questioner safe to run automatically, which is the
property that lets it run at all.

A NON-ANSWER IS NOT A NO
------------------------
"Cannot say" is a real answer with an empty effect set: it leaves the record
exactly as it was, keeps the uncertainty, and keeps the driver that prompted
the question. It never resolves anything and never reduces risk. A questioner
that treated silence as reassurance would be strictly worse than not asking,
because it would convert an honest gap into a false negative.

WHO CAN ANSWER
--------------
A question aimed at a patient who cannot communicate is worth nothing, however
high its score. `answerable_by` is checked before value is computed, and a
patient who cannot speak is routed to collateral and record sources instead.
This is not politeness. An unconscious patient, an infant and a person who does
not share a language with the nurse are three of the highest-risk groups in any
emergency department, and a questioner that quietly produced an empty list for
all of them would fail exactly where it is needed most.

SAFETY NOTE: the question bank is a set of SIMULATED DEMONSTRATION PROMPTS in
data/questions.json. It is not a validated screening instrument and no
clinician has reviewed it.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from core.enums import (
    CaptureStatus,
    FacialBaselineCondition,
    HistoryTier,
    TriageBand,
    Tri,
)
from core.schema import Assessment, Patient

REPO_ROOT = Path(__file__).resolve().parent.parent
QUESTIONS_FILE = REPO_ROOT / "data" / "questions.json"


def _load(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return {k: v for k, v in json.load(fh).items() if not k.startswith("_")}


class QuestionDataError(ValueError):
    """Raised when data/questions.json says something we refuse to guess about."""


# ---------------------------------------------------------------------------
# Applicability predicates
# ---------------------------------------------------------------------------
#
# Named functions rather than a rule mini-language in JSON, for the same reason
# core/age_rules.py names its rules: each one is individually readable,
# individually testable, and shows up by name in the output. A predicate DSL in
# a config file would be a programming language with no debugger.

def _facial_finding_without_baseline(p: Patient) -> bool:
    """A face that looks unusual and no record of what it usually looks like."""
    f = p.facial
    if not f.capture_status.has_data:
        return False
    looks_unusual = f.asymmetry_observed.is_yes or f.droop_observed.is_yes
    return looks_unusual and not f.baseline_known.is_yes


def _headache_without_onset_detail(p: Patient) -> bool:
    sr = p.self_report
    return (sr.has_symptom("headache")
            and not sr.has_symptom("thunderclap onset")
            and not sr.has_symptom("worst headache of life"))


def _denial_contradicted_by_findings(p: Patient) -> bool:
    """
    The patient says one thing and the instruments say another.

    Deliberately narrow, matching the conflict rule in the risk engine: it
    takes a denial AND objective findings that contradict that specific thing.
    """
    if not p.self_report.denies_symptom("breathlessness"):
        return False
    v = p.vitals
    findings = [
        v.spo2 is not None and v.spo2 < 94,
        v.respiratory_rate is not None and v.respiratory_rate > 22,
        p.observed.skin_pallor_or_cyanosis.is_yes,
    ]
    return sum(1 for f in findings if f) >= 1


def _cardiorespiratory_findings_no_chest_symptom(p: Patient) -> bool:
    sr = p.self_report
    if sr.has_symptom("chest pain") or sr.denies_symptom("chest pain"):
        return False
    v = p.vitals
    return bool(
        (v.spo2 is not None and v.spo2 < 94)
        or (v.heart_rate is not None and v.heart_rate > 110)
        or sr.has_symptom("breathlessness")
    )


def _history_thin(p: Patient) -> bool:
    return p.history.tier is HistoryTier.ZERO


def _vitals_stale_or_incomplete(p: Patient) -> bool:
    return len(p.vitals.missing_fields()) > 0


def _pain_not_obtained(p: Patient) -> bool:
    return p.self_report.pain_score is None


def _infant_without_feeding_history(p: Patient) -> bool:
    return (p.age_band.is_pediatric and p.age_years < 2
            and not p.self_report.has_symptom("poor feeding"))


def _older_patient_without_medication_list(p: Patient) -> bool:
    return p.age_years >= 65 and not p.history.medications


def _fall_or_trauma_without_head_detail(p: Patient) -> bool:
    sr = p.self_report
    if sr.has_symptom("head strike") or sr.denies_symptom("head injury"):
        return False
    text = f"{sr.chief_complaint} {' '.join(sr.symptoms)}".lower()
    return any(word in text for word in ("fell", "fall", "collapsed", "trip"))


PREDICATES: Dict[str, Callable[[Patient], bool]] = {
    "facial_finding_without_baseline": _facial_finding_without_baseline,
    "headache_without_onset_detail": _headache_without_onset_detail,
    "denial_contradicted_by_findings": _denial_contradicted_by_findings,
    "cardiorespiratory_findings_no_chest_symptom":
        _cardiorespiratory_findings_no_chest_symptom,
    "history_thin": _history_thin,
    "vitals_stale_or_incomplete": _vitals_stale_or_incomplete,
    "pain_not_obtained": _pain_not_obtained,
    "infant_without_feeding_history": _infant_without_feeding_history,
    "older_patient_without_medication_list": _older_patient_without_medication_list,
    "fall_or_trauma_without_head_detail": _fall_or_trauma_without_head_detail,
}


# ---------------------------------------------------------------------------
# Who can answer
# ---------------------------------------------------------------------------

_PATIENT_SOURCES = {"patient", "patient_or_collateral"}


def can_be_asked(question: "Question", patient: Patient) -> bool:
    """
    Is there anybody who could answer this question for this patient?

    A question the patient cannot answer is worth nothing regardless of how
    much it would tell us, so this is checked before value is computed rather
    than applied as a penalty afterwards.
    """
    if question.answerable_by not in _PATIENT_SOURCES:
        return True                       # a nurse, a record or a relative
    if patient.self_report.can_communicate.is_no:
        # A patient who cannot speak can still be asked ABOUT, by anyone who
        # knows them -- but not asked directly.
        return question.answerable_by == "patient_or_collateral"
    return True


# ---------------------------------------------------------------------------
# Applying an answer
# ---------------------------------------------------------------------------

_ENUM_FIELDS = {
    "facial.baseline_known": Tri,
    "facial.baseline_asymmetry_present": Tri,
    "facial.change_reported_as_new": Tri,
    "facial.asymmetry_observed": Tri,
    "facial.droop_observed": Tri,
    "facial.speech_abnormality": Tri,
    "facial.unilateral_weakness": Tri,
    "facial.baseline_condition": FacialBaselineCondition,
    "facial.capture_status": CaptureStatus,
    "voice.slurred_speech": Tri,
    "voice.unable_to_speak_full_sentence": Tri,
    "voice.breathlessness_between_words": Tri,
    "observed.visible_bleeding": Tri,
    "observed.skin_pallor_or_cyanosis": Tri,
    "observed.gait_abnormal": Tri,
    "history.tier": HistoryTier,
}


def apply_answer(patient: Patient, effects: Dict, now_minute: int = 0) -> Patient:
    """
    Return a copy of the patient with one answer folded in.

    Effects are explicit field paths. Three suffixes:
        "a.b"    assign
        "a.b+"   append to a list, without duplicating
        "a.b-"   remove from a list

    Written out rather than left as a generic setattr because a typo in
    data/questions.json must fail loudly here rather than silently produce a
    question whose answers do nothing -- which would look exactly like a
    question with no value and would be ranked away without anybody noticing.
    """
    p = copy.deepcopy(patient)

    for path, value in effects.items():
        if path == "vitals.refresh":
            # Re-taking observations does not change the numbers -- we have no
            # model of what a fresh set would say, and inventing one would be
            # fabricating clinical data. It updates the TIMESTAMP, which is
            # exactly and only what the staleness driver reads.
            p.vitals.measured_at_minute = now_minute
            continue

        op = "set"
        key = path
        if path.endswith("+"):
            op, key = "append", path[:-1]
        elif path.endswith("-"):
            op, key = "remove", path[:-1]

        if "." not in key:
            raise QuestionDataError(
                f"effect '{path}': expected a field path like 'facial.droop_observed'")
        section_name, attr = key.split(".", 1)

        section = getattr(p, section_name, None)
        if section is None or not hasattr(section, attr):
            raise QuestionDataError(
                f"effect '{path}': no such field. Check it against core/schema.py.")

        if op == "append":
            current = list(getattr(section, attr))
            for item in value:
                if item not in current:
                    current.append(item)
            setattr(section, attr, current)
        elif op == "remove":
            current = [i for i in getattr(section, attr) if i not in value]
            setattr(section, attr, current)
        else:
            enum_cls = _ENUM_FIELDS.get(key)
            setattr(section, attr, enum_cls(value) if enum_cls else value)

    return p


# ---------------------------------------------------------------------------
# Structures
# ---------------------------------------------------------------------------

@dataclass
class Answer:
    label: str
    effects: Dict = field(default_factory=dict)

    @property
    def is_non_answer(self) -> bool:
        """
        An answer that changes nothing on the record.

        Kept as a first-class case rather than filtered out. "Cannot say" is
        information about the patient's situation, and the system needs to be
        able to represent having asked and got nowhere -- which is different
        from not having asked.
        """
        return not self.effects


@dataclass
class Question:
    id: str
    text: str
    addresses: str                       # which confidence driver it targets
    answerable_by: str
    cost_seconds: float
    applicable_when: str
    answers: List[Answer]

    def applies_to(self, patient: Patient) -> bool:
        predicate = PREDICATES.get(self.applicable_when)
        if predicate is None:
            raise QuestionDataError(
                f"{self.id}: unknown predicate '{self.applicable_when}'. "
                f"Known: {', '.join(sorted(PREDICATES))}.")
        return predicate(patient)


@dataclass
class Outcome:
    """What one answer would do."""

    answer: Answer
    risk_score: float
    band: TriageBand
    confidence: float

    @property
    def confidence_pct(self) -> int:
        return int(round(self.confidence * 100))


@dataclass
class QuestionValue:
    """One candidate question, priced."""

    question: Question
    outcomes: List[Outcome]
    current_band: TriageBand
    current_confidence: float
    value: float = 0.0

    @property
    def bands(self) -> List[TriageBand]:
        return sorted({o.band for o in self.outcomes})

    @property
    def can_change_band(self) -> bool:
        return any(o.band != self.current_band for o in self.outcomes)

    @property
    def highest_band(self) -> TriageBand:
        return max(o.band for o in self.outcomes)

    @property
    def band_span(self) -> int:
        """How many bands apart the possible outcomes are."""
        return int(self.highest_band) - int(min(o.band for o in self.outcomes))

    @property
    def confidence_gain(self) -> float:
        """Best confidence improvement any answer offers."""
        return max(0.0, max(o.confidence for o in self.outcomes)
                   - self.current_confidence)

    @property
    def escalating_answers(self) -> List[Outcome]:
        return [o for o in self.outcomes if o.band > self.current_band]

    @property
    def can_escalate(self) -> bool:
        return bool(self.escalating_answers)

    @property
    def escalation_span(self) -> int:
        """How far up the worst answer reaches."""
        if not self.can_escalate:
            return 0
        return int(self.highest_band) - int(self.current_band)

    @property
    def only_proposes_lower(self) -> bool:
        """
        Every answer that moves the band moves it DOWN.

        Worth a name because of what the ratchet does to it. A patient already
        in the queue cannot be moved down by an answer -- the ratchet holds
        them at their previous band regardless. So a question like this does
        not change the queue at all; what it does is give a nurse documented
        grounds to review a de-escalation they would otherwise have no basis
        for. That is worth something, and it is worth much less than finding
        someone who is sicker than we thought.
        """
        return self.can_change_band and not self.can_escalate


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------

class QuestionEngine:
    """
    Prices every applicable question by re-running the pipeline on each answer.

    Stateless, like the risk engine. It never mutates the patient it is given
    and never records an answer -- deciding to ask is a different act from
    receiving an answer, and the second one belongs to a nurse.
    """

    def __init__(self, engine, config: Optional[dict] = None):
        cfg = config or _load(QUESTIONS_FILE)
        self.engine = engine
        self.ranking = cfg["ranking"]
        self.policy = cfg["asking_policy"]
        self.bank: List[Question] = [
            Question(
                id=q["id"],
                text=q["text"],
                addresses=q["addresses"],
                answerable_by=q["answerable_by"],
                cost_seconds=float(q["cost_seconds"]),
                applicable_when=q["applicable_when"],
                answers=[Answer(a["label"], a.get("effects", {}))
                         for a in q["answers"]],
            )
            for q in cfg["questions"]
        ]

    # -----------------------------------------------------------------------
    # Pricing
    # -----------------------------------------------------------------------

    def evaluate(self, patient: Patient, assessment: Optional[Assessment] = None,
                 now_minute: Optional[int] = None) -> List[QuestionValue]:
        """
        Every question worth asking this patient, most valuable first.

        Returns an empty list when nothing applies or nothing would help, and
        that emptiness is a real result: it means the picture is as good as our
        question bank can make it.
        """
        now = now_minute if now_minute is not None else patient.arrival_minute
        base = assessment or self.engine.assess(patient, now_minute=now)

        priced: List[QuestionValue] = []
        for question in self.bank:
            if not question.applies_to(patient):
                continue
            if not can_be_asked(question, patient):
                continue

            outcomes = []
            for answer in question.answers:
                hypothetical = apply_answer(patient, answer.effects, now)
                result = self.engine.assess(hypothetical, now_minute=now)
                outcomes.append(Outcome(
                    answer=answer,
                    risk_score=result.risk_score,
                    band=result.band,
                    confidence=result.confidence,
                ))

            value = QuestionValue(
                question=question,
                outcomes=outcomes,
                current_band=base.band,
                current_confidence=base.confidence,
            )
            value.value = self._price(value)
            if value.value >= float(self.ranking["min_useful_value"]):
                priced.append(value)

        priced.sort(key=lambda v: (-v.value, v.question.cost_seconds))
        return priced

    def _price(self, value: QuestionValue) -> float:
        """
        Band movement first, confidence second, cost as a tie-breaker.

        MOVEMENT IS DIRECTIONAL, and this is the part that matters. An answer
        that could reveal a patient is sicker than we thought changes what
        happens to them today. An answer that could only propose a LOWER band
        changes nothing on its own, because the ratchet holds a waiting patient
        at the band they already have -- its worth is in handing a nurse
        documented grounds for a review they would otherwise have no basis for.
        Real, and worth a fraction.

        Ranking them equally would let the questioner spend its one cheap
        question on confirming that somebody is fine, which is precisely the
        instinct a safety-biased system should not have.

        The cost term is bounded on purpose. An unbounded cost divisor would
        eventually let a cheap, useless question outrank an expensive, decisive
        one -- a triage system that avoided phoning a relative because it took
        two minutes would be optimising the wrong thing entirely.
        """
        r = self.ranking
        movement = 0.0
        if value.can_escalate:
            # Reaching two bands up is worth more than one, but not twice as
            # much: the decisive fact is that the band moves at all.
            span = value.escalation_span
            movement = 1.0 if span <= 1 else 1.0 + 0.25 * (span - 1)
        elif value.only_proposes_lower:
            movement = float(r["deescalation_only_weight"])

        raw = (float(r["band_movement_weight"]) * movement
               + float(r["confidence_weight"]) * value.confidence_gain)

        cost_ratio = value.question.cost_seconds / float(r["cost_reference_seconds"])
        penalty = min(float(r["max_cost_penalty"]),
                      float(r["max_cost_penalty"]) * (cost_ratio - 1.0) / 2.0)
        penalty = max(0.0, penalty)
        return raw * (1.0 - penalty)

    # -----------------------------------------------------------------------
    # Selection
    # -----------------------------------------------------------------------

    def next_question(self, patient: Patient,
                      assessment: Optional[Assessment] = None,
                      now_minute: Optional[int] = None) -> Optional[QuestionValue]:
        """The single best thing to ask, or None if nothing is worth asking."""
        priced = self.evaluate(patient, assessment, now_minute)
        if not priced:
            return None
        best = priced[0]
        if self.policy["stop_when_no_band_movement"] and not best.can_change_band:
            return None
        return best

    def why_not(self, patient: Patient,
                assessment: Optional[Assessment] = None) -> str:
        """
        Explain an empty question list.

        "No questions" and "no questions we can compute a value for" are very
        different states and a nurse should be able to tell them apart.
        """
        applicable = [q for q in self.bank if q.applies_to(patient)]
        if not applicable:
            return ("nothing in the bank applies to this patient: the gaps we "
                    "know how to close are already closed")
        unaskable = [q for q in applicable if not can_be_asked(q, patient)]
        if len(unaskable) == len(applicable):
            return (f"{len(applicable)} question(s) apply but this patient "
                    f"cannot answer them and no collateral source is recorded")
        return ("questions apply, but no answer to any of them would change "
                "this patient's band")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def explain_question(value: QuestionValue, indent: str = "    ") -> str:
    """The panel a nurse sees: the question, and what turns on the answer."""
    q = value.question
    lines = [f"{indent}{q.text}",
             f"{indent}  addresses {q.addresses}; ask {q.answerable_by.replace('_', ' ')}"
             f"; about {q.cost_seconds:.0f}s"]
    for outcome in value.outcomes:
        arrow = ""
        if outcome.band > value.current_band:
            arrow = f"  ESCALATES to {outcome.band.word}"
        elif outcome.band != value.current_band:
            arrow = f"  proposes {outcome.band.word}"
        tag = "  (record unchanged)" if outcome.answer.is_non_answer else ""
        lines.append(
            f"{indent}    \"{outcome.answer.label}\"".ljust(len(indent) + 34)
            + f"risk {outcome.risk_score:>3.0f}  "
              f"conf {outcome.confidence_pct:>3}%  "
              f"{outcome.band.word:<6}{arrow}{tag}")
    return "\n".join(lines)
