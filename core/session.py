"""
core/session.py
===============
The single source of truth for one live encounter.

WHY ONE OBJECT AND NOT TWO PAGES
--------------------------------
The obvious way to build a patient screen and a nurse screen is to build two
screens. Each holds what it needs, each talks to the engine, and they agree
because they are looking at the same patient.

They do not agree. They agree for about ninety seconds, and then a nurse raises
a severity while a transcript fragment arrives, and the two screens hold
different answers with no way to say which is right. In a spreadsheet that is
an inconvenience. In triage it is two people acting on different information
about the same patient.

So there is one ClinicSession per encounter. Both screens are views of it.
Neither holds state, neither computes anything, and every change -- a spoken
fragment, an observation, a nurse override -- goes through a method here and
comes back out as an event both screens receive.

THE TIMELINE IS THE RECORD
--------------------------
Every method appends an Event before it returns. The current state is a
projection of that list, not a thing maintained alongside it, so the audit
trail cannot disagree with the screen. When a nurse asks why a patient became
an emergency at 14:32:18, the answer is a row, with the phrase that caused it.

TEMPORAL ALIGNMENT
------------------
Speech and vision arrive on different schedules: a transcript fragment when
somebody finishes a sentence, a visual observation whenever a scan completes.
Comparing them as they arrive compares things that happened at different
moments, which is how a system concludes that a patient who said "I'm fine" at
0:12 was distressed, when the distress was recorded at 0:47 in response to a
different question.

So every event carries `at_second` on one clock, and cross-modal reasoning
happens over a WINDOW (`MULTIMODAL_WINDOW_SECONDS`). Two signals are treated as
contradicting each other only when they are close enough in time to be about
the same moment.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from core.ai.provider import Extraction, ExtractedSymptom
from core.config import HospitalConfig
from core.emergency import EmergencyGate, EmergencyState, Trigger, summarise
from core.enums import TriageBand
from core.intake_bridge import build_patient
from core.ratchet import Ratchet
from core.risk_engine import RiskEngine, explain
from core.safety_rules import explain_rules
from core.uncertainty import explain_confidence

MULTIMODAL_WINDOW_SECONDS = 12.0

STATUS_ORDER = ["normal", "monitoring", "concerning", "high risk", "emergency"]

BAND_TO_STATUS = {
    TriageBand.L1_WATCH: "normal",
    TriageBand.L2_LOOK: "monitoring",
    TriageBand.L3_PULL: "concerning",
    TriageBand.L4_CODE: "high risk",
}

_ids = itertools.count(1)


def _clock() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

@dataclass
class Event:
    kind: str
    text: str
    at_second: float = 0.0
    at_clock: str = field(default_factory=_clock)
    detail: Dict = field(default_factory=dict)
    actor: str = "system"
    event_id: int = field(default_factory=lambda: next(_ids))

    def as_dict(self) -> dict:
        return {
            "event_id": self.event_id, "kind": self.kind, "text": self.text,
            "at_second": round(self.at_second, 1), "at_clock": self.at_clock,
            "detail": self.detail, "actor": self.actor,
        }


@dataclass
class LedgerEntry:
    """
    One symptom as the session holds it: what the AI found, what the nurse did.

    `ai_severity` and `nurse_severity` are kept apart rather than overwritten.
    A nurse raising a severity has made a clinical judgement and the record
    should show both what the machine said and what the human decided, because
    the pair is what makes the override auditable and what makes disagreement
    measurable later.
    """

    term: str
    normalised: str = ""
    said: str = ""
    ai_severity: Optional[int] = None
    nurse_severity: Optional[int] = None
    onset: str = ""
    duration_hours: Optional[float] = None
    location: str = ""
    laterality: str = ""
    progression: str = ""
    associated: List[str] = field(default_factory=list)
    confidence: float = 0.5
    source: str = "speech"
    first_at_second: float = 0.0
    first_at_clock: str = field(default_factory=_clock)
    negated: bool = False
    uncertain: bool = False
    removed_by: str = ""
    added_by: str = ""
    provider: str = ""

    @property
    def severity(self) -> Optional[int]:
        return self.nurse_severity if self.nurse_severity is not None else self.ai_severity

    @property
    def active(self) -> bool:
        return not self.removed_by

    @property
    def overridden(self) -> bool:
        return (self.nurse_severity is not None
                and self.nurse_severity != self.ai_severity)

    def as_dict(self) -> dict:
        return {
            "term": self.term, "normalised": self.normalised or self.term,
            "said": self.said, "ai_severity": self.ai_severity,
            "nurse_severity": self.nurse_severity, "severity": self.severity,
            "onset": self.onset, "duration_hours": self.duration_hours,
            "location": self.location, "laterality": self.laterality,
            "progression": self.progression, "associated": self.associated,
            "confidence": round(self.confidence, 2), "source": self.source,
            "first_at_second": round(self.first_at_second, 1),
            "first_at_clock": self.first_at_clock, "negated": self.negated,
            "uncertain": self.uncertain, "removed_by": self.removed_by,
            "added_by": self.added_by, "provider": self.provider,
            "active": self.active, "overridden": self.overridden,
        }


# ---------------------------------------------------------------------------
# The session
# ---------------------------------------------------------------------------

class ClinicSession:
    def __init__(self, session_id: str, provider, hospital: str = "medium_ed"):
        self.session_id = session_id
        self.provider = provider
        self.hospital = HospitalConfig.load(hospital)
        self.engine = RiskEngine(self.hospital)
        self.ratchet = Ratchet()
        self.gate = EmergencyGate()

        self.events: List[Event] = []
        self.ledger: Dict[str, LedgerEntry] = {}
        self.denials: Dict[str, LedgerEntry] = {}
        self.concerns: List[dict] = []
        self.baseline_hints: List[dict] = []
        self.visual_observations: List[dict] = []
        self.audio_observations: List[dict] = []
        self.emergency = EmergencyState()
        # A contradiction between what is said and what is observed is not an
        # emergency and must not be routed like one. See _check_contradiction.
        self.review_flags: List[dict] = []

        self.transcript: List[dict] = []
        self.demographics = {"age_years": 40, "sex": "unspecified",
                             "history_tier": "zero"}
        self.observations: Dict = {}
        self.flags: Dict = {}
        self.questions_asked: List[dict] = []
        # What the model suggested asking next, if a model served this turn.
        self.model_question: str = ""
        self.pending_question: Optional[dict] = None
        self.reported_pain: Optional[int] = None
        self.complete: bool = False
        self.completed_reason: str = ""
        self.nurse_notes_override = ""
        self.last_result: Dict = {}
        self.degraded = False
        self.degraded_reason = ""

        self._subscribers: List[Callable[[dict], None]] = []
        self._log("session", "Encounter opened", detail={"session": session_id})

    # -- pub/sub -----------------------------------------------------------

    def subscribe(self, callback: Callable[[dict], None]) -> Callable[[], None]:
        self._subscribers.append(callback)
        return lambda: self._subscribers.remove(callback) \
            if callback in self._subscribers else None

    def _broadcast(self) -> None:
        """
        Push the whole state to every listener.

        A delta protocol would be leaner. It would also be a second place where
        the two screens can disagree, and the entire reason this class exists is
        that they must not. The payload is a few kilobytes and there are two
        clients.
        """
        snapshot = self.snapshot()
        for callback in list(self._subscribers):
            try:
                callback(snapshot)
            except Exception:                               # noqa: BLE001
                pass

    def _log(self, kind: str, text: str, at_second: float = 0.0,
             detail: Optional[Dict] = None, actor: str = "system") -> Event:
        event = Event(kind, text, at_second, detail=detail or {}, actor=actor)
        self.events.append(event)
        return event

    # -- inputs ------------------------------------------------------------

    def set_demographics(self, **kwargs) -> None:
        self.demographics.update({k: v for k, v in kwargs.items() if v not in ("", None)})
        self._broadcast()

    def hear(self, text: str, at_second: float = 0.0,
             source: str = "speech") -> dict:
        """
        A fragment of speech or typed text.

        The order is the safety architecture: the emergency gate runs on the
        raw words FIRST, before extraction, before scoring, before anything
        that could fail or take time. A patient saying "I can't breathe" does
        not wait for a model round trip.
        """
        text = (text or "").strip()
        if not text:
            return self.snapshot()

        self.transcript.append({"text": text, "at_second": at_second,
                                "at_clock": _clock(), "source": source})
        self._log("speech", text, at_second, actor="patient")
        self._attach_answer(text, at_second)

        # 1. GATE FIRST, on the raw words, by rule, with no dependencies.
        #    Spoken phrases, catastrophic mechanism and extreme severity
        #    language all run here, before extraction, before scoring. The
        #    mechanism layer is why "my leg is amputated" now reaches a nurse:
        #    it is not a symptom and the symptom-shaped version of this gate
        #    was blind to it.
        self._fire_triggers(self.gate.spoken(text, at_second))
        self._fire_triggers(self.gate.mechanism(text, at_second))
        self._fire_triggers(self.gate.severity_language(text, at_second))

        # 2. Extraction. May be slow, may be degraded, may fail.
        context = {
            "at_second": at_second, "source": source,
            "said_so_far": " ".join(t["text"] for t in self.transcript[:-1]),
        }
        extraction = self.provider.extract(text, context)
        self.degraded = bool(extraction.degraded)
        # WHY it degraded, not just that it did. A DEGRADED badge with no
        # reason tells a nurse the system is worse without telling them what to
        # do about it, and tells whoever set the key up nothing at all.
        self.degraded_reason = extraction.note if extraction.degraded else ""
        self.model_question = getattr(extraction, "next_question", "") or ""
        self._absorb(extraction, at_second)

        # 3. Anything the model flagged that the rules did not. Additive only.
        self._fire_triggers(self.gate.from_model(extraction.emergency_phrases,
                                                 at_second))

        # 4. Clusters, over everything known so far.
        self._fire_triggers(self.gate.combinations(self._facts(), at_second))

        # 5. Multimodal contradiction, inside the time window.
        self._check_contradiction(text, at_second)

        return self.reassess(at_second)

    def observe_visual(self, kind: str, description: str, at_second: float = 0.0,
                       measurements: Optional[Dict] = None) -> dict:
        """
        Something the camera saw, recorded as an observation and never as a
        diagnosis. "Visible linear scar on the left forearm", not a condition.
        """
        entry = {
            "id": f"V{len(self.visual_observations) + 1}", "kind": kind,
            "description": description, "at_second": at_second,
            "at_clock": _clock(), "measurements": measurements or {},
            "status": "unreviewed", "nurse_note": "",
        }
        self.visual_observations.append(entry)
        self._log("visual", description, at_second,
                  detail={"kind": kind, "id": entry["id"]})
        self._broadcast()
        return entry

    def observe_audio(self, kind: str, description: str, at_second: float = 0.0,
                      measurements: Optional[Dict] = None) -> dict:
        entry = {
            "id": f"A{len(self.audio_observations) + 1}", "kind": kind,
            "description": description, "at_second": at_second,
            "at_clock": _clock(), "measurements": measurements or {},
            "status": "unreviewed", "nurse_note": "",
        }
        self.audio_observations.append(entry)
        self._log("audio", description, at_second, detail={"kind": kind})
        self._broadcast()
        return entry

    def set_capture(self, facial: Optional[str] = None,
                    voice: Optional[str] = None) -> dict:
        """
        Record whether the camera and microphone actually ran.

        The uncertainty engine said "facial capture not attempted" on every
        single encounter, including ones where the patient had the camera on
        the whole time, because nothing ever told the engine otherwise. A
        confidence penalty for a missing modality is correct; charging it while
        the modality is running is a lie in the audit trail.
        """
        if facial:
            self.flags["facial_capture_status"] = facial
        if voice:
            self.flags["voice_capture_status"] = voice
        return self.reassess(self._current_second())

    def set_observations(self, **kwargs) -> dict:
        """Vitals and observed flags. Runs the objective emergency layer."""
        for key, value in kwargs.items():
            if value in ("", None):
                continue
            if key in ("consciousness", "spo2", "respiratory_rate",
                       "heart_rate", "systolic_bp", "diastolic_bp",
                       "temperature_c"):
                self.observations[key] = value
            else:
                self.flags[key] = value
        self._fire_triggers(self.gate.observed(self.observations,
                                               self._current_second()))
        return self.reassess(self._current_second())

    # -- absorbing an extraction -------------------------------------------

    def _absorb(self, extraction: Extraction, at_second: float) -> None:
        # A pain score can arrive in any utterance, usually the one answering
        # "how bad is it". The first version only read it alongside a symptom
        # in the same sentence, so a patient who said "about an 8 out of 10"
        # in reply to a question was still recorded as "pain score not
        # obtained" -- the system asked, was answered, and did not listen.
        if extraction.pain_score is not None:
            self.reported_pain = extraction.pain_score
            for entry in self.ledger.values():
                if entry.active and entry.ai_severity is None:
                    entry.ai_severity = extraction.pain_score
            self._log("pain", f"pain reported as {extraction.pain_score}/10",
                      at_second, actor="patient")

        for symptom in extraction.symptoms:
            self._add_symptom(symptom, extraction.provider, at_second)
        for denial in extraction.denials:
            if denial.term in self.ledger and self.ledger[denial.term].active:
                # Reported earlier, denied now. Both stay: the contradiction is
                # the finding, and resolving it by picking a winner is how a
                # system loses the fact that it happened.
                self._log("conflict",
                          f"denied '{denial.term}' after reporting it earlier",
                          at_second, detail={"term": denial.term,
                                             "said": denial.said})
                continue
            entry = self.denials.get(denial.term) or LedgerEntry(
                term=denial.term, normalised=denial.normalised or denial.term,
                said=denial.said, negated=True, confidence=denial.confidence,
                source=denial.source, first_at_second=at_second,
                provider=extraction.provider)
            self.denials[denial.term] = entry
            self._log("denial", f"denies {denial.term}", at_second,
                      detail={"said": denial.said})

        for concern in extraction.concerns:
            if not any(c.get("concern") == concern.get("concern")
                       for c in self.concerns):
                self.concerns.append(concern)
                self._log("concern",
                          concern.get("label", concern.get("concern", "")),
                          at_second, detail=concern)
        for hint in extraction.baseline_hints:
            if not any(h.get("hint") == hint.get("hint")
                       for h in self.baseline_hints):
                self.baseline_hints.append(hint)
                self._log("baseline_hint",
                          f"patient's words mention: {hint.get('hint')}",
                          at_second, detail=hint)

    def _add_symptom(self, symptom: ExtractedSymptom, provider: str,
                     at_second: float) -> None:
        existing = self.ledger.get(symptom.term)
        if existing and existing.active:
            # Enrich rather than replace. A second mention usually adds detail
            # (an onset, a progression) and overwriting loses the first
            # description, which is often the better one.
            for attr in ("onset", "location", "laterality", "progression"):
                if not getattr(existing, attr) and getattr(symptom, attr):
                    setattr(existing, attr, getattr(symptom, attr))
            if existing.duration_hours is None:
                existing.duration_hours = symptom.duration_hours
            if existing.ai_severity is None:
                existing.ai_severity = symptom.severity_score
            existing.confidence = max(existing.confidence, symptom.confidence)
            return

        entry = LedgerEntry(
            term=symptom.term, normalised=symptom.normalised or symptom.term,
            said=symptom.said, ai_severity=symptom.severity_score,
            onset=symptom.onset, duration_hours=symptom.duration_hours,
            location=symptom.location, laterality=symptom.laterality,
            progression=symptom.progression, associated=list(symptom.associated),
            confidence=symptom.confidence, source=symptom.source,
            first_at_second=at_second, uncertain=symptom.uncertain,
            provider=provider)
        self.ledger[symptom.term] = entry
        self._log("symptom", f"{entry.normalised} detected", at_second,
                  detail=entry.as_dict())

    # -- emergency ---------------------------------------------------------

    def _fire_triggers(self, triggers: List[Trigger]) -> None:
        fresh = [t for t in triggers
                 if not any(x.trigger_id == t.trigger_id
                            for x in self.emergency.triggers)]
        if not fresh:
            return
        self.emergency.triggers.extend(fresh)
        for trigger in fresh:
            self._log("emergency_trigger",
                      f"{trigger.trigger_id}: {trigger.why}",
                      trigger.at_second, detail=trigger.as_dict())
        if not self.emergency.active and self.emergency.active_triggers:
            self.emergency.active = True
            self.emergency.declared_at = _clock()
            self._log("emergency", "EMERGENCY declared; routine questioning "
                                   "stopped", self._current_second(),
                      detail={"reasons": [t.trigger_id
                                          for t in self.emergency.active_triggers]})

    def _check_contradiction(self, text: str, at_second: float) -> None:
        """
        Self-report against observation, inside the time window.

        Both observed channels must disagree, and both must have been recorded
        within MULTIMODAL_WINDOW_SECONDS of the statement. A visual observation
        from forty seconds ago is not evidence about what the patient just said.
        """
        norm = text.lower()
        said_fine = any(p in norm for p in (
            "im fine", "i'm fine", "i am fine", "nothing is wrong",
            "im okay", "i'm okay", "i am okay", "im alright", "i'm alright",
            "no problem", "im completely fine", "i'm completely fine"))
        if not said_fine:
            return

        def recent(entries) -> bool:
            return any(abs(e["at_second"] - at_second) <= MULTIMODAL_WINDOW_SECONDS
                       and e.get("status") != "dismissed" for e in entries)

        visible = recent([e for e in self.visual_observations
                          if e["kind"] in ("distress", "grimacing", "discomfort")])
        audible = recent([e for e in self.audio_observations
                          if e["kind"] in ("distress", "breathlessness", "strain")])

        if not (visible or audible):
            return

        self._log("contradiction",
                  "patient states they are fine; observed channels disagree",
                  at_second,
                  detail={"visual": visible, "audio": audible,
                          "window_seconds": MULTIMODAL_WINDOW_SECONDS,
                          "statement": text})

        # REVIEW, NOT EMERGENCY.
        #
        # The first version of this routed a contradiction through the
        # emergency gate, and the scenario run showed what that costs: a
        # patient who says "I'm fine" while grimacing gets a resus alert. Do
        # that a few times a shift and the alert stops meaning anything, which
        # is how alarm fatigue is built and how the one real emergency gets
        # scrolled past.
        #
        # So it raises a review flag: prominent on the nurse screen, invisible
        # to the patient, and it does NOT stop questioning -- a patient
        # underplaying their symptoms is exactly who there is more to ask.
        for flag in self.gate.contradiction(said_fine, visible, audible, at_second):
            if any(f["flag_id"] == flag.trigger_id for f in self.review_flags):
                continue
            self.review_flags.append({
                "flag_id": flag.trigger_id, "why": flag.why,
                "evidence": flag.evidence, "statement": text,
                "at_second": at_second, "at_clock": flag.at_clock,
                "visual": visible, "audio": audible,
                "status": "unreviewed", "nurse_note": "",
            })
            self._log("review_flag", flag.why, at_second,
                      detail={"flag_id": flag.trigger_id, "statement": text})

    # -- questions ---------------------------------------------------------

    def record_question(self, question_id: str, text: str, why: str = "") -> dict:
        """
        Mark a question as ASKED at the moment it reaches the patient.

        This did not exist, and its absence was the whole broken loop:
        `questions_asked` was read when choosing the next question and written
        nowhere, so the same question was chosen forever. A patient answered,
        the transcript logged it, and the screen never moved.
        """
        if any(q["id"] == question_id for q in self.questions_asked):
            return self.snapshot()
        entry = {"id": question_id, "text": text, "why": why,
                 "at_clock": _clock(), "at_second": self._current_second(),
                 "answer": "", "answered_at_second": None}
        self.questions_asked.append(entry)
        self.pending_question = entry
        self._log("question", text, entry["at_second"],
                  detail={"id": question_id, "why": why})
        self._broadcast()
        return self.snapshot()

    def _attach_answer(self, text: str, at_second: float) -> None:
        """
        The next thing a patient says after a question is its answer.

        Crude, and right often enough to be worth having: it puts the question
        and the reply next to each other in the notes instead of leaving a
        nurse to work out which sentence answered what.
        """
        if not self.pending_question or self.pending_question["answer"]:
            return
        self.pending_question["answer"] = text
        self.pending_question["answered_at_second"] = at_second
        self._log("answer", f"answered '{self.pending_question['text'][:48]}'",
                  at_second, actor="patient",
                  detail={"id": self.pending_question["id"], "answer": text})
        self.pending_question = None

    def finish(self, reason: str = "no further question would change the "
                                   "assessment") -> dict:
        """
        End the intake.

        An assessment that never ends is not an assessment. The previous
        version had no terminal state at all: once the question engine ran out,
        the patient sat looking at a screen that had stopped responding without
        saying so.
        """
        if not self.complete:
            self.complete = True
            self.completed_reason = reason
            self._log("session", f"intake complete: {reason}",
                      self._current_second())
            self._broadcast()
        return self.snapshot()

    # -- nurse actions -----------------------------------------------------

    def nurse_set_severity(self, term: str, severity: Optional[int],
                           nurse: str = "nurse") -> dict:
        entry = self.ledger.get(term)
        if not entry:
            return self.snapshot()
        before = entry.severity
        entry.nurse_severity = severity
        self._log("override", f"{term} severity {before} -> {severity}",
                  self._current_second(), actor=nurse,
                  detail={"term": term, "from": before, "to": severity})
        return self.reassess(self._current_second())

    def nurse_remove_symptom(self, term: str, reason: str = "",
                             nurse: str = "nurse") -> dict:
        entry = self.ledger.get(term)
        if not entry:
            return self.snapshot()
        entry.removed_by = nurse
        self._log("override", f"removed {term}: {reason or 'no reason given'}",
                  self._current_second(), actor=nurse,
                  detail={"term": term, "reason": reason})
        return self.reassess(self._current_second())

    def nurse_add_symptom(self, term: str, severity: Optional[int] = None,
                          note: str = "", nurse: str = "nurse") -> dict:
        entry = self.ledger.get(term)
        if entry:
            entry.removed_by = ""
            entry.nurse_severity = severity
        else:
            self.ledger[term] = LedgerEntry(
                term=term, normalised=term, said=note, nurse_severity=severity,
                confidence=1.0, source="nurse", added_by=nurse,
                first_at_second=self._current_second(), provider="nurse")
        self._log("override", f"added {term}", self._current_second(),
                  actor=nurse, detail={"term": term, "note": note})
        return self.reassess(self._current_second())

    def nurse_review_observation(self, observation_id: str, status: str,
                                 note: str = "", nurse: str = "nurse") -> dict:
        for pool in (self.visual_observations, self.audio_observations):
            for entry in pool:
                if entry["id"] == observation_id:
                    entry["status"] = status
                    entry["nurse_note"] = note
                    self._log("override",
                              f"observation {observation_id} {status}",
                              self._current_second(), actor=nurse,
                              detail={"id": observation_id, "note": note})
                    self._broadcast()
                    return self.snapshot()
        return self.snapshot()

    def nurse_review_flag(self, flag_id: str, status: str, note: str = "",
                          nurse: str = "nurse") -> dict:
        for flag in self.review_flags:
            if flag["flag_id"] == flag_id:
                flag["status"] = status
                flag["nurse_note"] = note
                self._log("override", f"review flag {flag_id} {status}",
                          self._current_second(), actor=nurse,
                          detail={"flag_id": flag_id, "note": note})
        self._broadcast()
        return self.snapshot()

    def nurse_acknowledge_emergency(self, nurse: str = "nurse") -> dict:
        self.emergency.acknowledged_by = nurse
        self.emergency.acknowledged_at = _clock()
        self._log("override", "emergency acknowledged", self._current_second(),
                  actor=nurse)
        self._broadcast()
        return self.snapshot()

    def nurse_dismiss_trigger(self, trigger_id: str, reason: str,
                              nurse: str = "nurse") -> dict:
        """
        A human clearing a trigger, with a reason. The only way one clears.

        Nothing automated dismisses a trigger, which is the same asymmetry the
        Ratchet applies to bands. If every trigger is dismissed the emergency
        state stands down, and the event log keeps the whole exchange.
        """
        for trigger in self.emergency.triggers:
            if trigger.trigger_id == trigger_id and trigger.active:
                trigger.dismissed_by = nurse
                trigger.dismiss_reason = reason
                self._log("override",
                          f"dismissed {trigger_id}: {reason}",
                          self._current_second(), actor=nurse,
                          detail={"trigger_id": trigger_id, "reason": reason})
        if self.emergency.active and not self.emergency.active_triggers:
            self.emergency.active = False
            self._log("emergency", "emergency stood down by nurse; all triggers "
                                   "dismissed", self._current_second(), actor=nurse)
        return self.reassess(self._current_second())

    def nurse_set_notes(self, text: str, nurse: str = "nurse") -> dict:
        self.nurse_notes_override = text
        self._log("override", "clinical notes edited",
                  self._current_second(), actor=nurse)
        self._broadcast()
        return self.snapshot()

    # -- scoring -----------------------------------------------------------

    def _facts(self) -> Dict:
        facts = {term: True for term, e in self.ledger.items() if e.active}
        facts["facial_acute_change"] = self.flags.get("change_reported_as_new") == "yes"
        facts["speech_abnormality"] = self.flags.get("speech_abnormality") == "yes"
        facts["unilateral_weakness"] = (
            self.flags.get("unilateral_weakness") == "yes"
            or "unilateral weakness" in facts)
        facts["pallor_or_sweating"] = (
            self.flags.get("skin_pallor_or_cyanosis") == "yes"
            or "sweating" in facts)
        return facts

    def _current_second(self) -> float:
        return self.transcript[-1]["at_second"] if self.transcript else 0.0

    def _payload(self) -> dict:
        """Assemble the intake payload the existing engine already understands."""
        active = [e for e in self.ledger.values() if e.active]
        severities = [e.severity for e in active if e.severity is not None]
        payload = {
            "patient_id": self.session_id,
            "age_years": self.demographics.get("age_years", 40),
            "sex": self.demographics.get("sex", "unspecified"),
            "history_tier": self.demographics.get("history_tier", "zero"),
            "arrival_minute": 0,
            "chief_complaint": (self.transcript[0]["text"][:140]
                                if self.transcript else ""),
            "added_symptoms": [e.term for e in active],
            "denied_symptoms": [t for t in self.denials
                                if t not in {e.term for e in active}],
            "added_concerns": [c.get("concern") for c in self.concerns
                               if c.get("concern")],
            "pain_score": (max(severities) if severities
                           else self.reported_pain),
            "transcript": "",     # already extracted; do not double-count
            "typed_symptoms": "",
        }
        payload.update(self.observations)
        payload.update(self.flags)
        return payload

    def reassess(self, at_second: float = 0.0) -> dict:
        from core.intake_bridge import serialise
        patient = build_patient(self._payload())
        assessment = self.engine.assess(patient, now_minute=0)

        # AN OPEN GATE FLOORS THE BAND.
        #
        # Without this the console showed "risk 0/100, L1 WATCH, status
        # EMERGENCY" for a patient describing an amputation, which is
        # architecturally correct -- the gate is independent of the score, and
        # the score is right that no scoreable symptom was mentioned -- and
        # completely indefensible on a screen a nurse is reading in seconds.
        #
        # The floor is applied the same way every other hard rule is applied:
        # it can only RAISE, it is recorded by name, and the score underneath
        # is left alone rather than being inflated to justify the band. The
        # panel still says 0, because 0 is what the symptoms came to. The band
        # says CODE, because somebody has told us their leg is off.
        if self.emergency.active and (assessment.proposed_band is None
                                      or assessment.proposed_band < TriageBand.L4_CODE):
            reasons = ", ".join(t.trigger_id for t in self.emergency.active_triggers)
            assessment.proposed_band = TriageBand.L4_CODE
            assessment.safety_rules_fired.append(
                f"EMERGENCY_GATE -> floor CODE (BINDING): {reasons}")

        assessment = self.ratchet.record(assessment)
        result = serialise(patient, assessment, self.hospital)

        previous = self.last_result.get("band_code")
        self.last_result = result
        if previous and previous != result["band_code"]:
            self._log("band", f"{previous} -> {result['band_code']} "
                              f"{result['band_word']}", at_second,
                      detail={"risk": result["risk_score"],
                              "reason": result.get("change_reason", "")})
        self._broadcast()
        return self.snapshot()

    # -- status ------------------------------------------------------------

    @property
    def status(self) -> str:
        if self.emergency.active:
            return "emergency"
        band = self.last_result.get("band_word", "")
        return {"WATCH": "normal", "LOOK": "monitoring",
                "PULL": "concerning", "CODE": "high risk"}.get(band, "normal")

    @property
    def routine_questions_allowed(self) -> bool:
        """
        False the moment an emergency is live.

        Requirement nine of the brief, and the reason the gate sits above the
        question engine rather than beside it. Asking somebody who has just
        said they cannot breathe how long their headache has lasted is not
        thorough; it is the system failing to notice.
        """
        return not (self.emergency.active and self.gate.stops_routine_questions)

    def snapshot(self) -> dict:
        return {
            "session_id": self.session_id,
            "status": self.status,
            "emergency": self.emergency.as_dict(),
            "routine_questions_allowed": self.routine_questions_allowed,
            "patient_message": (self.gate.patient_message
                                if self.emergency.active else ""),
            "symptoms": [e.as_dict() for e in self.ledger.values()],
            "denials": [e.as_dict() for e in self.denials.values()],
            "concerns": list(self.concerns),
            "baseline_hints": list(self.baseline_hints),
            "review_flags": list(self.review_flags),
            "visual_observations": list(self.visual_observations),
            "audio_observations": list(self.audio_observations),
            "transcript": list(self.transcript),
            "timeline": [e.as_dict() for e in self.events],
            "assessment": self.last_result,
            "demographics": dict(self.demographics),
            "observations": dict(self.observations),
            "flags": dict(self.flags),
            "questions_asked": list(self.questions_asked),
            "model_question": self.model_question,
            "complete": self.complete,
            "completed_reason": self.completed_reason,
            "provider": self.provider.describe(),
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason,
            "emergency_summary": summarise(self.emergency),
        }
