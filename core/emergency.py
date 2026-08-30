"""
core/emergency.py
=================
The emergency gate. It runs before the scoring pipeline, on every fragment of
speech and every observation, and it can interrupt everything downstream.

WHY IT IS SEPARATE FROM THE RISK ENGINE
---------------------------------------
The risk engine is careful. It weighs signals against each other, caps
correlated domains so a breathless patient is not counted six times, and
produces a number that survives argument. All of that takes a complete picture,
and completeness takes time.

A patient saying "I can't breathe and I'm going to pass out" has not given us a
complete picture. They have given us a reason to stop assessing and get
somebody. Waiting for the rest of the intake to produce a score that would have
said the same thing is a design that kills people slowly and defensibly.

So the gate does not score. It matches, it records, and it interrupts. It runs
on the fragment as it arrives rather than on the finished assessment, and its
output is a state change, not a number.

WHAT THIS FILE GOT WRONG, AND WHY IT IS BUILT DIFFERENTLY NOW
-------------------------------------------------------------
The first version was ten phrase groups, all of them medical: cardiac,
respiratory, neurological, allergic, overdose. A patient said

    "I have been in a fatal car accident, my friend is dead and my leg is
     amputated due to the accident"

and this file returned nothing. The system scored zero and called it NORMAL.

That is not a missing phrase. It is a missing IDEA. The patient described what
HAPPENED TO THEM, not what they were feeling, and a gate built entirely out of
symptom language had nothing to match against. Adding "amputated" to the list
would fix that sentence and leave the next one broken, because enumerating the
emergencies somebody thought of on a Tuesday is a record of that afternoon, not
a safety net.

FOUR LAYERS, AND WHY NONE CAN SILENCE ANOTHER
---------------------------------------------
  * MODEL: a language model judges whether what was said may be
    life-threatening, with no list involved. PRIMARY whenever a key is
    configured, because this is the only layer that can handle a sentence
    nobody anticipated.
  * MECHANISM: catastrophic EVENTS and catastrophic INJURIES, by category
    rather than by body system -- trauma, penetrating injury, limb loss, burns,
    head and spine, drowning, a death at the scene. This is the layer the
    car-accident case needed.
  * SPOKEN: the phrases most likely to arrive verbatim. A floor, not the roof.
  * OBSERVED: objective values past a critical threshold.

Any layer fires alone. `evaluate` takes the UNION. A model that is down, wrong,
or talked into something degrades detection to the rule floor and cannot remove
anything, which is the Ratchet's asymmetry applied to the gate.

The model may only ADD. There is no code path by which a model clears a rule
trigger, and `EmergencyGate.evaluate` takes the union rather than a consensus.
A model that is down, wrong or talked into something degrades detection to the
rule floor. It cannot remove anything. This is the Ratchet's asymmetry applied
to the gate: the automated layers can escalate and cannot stand down.

WHAT IT DOES NOT CLAIM
----------------------
It does not detect heart attacks, strokes or cardiac arrest, and nothing in
this file or the interface says it does. It detects PHRASES AND VALUES that may
indicate a life-threatening situation, and it escalates them to a human. A
patient who says "I'm having a heart attack" has said something that must reach
a nurse in seconds whether or not it turns out to be true, and that is the
entire claim.

DELIBERATELY OVER-SENSITIVE
---------------------------
A false emergency costs a nurse thirty seconds. A missed one is the failure
this project exists to reduce. Every trigger records the exact phrase and the
timestamp, so dismissal is one click and the false-positive rate is measurable
rather than assumed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
EMERGENCY_CONFIG = REPO_ROOT / "data" / "emergency_config.json"


def _load(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return {k: v for k, v in json.load(fh).items() if not k.startswith("_")}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S")


@dataclass
class Trigger:
    """One reason the gate fired, with everything needed to dismiss it."""

    trigger_id: str
    layer: str                  # spoken | observed | combination | model | multimodal
    why: str
    evidence: str = ""
    at_second: float = 0.0
    at_clock: str = field(default_factory=_now)
    dismissed_by: str = ""
    dismiss_reason: str = ""

    @property
    def active(self) -> bool:
        return not self.dismissed_by

    def as_dict(self) -> dict:
        return {
            "trigger_id": self.trigger_id, "layer": self.layer, "why": self.why,
            "evidence": self.evidence, "at_second": self.at_second,
            "at_clock": self.at_clock, "dismissed_by": self.dismissed_by,
            "dismiss_reason": self.dismiss_reason, "active": self.active,
        }


@dataclass
class EmergencyState:
    active: bool = False
    triggers: List[Trigger] = field(default_factory=list)
    declared_at: str = ""
    acknowledged_by: str = ""
    acknowledged_at: str = ""

    @property
    def active_triggers(self) -> List[Trigger]:
        return [t for t in self.triggers if t.active]

    def as_dict(self) -> dict:
        return {
            "active": self.active,
            "declared_at": self.declared_at,
            "acknowledged_by": self.acknowledged_by,
            "acknowledged_at": self.acknowledged_at,
            "triggers": [t.as_dict() for t in self.triggers],
            "active_count": len(self.active_triggers),
        }


class EmergencyGate:
    """
    Stateless matcher over one fragment plus the current observation set.

    The state lives in the session, not here, so the gate can be run over a
    transcript in a test without a session and gives the same answer.
    """

    def __init__(self, config: Optional[dict] = None):
        self.cfg = config or _load(EMERGENCY_CONFIG)
        self.settings = self.cfg.get("settings", {})

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _normalise(text: str) -> str:
        t = (text or "").lower().replace("'", "").replace("\u2019", "")
        t = re.sub(r"[^a-z0-9\s]", " ", t)
        return " " + re.sub(r"\s+", " ", t).strip() + " "

    @staticmethod
    def _quote(text: str, phrase: str) -> str:
        """The patient's words around the match, for the nurse to read."""
        at = text.find(phrase)
        if at == -1:
            return phrase
        lo, hi = max(0, at - 40), min(len(text), at + len(phrase) + 40)
        while lo > 0 and text[lo] != " ":
            lo -= 1
        while hi < len(text) and text[hi] != " ":
            hi += 1
        return text[lo:hi].strip()

    # -- layer 1: the patient's own words ----------------------------------

    def spoken(self, text: str, at_second: float = 0.0) -> List[Trigger]:
        norm = self._normalise(text)
        out: List[Trigger] = []
        for rule in self.cfg.get("spoken_triggers", []):
            for phrase in rule["phrases"]:
                if phrase in norm:
                    out.append(Trigger(
                        trigger_id=rule["id"], layer="spoken", why=rule["why"],
                        evidence=self._quote(norm, phrase).strip(),
                        at_second=at_second))
                    break
        return out

    # -- layer 1b: catastrophic mechanism ----------------------------------

    def mechanism(self, text: str, at_second: float = 0.0) -> List[Trigger]:
        """
        What happened to the patient, rather than what they are feeling.

        The layer the fatal-car-accident case needed. A patient describing an
        amputation, a stabbing, a fall from height or a death at the scene has
        told a triage system everything it needs, and none of it is a symptom.
        """
        norm = self._normalise(text)
        out: List[Trigger] = []
        for rule in self.cfg.get("mechanism_triggers", []):
            for phrase in rule["phrases"]:
                if phrase in norm:
                    out.append(Trigger(
                        trigger_id=rule["id"], layer="mechanism",
                        why=rule["why"],
                        evidence=self._quote(norm, phrase).strip(),
                        at_second=at_second))
                    break
        return out

    def severity_language(self, text: str, at_second: float = 0.0) -> List[Trigger]:
        """
        The patient describing something at the top of their own scale.

        An intensifier alone means nothing -- "severe traffic" is not an
        emergency. Paired with a body part or a symptom word in the same
        clause it means the patient is telling us this is the worst they have
        had, and discarding that because the exact phrase was not on a list is
        the failure this file already made once.
        """
        norm = self._normalise(text)
        words = self.cfg.get("severity_words", [])
        anchors = ("pain", "hurt", "bleed", "breath", "chest", "head", "burn",
                   "injur", "wound", "accident", "crash", "fall", "cut",
                   "leg", "arm", "back", "stomach", "blood")
        for word in words:
            at = norm.find(" " + word)
            if at == -1:
                continue
            window = norm[max(0, at - 70):at + 70]
            if any(anchor in window for anchor in anchors):
                return [Trigger(
                    "S1_extreme_severity_language", "severity",
                    "patient describes this at the top of their own scale",
                    self._quote(norm, word).strip(), at_second)]
        return []

    # -- layer 2: objective observations -----------------------------------

    def observed(self, observations: Dict, at_second: float = 0.0) -> List[Trigger]:
        out: List[Trigger] = []
        for rule in self.cfg.get("observed_triggers", []):
            value = observations.get(rule["field"])
            if value in (None, ""):
                continue
            fired = False
            detail = ""
            if "values" in rule:
                if str(value) in rule["values"]:
                    fired, detail = True, f"{rule['field']} = {value}"
            else:
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    continue
                if "below" in rule and number < rule["below"]:
                    fired, detail = True, f"{rule['field']} {number:g} below {rule['below']}"
                elif "above" in rule and number > rule["above"]:
                    fired, detail = True, f"{rule['field']} {number:g} above {rule['above']}"
            if fired:
                out.append(Trigger(rule["id"], "observed", rule["why"],
                                   detail, at_second))
        return out

    # -- layer 3: clusters --------------------------------------------------

    def combinations(self, facts: Dict, at_second: float = 0.0) -> List[Trigger]:
        """
        Patterns that are unremarkable apart and serious together.

        `facts` is a flat set of booleans assembled by the session: symptom
        terms present, plus derived flags such as facial_acute_change.
        """
        out: List[Trigger] = []
        for rule in self.cfg.get("combination_triggers", []):
            needed = rule["requires"]
            if all(facts.get(key) for key in needed):
                out.append(Trigger(rule["id"], "combination", rule["why"],
                                   " + ".join(needed), at_second))
        return out

    # -- layer 4: what a model flagged --------------------------------------

    def from_model(self, phrases: List[dict], at_second: float = 0.0) -> List[Trigger]:
        """
        Phrases a language provider flagged as potentially life-threatening.

        Additive only. These never clear a rule trigger, and the gate is
        equally functional with this layer returning nothing forever.
        """
        out: List[Trigger] = []
        for i, item in enumerate(phrases or []):
            phrase = str(item.get("phrase", "")).strip()
            if not phrase:
                continue
            out.append(Trigger(
                trigger_id=f"M{i + 1}_model_flagged", layer="model",
                why=str(item.get("why", "flagged by the language model")),
                evidence=phrase, at_second=at_second))
        return out

    # -- layer 5: the multimodal contradiction ------------------------------

    def contradiction(self, said_fine: bool, visible_distress: bool,
                      voice_distress: bool, at_second: float = 0.0
                      ) -> List[Trigger]:
        """
        The patient says they are fine and the observations disagree.

        Not an emergency on its own. It IS a reason to look, and it is the case
        most easily lost by a system that takes self-report at face value. It
        fires only when both observed channels disagree with the statement,
        which keeps it rare enough to mean something.
        """
        if said_fine and visible_distress and voice_distress:
            return [Trigger(
                "X1_verbal_visual_conflict", "multimodal",
                "patient states they are fine while both observed channels "
                "indicate distress",
                "self-report contradicts visible and audible observation",
                at_second)]
        return []

    # -- the gate ----------------------------------------------------------

    def evaluate(self, text: str = "", observations: Optional[Dict] = None,
                 facts: Optional[Dict] = None,
                 model_phrases: Optional[List[dict]] = None,
                 said_fine: bool = False, visible_distress: bool = False,
                 voice_distress: bool = False,
                 at_second: float = 0.0) -> List[Trigger]:
        """
        Every layer, unioned.

        Union rather than consensus, on purpose. Requiring two layers to agree
        would mean a patient saying "I can't breathe" in a room with no
        monitoring does not trigger, and that is the exact patient this is for.
        """
        found: List[Trigger] = []
        found += self.spoken(text, at_second)
        found += self.mechanism(text, at_second)
        found += self.severity_language(text, at_second)
        found += self.observed(observations or {}, at_second)
        found += self.combinations(facts or {}, at_second)
        found += self.from_model(model_phrases or [], at_second)
        found += self.contradiction(said_fine, visible_distress, voice_distress,
                                    at_second)

        # One trigger per id per evaluation. The same phrase repeated in one
        # utterance is one reason, not three.
        seen, unique = set(), []
        for trigger in found:
            if trigger.trigger_id not in seen:
                seen.add(trigger.trigger_id)
                unique.append(trigger)
        return unique

    # -- policy ------------------------------------------------------------

    @property
    def stops_routine_questions(self) -> bool:
        return bool(self.settings.get("stop_routine_questions", True))

    @property
    def patient_message(self) -> str:
        return self.settings.get(
            "patient_message",
            "We are getting someone to you now. Please stay where you are.")


def summarise(state: EmergencyState) -> str:
    """A nurse-facing summary of why the gate fired. Plain text, for the notes."""
    if not state.active:
        return "No emergency triggers fired."
    lines = [f"EMERGENCY declared {state.declared_at}", ""]
    for trigger in state.triggers:
        mark = " " if trigger.active else "x"
        lines.append(f"  [{mark}] {trigger.at_clock}  {trigger.trigger_id}"
                     f"  ({trigger.layer})")
        lines.append(f"        {trigger.why}")
        if trigger.evidence:
            lines.append(f"        heard: \"{trigger.evidence}\"")
        if trigger.dismissed_by:
            lines.append(f"        dismissed by {trigger.dismissed_by}: "
                         f"{trigger.dismiss_reason}")
    lines.append("")
    lines.append("  This prototype does not diagnose. It identifies phrases and")
    lines.append("  values that may indicate a life-threatening situation and")
    lines.append("  escalates them for clinical attention.")
    return "\n".join(lines)


__all__ = ["EmergencyGate", "EmergencyState", "Trigger", "summarise"]
