"""
core/ratchet.py
===============
The Ratchet Engine. The claim this whole project is built around, and the
smallest file that delivers it.

    The engine may RAISE a patient's acuity.
    The engine may NEVER lower it.
    Only a nurse can de-escalate, and only with a reason on the record.

WHY THIS IS A MECHANISM AND NOT A PROMISE
-----------------------------------------
Every triage system says it is safety-biased. The claim is usually implemented
as a weighting: risk is scored a bit higher, thresholds are set a bit lower,
and the model is free to move a patient in either direction as its inputs
change. That is not asymmetry, it is a thumb on the scale, and a sufficiently
confident model will still walk a deteriorating patient back down.

Here the asymmetry is structural. `apply()` computes

    final = max(proposed, previous)

for every automated path through this file. There is no branch, no flag and no
configuration value that lets the engine produce a lower band. De-escalation
exists in exactly one function, `nurse_override()`, which will not run without
a human identifier and a reason that survives validation.

The mechanism costs something and it is worth being honest about what. A
patient who genuinely improves stays at their old band until a human agrees
they have improved. In a busy department that means the queue carries acuity
that reality has moved past. We think that is the right side to be wrong on --
the failure mode of the alternative is a patient quietly walked down the ladder
by a machine, and that failure mode kills people while this one wastes a
nurse's time. But it IS a cost, it is not free, and a department adopting this
should adopt it knowing that.

WHY ONLY DE-ESCALATIONS NEED A TYPED REASON
-------------------------------------------
An escalation already carries its justification. The contribution trace, the
confidence panel and any rule firing are all on the record, and making a nurse
type an explanation for the machine's own decision would be theatre.

A de-escalation is the opposite: it overrides evidence the system has recorded,
so the person overriding it supplies the missing piece. That is why the reason
requirement points one way, and why `rejected_reasons` exists -- a free-text
box that accepts "fine" has documented nothing while looking like
accountability, which is worse than documenting nothing at all.

WHAT THIS FILE DOES NOT DO
--------------------------
  * No persistence. Band history lives in memory. Phase 9 replaces it with an
    append-only audit log, and the BandTransition records below are already
    shaped for that.
  * No clock. Phase 10 drives re-assessment; this file only ever compares a new
    assessment against what it was told last time.

SAFETY NOTE: policy values load from data/ratchet_config.json and are SIMULATED
DEMONSTRATION SETTINGS, not a clinical governance standard.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from core.enums import ChangedBy, TriageBand
from core.schema import Assessment

REPO_ROOT = Path(__file__).resolve().parent.parent
RATCHET_CONFIG_FILE = REPO_ROOT / "data" / "ratchet_config.json"


def _load(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return {k: v for k, v in json.load(fh).items() if not k.startswith("_")}


class RatchetViolation(RuntimeError):
    """
    Raised when something tries to lower a band through an automated path.

    This should be unreachable. It exists because "unreachable" is a claim, and
    a claim about safety behaviour is worth more when the code will crash
    rather than quietly comply if it turns out to be wrong.
    """


class OverrideRejected(ValueError):
    """Raised when a nurse override is missing an identifier or a real reason."""


# ---------------------------------------------------------------------------
# Transition records
# ---------------------------------------------------------------------------

@dataclass
class BandTransition:
    """
    One acuity change, with its author. Shaped for the Phase 9 audit log.

    `changed_by` is not decoration. It is the field that makes the ratchet
    auditable after the fact: any transition where the band went DOWN and
    `changed_by` is not NURSE_OVERRIDE is a bug in this file, and that is a
    query a governance team can run over the log without reading our code.
    """

    patient_id: str
    at_minute: int
    from_band: Optional[TriageBand]
    to_band: TriageBand
    changed_by: ChangedBy
    reason: str = ""
    actor_id: str = ""
    acknowledged_rules: List[str] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)

    @property
    def author_label(self) -> str:
        """
        Display name for who set this band.

        A hold is not authored by anyone -- the engine asked for a change and
        did not get one -- so it is labelled as the ratchet rather than
        inheriting an attribution that would misdescribe what happened.
        """
        if self.direction == "held" and any(f.startswith("held:") for f in self.flags):
            return "ratchet_held"
        return str(self.changed_by)

    @property
    def direction(self) -> str:
        if self.from_band is None:
            return "initial"
        if self.to_band > self.from_band:
            return "up"
        if self.to_band < self.from_band:
            return "down"
        return "held"

    def __str__(self) -> str:
        arrow = (f"{self.from_band.word} -> {self.to_band.word}"
                 if self.from_band else f"{self.to_band.word}")
        line = f"t={self.at_minute:<4} {arrow:<18} {self.author_label}"
        if self.actor_id:
            line += f" [{self.actor_id}]"
        if self.reason:
            line += f"  \"{self.reason}\""
        return line


# ---------------------------------------------------------------------------
# The ratchet
# ---------------------------------------------------------------------------

class Ratchet:
    """
    Holds the current band for each patient and enforces the one-way rule.

    Stateful, unlike every other engine in core/. That is deliberate and it is
    the only stateful component: the ratchet's entire job is to remember what a
    patient's acuity already was, and a stateless version of it would be a
    contradiction in terms.
    """

    def __init__(self, config: Optional[dict] = None):
        cfg = config or _load(RATCHET_CONFIG_FILE)
        self.policy = cfg["override"]
        self._rejected = {r.strip().lower()
                          for r in self.policy["rejected_reasons"]}
        self.current: Dict[str, TriageBand] = {}
        self.transitions: Dict[str, List[BandTransition]] = {}

    # -----------------------------------------------------------------------
    # The automated path
    # -----------------------------------------------------------------------

    def record(self, assessment: Assessment) -> Assessment:
        """
        Apply the ratchet to a fresh assessment and return it with `final_band`
        set.

        Three outcomes, and only three:
          * first sighting        -> final = proposed, SYSTEM_INITIAL
          * proposed is higher    -> final = proposed, AI_ESCALATION
          * proposed is lower or
            the same              -> final = previous, band HELD
        """
        pid = assessment.patient_id
        previous = self.current.get(pid)
        proposed = assessment.proposed_band
        if proposed is None:
            raise RatchetViolation(
                f"{pid}: assessment has no proposed_band to ratchet")

        assessment.previous_band = previous

        if previous is None:
            final, changed_by, reason = proposed, ChangedBy.SYSTEM_INITIAL, ""
        elif proposed > previous:
            final = proposed
            changed_by = ChangedBy.AI_ESCALATION
            reason = self._escalation_reason(assessment)
        else:
            # THE RATCHET. A lower proposal and an equal proposal are handled
            # identically, because the engine's opinion that a patient has
            # improved carries no authority here. Nothing new is authored by a
            # hold, so the band keeps the attribution it already had.
            final = previous
            changed_by = ChangedBy.SYSTEM_INITIAL
            reason = ("" if proposed == previous else
                      f"engine proposed {proposed.word}; held at "
                      f"{previous.word} pending nurse review")

        if previous is not None and final < previous:
            raise RatchetViolation(
                f"{pid}: automated path produced {final.word} from "
                f"{previous.word} -- the ratchet has been broken")

        assessment.final_band = final
        assessment.changed_by = changed_by
        assessment.change_reason = reason
        self.current[pid] = final

        transition = BandTransition(
            patient_id=pid,
            at_minute=assessment.at_minute,
            from_band=previous,
            to_band=final,
            changed_by=changed_by,
            reason=reason,
        )
        if proposed < final:
            transition.flags.append(
                f"held: engine proposed {proposed.word}")
        self.transitions.setdefault(pid, []).append(transition)
        return assessment

    @staticmethod
    def _escalation_reason(assessment: Assessment) -> str:
        """The machine explains itself; the nurse does not have to."""
        if assessment.floor_reason:
            return assessment.floor_reason
        drivers = [c for c in assessment.contributions if c.points > 0]
        if drivers:
            return f"risk {assessment.risk_score:.0f}: {drivers[0].label}"
        return f"risk {assessment.risk_score:.0f}"

    # -----------------------------------------------------------------------
    # The only path that can lower a band
    # -----------------------------------------------------------------------

    def nurse_override(
        self,
        assessment: Assessment,
        new_band: TriageBand,
        reason: str,
        nurse_id: str,
        acknowledged_rules: Optional[List[str]] = None,
    ) -> BandTransition:
        """
        A human decision, recorded under their name.

        Nurses may move a band in either direction. Lowering one is the only
        operation in this system that can reduce a patient's acuity at all, and
        it is gated on three things: an identifier, a reason that survives
        validation, and acknowledgement of any safety rule currently holding
        the floor.

        The nurse is not blocked from disagreeing with a rule. A Phase 7 floor
        is a floor for the machine, not for a clinician. What they cannot do is
        remove one without being shown what put it there.
        """
        pid = assessment.patient_id
        current = self.current.get(pid, assessment.proposed_band)
        going_down = new_band < current
        acknowledged = list(acknowledged_rules or [])

        if self.policy["require_nurse_id"] and not nurse_id.strip():
            raise OverrideRejected(
                "an override must be attributable: no nurse identifier given")

        if going_down and self.policy["require_reason_on_deescalation"]:
            self._validate_reason(reason)

            binding = [f.rule.rule_id for f in assessment.rule_firings
                       if f.binding]
            if binding and self.policy["must_acknowledge_binding_rules"]:
                missing = [r for r in binding if r not in acknowledged]
                if missing:
                    raise OverrideRejected(
                        f"band is held by {', '.join(missing)}; an override "
                        f"must acknowledge the rule it is removing")

        flags = []
        if (going_down and self.policy["flag_multi_band_drops"]
                and current - new_band > 1):
            flags.append(
                f"multi-band drop ({current.word} -> {new_band.word}), "
                f"flagged for review")

        transition = BandTransition(
            patient_id=pid,
            at_minute=assessment.at_minute,
            from_band=current,
            to_band=new_band,
            changed_by=ChangedBy.NURSE_OVERRIDE,
            reason=reason.strip(),
            actor_id=nurse_id.strip(),
            acknowledged_rules=acknowledged,
            flags=flags,
        )

        assessment.previous_band = current
        assessment.final_band = new_band
        assessment.changed_by = ChangedBy.NURSE_OVERRIDE
        assessment.change_reason = reason.strip()
        self.current[pid] = new_band
        self.transitions.setdefault(pid, []).append(transition)
        return transition

    def _validate_reason(self, reason: str) -> None:
        text = (reason or "").strip()
        if not text:
            raise OverrideRejected(
                "lowering a band requires a reason on the record")
        if text.lower() in self._rejected:
            raise OverrideRejected(
                f"'{text}' is not a reason. A record that says this documents "
                f"nothing while looking like accountability")
        if len(text) < int(self.policy["min_reason_chars"]):
            raise OverrideRejected(
                f"reason is {len(text)} characters; policy requires at least "
                f"{self.policy['min_reason_chars']}")

    # -----------------------------------------------------------------------
    # Reading back
    # -----------------------------------------------------------------------

    def history(self, patient_id: str) -> List[BandTransition]:
        return self.transitions.get(patient_id, [])

    def band(self, patient_id: str) -> Optional[TriageBand]:
        return self.current.get(patient_id)

    def audit_violations(self) -> List[BandTransition]:
        """
        Every transition that lowered a band without a nurse behind it.

        This should always return an empty list. It exists so the property can
        be CHECKED rather than trusted, including over a log this code did not
        produce -- which is the form a governance team actually needs.
        """
        out = []
        for records in self.transitions.values():
            for t in records:
                if (t.direction == "down"
                        and t.changed_by is not ChangedBy.NURSE_OVERRIDE):
                    out.append(t)
        return out


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def explain_history(ratchet: Ratchet, patient_id: str) -> str:
    records = ratchet.history(patient_id)
    if not records:
        return "    no acuity history recorded"
    lines = []
    for t in records:
        lines.append(f"    {t}")
        for flag in t.flags:
            lines.append(f"        ! {flag}")
        for rule_id in t.acknowledged_rules:
            lines.append(f"        acknowledged: {rule_id}")
    return "\n".join(lines)
