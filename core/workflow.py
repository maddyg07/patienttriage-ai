"""
core/workflow.py
================
The clinician's side of the loop. Where the board stops being something to read
and becomes something to act on.

WHAT PHASE 12 LEFT UNFINISHED
-----------------------------
The board reports three things a charge nurse needs: who is sickest, who has
waited past their target, and who we are least sure about. It also prints the
questions worth asking. Every one of those was read-only. The overdue list had
no way to shrink, and the questions had nowhere to send an answer.

This file adds the four things a person can do, and nothing else:

    mark_seen           a clinician made contact with this patient
    answer_question     somebody answered one of the questions we were asking
    unable_to_answer    somebody tried, and could not get an answer
    override            a nurse changes the band  (delegates to Phase 8)

Four actions is not a small API by accident. Every additional verb here is a
new way for the record to disagree with what happened.

THE SAFETY PROPERTY OF THIS PHASE
---------------------------------
Nothing in this system can mark a patient as seen. There is no automated path,
no default, no batch operation and no config flag that produces one. Only a
person can, and only under their own identifier.

That restriction is the point rather than an inconvenience. "Waiting past
target" is the panel that says the department is not keeping up. A system able
to clear its own overdue list could make that panel look healthy without
anybody being treated -- and an engine that can improve its own reported
metrics will eventually be tuned to do so, whether or not anybody sets out to
cheat. The same reasoning as the ratchet, pointed at a different failure: there
the machine must not lower acuity, here it must not close a need.

SEEN IS NOT TREATED
-------------------
`mark_seen` records that a clinician made contact. It says nothing about
whether anything was done, whether the patient improved, or whether they still
need a bed. A department can reach total compliance with a time-to-clinician
target by having somebody walk past every patient in the waiting room, and
target-driven systems reliably discover exactly that.

We record contact because it is the only thing we can honestly observe from
here. We do not call it a quality measure, the board does not describe it as
one, and `time_to_seen()` is labelled as what it is. Naming the weakness is
worth more than a stronger-sounding metric we cannot support.

AN ANSWER IS NOT AN OBSERVATION
-------------------------------
Phase 10 drew a line between the world changing and the system noticing. This
file draws the matching one: an answer is evidence a PERSON gave us, so it
carries their identifier, whereas a trajectory update is the world moving
whether anyone is watching. Merging them would make it impossible to tell what
we were told from what we observed, which is exactly the distinction a
retrospective review turns on.

WHAT AN ACTION CANNOT DO
------------------------
Answering a question produces a fresh assessment which goes through the ratchet
like any other, so an answer can raise a band and cannot lower one. The single
exception is `override`, which is Phase 8's function unchanged: a nurse
identifier, a reason that survives validation, and acknowledgement of any
safety rule holding the floor.

This file also does not decide who to see next. The board deliberately presents
three lists rather than one blended ranking, and a workflow layer that answered
"who next?" would collapse them back into the single number Phase 12 refused to
produce -- on weights nobody agreed, with a clinical trade-off hidden inside.
The nurse chooses. We record what they chose.

SAFETY NOTE: policy values load from data/workflow_config.json and are
SIMULATED DEMONSTRATION SETTINGS.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from core.audit import AuditLog
from core.enums import TriageBand
from core.questions import QuestionValue, apply_answer
from core.ratchet import OverrideRejected, Ratchet
from core.schema import Assessment, Patient

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_CONFIG_FILE = REPO_ROOT / "data" / "workflow_config.json"


def _load(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return {k: v for k, v in json.load(fh).items() if not k.startswith("_")}


# Event names. Strings, for the reason core/audit.py gives: they are written to
# a file that outlives this code and a reader three years from now should not
# need our definitions to understand it.
PATIENT_SEEN = "patient_seen"
QUESTION_ANSWERED = "question_answered"
QUESTION_UNANSWERED = "question_unanswered"


class ActionRejected(ValueError):
    """Raised when an action is missing its attribution or makes no sense."""


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

@dataclass
class SeenRecord:
    """One clinician making contact with one patient."""

    patient_id: str
    at_minute: int
    actor_id: str
    band_when_seen: TriageBand
    waited_minutes: int
    note: str = ""


@dataclass
class ActionResult:
    """
    What an action did.

    Carries the resulting assessment when there is one, so a caller never has
    to re-run the pipeline to find out what changed -- re-running it would be a
    second opinion, and the whole point is that there is one.
    """

    action: str
    patient_id: str
    at_minute: int
    actor_id: str
    detail: str = ""
    patient: Optional[Patient] = None
    assessment: Optional[Assessment] = None
    previous_band: Optional[TriageBand] = None

    @property
    def band(self) -> Optional[TriageBand]:
        return self.assessment.band if self.assessment else None

    @property
    def escalated(self) -> bool:
        return (self.assessment is not None
                and self.previous_band is not None
                and self.assessment.band > self.previous_band)


# ---------------------------------------------------------------------------
# The workflow
# ---------------------------------------------------------------------------

class Workflow:
    """
    The only route by which a human changes anything.

    Stateful, like the ratchet, and for the same reason: remembering who has
    been seen is its job. It holds a ratchet and an audit log rather than
    duplicating them, so every band change a nurse causes goes through exactly
    the same mechanism an automated one does.
    """

    def __init__(self, engine, ratchet: Optional[Ratchet] = None,
                 audit: Optional[AuditLog] = None,
                 config: Optional[dict] = None):
        cfg = config or _load(WORKFLOW_CONFIG_FILE)
        self.engine = engine
        self.ratchet = ratchet or Ratchet(audit=audit)
        self.audit = audit if audit is not None else self.ratchet.audit
        self.policy = cfg["actions"]
        self.seen_policy = cfg["seen"]
        self.answer_policy = cfg["answers"]
        self.seen: Dict[str, SeenRecord] = {}
        self.actions: List[ActionResult] = []

    # -----------------------------------------------------------------------
    # Shared validation
    # -----------------------------------------------------------------------

    def _actor(self, actor_id: str, action: str) -> str:
        actor = (actor_id or "").strip()
        if self.policy["require_actor_id"] and not actor:
            raise ActionRejected(
                f"{action} must be attributable: no clinician identifier given")
        return actor

    def _record(self, result: ActionResult) -> ActionResult:
        self.actions.append(result)
        return result

    # -----------------------------------------------------------------------
    # Marking a patient seen
    # -----------------------------------------------------------------------

    def mark_seen(self, patient: Patient, assessment: Assessment,
                  actor_id: str, at_minute: int, note: str = "") -> ActionResult:
        """
        Record that a clinician made contact with this patient.

        The only thing that stops a patient's time-to-clinician clock, and
        there is deliberately no other caller anywhere in this repository that
        can produce this record. Grep for PATIENT_SEEN: it is written here and
        read by the board, and nothing automated reaches it.

        Marking a patient seen does NOT take them off the reassessment
        schedule. A patient who has been seen and is still waiting can still
        deteriorate, and treating "a nurse looked at them once" as "they are
        now somebody else's problem" is the exact failure this project is named
        after.
        """
        pid = patient.patient_id
        actor = self._actor(actor_id, "marking a patient seen")

        if pid in self.seen:
            previous = self.seen[pid]
            raise ActionRejected(
                f"{pid} was already seen at minute {previous.at_minute} by "
                f"{previous.actor_id}; a second contact is a new note, not a "
                f"correction to the first")

        record = SeenRecord(
            patient_id=pid,
            at_minute=at_minute,
            actor_id=actor,
            band_when_seen=assessment.band,
            waited_minutes=patient.wait_minutes(at_minute),
            note=note.strip(),
        )
        self.seen[pid] = record

        if self.audit is not None:
            self.audit.append(
                PATIENT_SEEN, pid, at_minute,
                {"band_when_seen": record.band_when_seen.word,
                 "waited_minutes": record.waited_minutes,
                 "note": record.note},
                actor)

        return self._record(ActionResult(
            action=PATIENT_SEEN, patient_id=pid, at_minute=at_minute,
            actor_id=actor, patient=patient, assessment=assessment,
            detail=(f"seen at {at_minute} after {record.waited_minutes} min "
                    f"at {record.band_when_seen.word}")))

    def is_seen(self, patient_id: str) -> bool:
        return patient_id in self.seen

    def seen_record(self, patient_id: str) -> Optional[SeenRecord]:
        return self.seen.get(patient_id)

    def time_to_seen(self) -> Dict[str, int]:
        """
        Minutes each seen patient waited before first clinical contact.

        Labelled precisely because the label is the honest part. This measures
        CONTACT. It is not a measure of care quality, of whether anything was
        done, or of whether the patient still needs something -- and a
        department optimising it can score perfectly by walking past people.
        """
        return {pid: record.waited_minutes for pid, record in self.seen.items()}

    # -----------------------------------------------------------------------
    # Answering a question
    # -----------------------------------------------------------------------

    def answer_question(self, patient: Patient, question_value: QuestionValue,
                        answer_label: str, actor_id: str,
                        at_minute: int) -> ActionResult:
        """
        Apply an answer, re-run the pipeline, and put the result through the
        ratchet.

        Note what this does NOT do: it does not trust the question engine's
        prediction of what the answer would produce. Phase 11 priced the
        question by running the pipeline on each hypothetical answer, and this
        runs it again on the real one. Displaying the earlier figure would mean
        the board could show a band that no assessment ever produced, and the
        two computations agreeing is worth more than the microseconds saved by
        reusing one.
        """
        pid = patient.patient_id
        actor = self._actor(actor_id, "answering a question")

        answer = next((a for a in question_value.question.answers
                       if a.label == answer_label), None)
        if answer is None:
            options = ", ".join(f"'{a.label}'"
                                for a in question_value.question.answers)
            raise ActionRejected(
                f"'{answer_label}' is not an answer to "
                f"{question_value.question.id}. Options: {options}")

        updated = apply_answer(patient, answer.effects, at_minute)
        previous = self.ratchet.band(pid)

        # ORDER MATTERS, and getting it wrong was the first version of this
        # method. Logging the assessment first produced a trail where P013's
        # band moved to PULL on an `ai_escalation` entry, and the nurse's
        # answer appeared underneath it -- so the log read as though the engine
        # had decided something and a human had agreed afterwards. The truth is
        # the reverse: a person elicited a fact, and the engine drew a
        # conclusion from it.
        #
        # The answer therefore goes in FIRST, and the band_transition the
        # ratchet writes follows it. Replaying the file in sequence now
        # reconstructs the actual causal chain, which is the property a
        # retrospective review depends on and the reason the log exists.
        if self.audit is not None:
            self.audit.append(
                QUESTION_ANSWERED, pid, at_minute,
                {"question_id": question_value.question.id,
                 "question": question_value.question.text,
                 "answer": answer.label,
                 "changed_record": not answer.is_non_answer,
                 "band_before": previous.word if previous else None},
                actor)

        assessment = self.ratchet.record(
            self.engine.assess(updated, now_minute=at_minute))

        return self._record(ActionResult(
            action=QUESTION_ANSWERED, patient_id=pid, at_minute=at_minute,
            actor_id=actor, patient=updated, assessment=assessment,
            previous_band=previous,
            detail=f"{question_value.question.id}: \"{answer.label}\""))

    def unable_to_answer(self, patient: Patient,
                         question_value: QuestionValue, actor_id: str,
                         at_minute: int, note: str = "") -> ActionResult:
        """
        Somebody tried and could not get an answer.

        A real outcome with no effect on the record. It keeps the uncertainty,
        keeps the question available to ask again, and resolves nothing. A
        workflow that only accepted answers would quietly push a clinician
        under time pressure toward guessing on the patient's behalf, and a
        guess entered as an answer is worse than a gap, because a gap is
        visible.
        """
        pid = patient.patient_id
        actor = self._actor(actor_id, "recording an unanswered question")

        if self.audit is not None:
            self.audit.append(
                QUESTION_UNANSWERED, pid, at_minute,
                {"question_id": question_value.question.id,
                 "question": question_value.question.text,
                 "note": note.strip()},
                actor)

        return self._record(ActionResult(
            action=QUESTION_UNANSWERED, patient_id=pid, at_minute=at_minute,
            actor_id=actor, patient=patient,
            detail=f"{question_value.question.id}: no answer obtained"))

    # -----------------------------------------------------------------------
    # Changing a band
    # -----------------------------------------------------------------------

    def override(self, assessment: Assessment, new_band: TriageBand,
                 reason: str, nurse_id: str,
                 acknowledged_rules: Optional[List[str]] = None) -> ActionResult:
        """
        A nurse changes the band. Phase 8's function, unchanged.

        Deliberately a thin delegate rather than a reimplementation. The
        validation, the rejected-reason list and the requirement to acknowledge
        a binding safety rule all live in core/ratchet.py, and a workflow layer
        that reimplemented any of it would be a second place for the rules to
        drift.
        """
        previous = self.ratchet.band(assessment.patient_id)
        transition = self.ratchet.nurse_override(
            assessment, new_band, reason, nurse_id, acknowledged_rules)
        return self._record(ActionResult(
            action="override_accepted", patient_id=assessment.patient_id,
            at_minute=assessment.at_minute, actor_id=transition.actor_id,
            assessment=assessment, previous_band=previous,
            detail=f"{transition.from_band.word} -> {transition.to_band.word}: "
                   f"{transition.reason}"))

    # -----------------------------------------------------------------------
    # Reading back
    # -----------------------------------------------------------------------

    def actions_by(self, actor_id: str) -> List[ActionResult]:
        return [a for a in self.actions if a.actor_id == actor_id]

    def actions_for(self, patient_id: str) -> List[ActionResult]:
        return [a for a in self.actions if a.patient_id == patient_id]

    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for action in self.actions:
            counts[action.action] = counts.get(action.action, 0) + 1
        return counts


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def explain_actions(workflow: Workflow, patient_id: str) -> str:
    records = workflow.actions_for(patient_id)
    if not records:
        return "    no clinician actions recorded"
    lines = []
    for action in records:
        line = (f"    t={action.at_minute:<5}{action.action:<20}"
                f"[{action.actor_id}]  {action.detail}")
        if action.escalated:
            line += f"   ESCALATED to {action.band.word}"
        lines.append(line)
    return "\n".join(lines)
