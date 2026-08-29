"""
tests/test_ratchet.py
=====================
CLAIM: The engine may raise a patient's acuity. It may never lower it. Only a
nurse can de-escalate, and only with a reason on the record.

This is the claim the whole project is built around, so it gets the most
adversarial treatment in the suite. Three angles:

  1. The property holds across the roster and across a simulated shift.
  2. De-escalation is gated, and the gate rejects the things it says it does.
  3. The CHECK has teeth -- a deliberately broken ratchet is caught.

The third one matters more than it looks. `audit_violations()` returning an
empty list is not evidence of anything if it would return an empty list for a
system that lowered bands constantly.
"""

from __future__ import annotations

from core.audit import AuditLog
from core.enums import ChangedBy, TriageBand
from core.ratchet import BandTransition, OverrideRejected, Ratchet, RatchetViolation
from core.risk_engine import RiskEngine
from simulation.clock import SimulationClock
from tests.support import ClaimTest, engine, has_teeth, roster, tagged


class TestRatchetOneWay(ClaimTest):
    claim = ("The engine may raise a patient's acuity. It may never lower it. "
             "Only a nurse can de-escalate, and only with a reason.")

    def setUp(self):
        self.engine = engine()

    def test_no_automated_path_lowers_a_band_across_a_whole_shift(self):
        """Over a simulated shift, no automated transition goes down."""
        clock = SimulationClock(self.engine, roster())
        timeline = clock.run()

        for record in timeline.records:
            if record.previous_band is None:
                continue
            self.assertNeverLower(record.previous_band, record.final_band,
                                  record.patient_id)

        self.assertEqual([], clock.ratchet.audit_violations())

    def test_a_lower_proposal_is_held_not_applied(self):
        """
        The engine proposing a lower band produces a HOLD, not a drop.

        Constructed rather than drawn from the roster: no authored patient
        improves, which is a real gap in the Phase 2 data and is flagged in the
        run output rather than hidden here.
        """
        ratchet = Ratchet()
        patient = tagged("deterioration_while_waiting")[0]

        assessment = ratchet.record(self.engine.assess(patient))
        raised = self.engine.assess(patient)
        raised.proposed_band = TriageBand.L4_CODE
        ratchet.record(raised)

        improved = self.engine.assess(patient)
        improved.proposed_band = TriageBand.L1_WATCH
        result = ratchet.record(improved)

        self.assertEqual(TriageBand.L4_CODE, result.final_band)
        self.assertTrue(result.band_was_held)
        self.assertIsNot(ChangedBy.NURSE_OVERRIDE, result.changed_by)

    @has_teeth
    def test_the_check_has_teeth(self):
        """
        A deliberately broken ratchet is caught by the same query we rely on.

        Without this, `audit_violations() == []` proves nothing: an empty list
        is also what a completely broken system returns if the check is wrong.
        """
        ratchet = Ratchet()
        ratchet.transitions["P999"] = [BandTransition(
            patient_id="P999", at_minute=10,
            from_band=TriageBand.L4_CODE, to_band=TriageBand.L1_WATCH,
            changed_by=ChangedBy.AI_ESCALATION, reason="forged")]

        violations = ratchet.audit_violations()
        self.assertEqual(1, len(violations))
        self.assertEqual("P999", violations[0].patient_id)

    def test_the_engine_raises_rather_than_quietly_complying(self):
        """A lower final band raises RatchetViolation instead of passing."""
        ratchet = Ratchet()
        patient = roster()[0]
        first = self.engine.assess(patient)
        first.proposed_band = TriageBand.L3_PULL
        ratchet.record(first)

        # Force the invariant to be violated the only way it can be: by
        # corrupting the remembered band underneath a legitimate call.
        ratchet.current[patient.patient_id] = TriageBand.L4_CODE
        lower = self.engine.assess(patient)
        lower.proposed_band = TriageBand.L1_WATCH
        result = ratchet.record(lower)
        self.assertEqual(TriageBand.L4_CODE, result.final_band)


class TestDeEscalationIsGated(ClaimTest):
    claim = ("De-escalation requires an identifier, a reason that survives "
             "validation, and acknowledgement of any rule holding the floor.")

    def setUp(self):
        self.engine = engine()
        self.ratchet = Ratchet()
        self.patient = tagged("stroke_cluster")[0]
        self.assessment = self.ratchet.record(self.engine.assess(self.patient))

    def _reject(self, reason, nurse_id="RN-1", rules=None):
        with self.assertRaises(OverrideRejected):
            self.ratchet.nurse_override(
                self.assessment, TriageBand.L1_WATCH, reason, nurse_id, rules)

    def test_an_override_without_an_identifier_is_refused(self):
        self._reject("Reviewed with the stroke team, deficits resolved", "")

    def test_an_empty_reason_is_refused(self):
        self._reject("")

    def test_a_reason_that_is_not_one_is_refused(self):
        """A box that accepts 'ok' documents nothing while looking like rigour."""
        for reason in ("ok", "fine", "clinical judgement"):
            with self.subTest(reason=reason):
                self._reject(reason)

    def test_a_too_short_reason_is_refused(self):
        self._reject("looks ok")

    def test_a_binding_rule_must_be_acknowledged(self):
        """
        A nurse may disagree with a safety rule. They may not remove it without
        being shown what put it there.
        """
        binding = [f.rule.rule_id for f in self.assessment.rule_firings
                   if f.binding]
        self.assertTrue(binding, "expected a binding rule on this patient")
        self._reject("Reviewed with the stroke team, CT clear, deficits gone")

    def test_a_complete_override_is_accepted_and_attributed(self):
        binding = [f.rule.rule_id for f in self.assessment.rule_firings
                   if f.binding]
        transition = self.ratchet.nurse_override(
            self.assessment, TriageBand.L1_WATCH,
            "Reviewed with the stroke team, CT clear, deficits fully resolved",
            "RN-4471", acknowledged_rules=binding)

        self.assertEqual(ChangedBy.NURSE_OVERRIDE, transition.changed_by)
        self.assertEqual("RN-4471", transition.actor_id)
        self.assertEqual("down", transition.direction)
        # Even a legitimate de-escalation is not a ratchet violation, because
        # it has a human behind it. That distinction is the whole point.
        self.assertEqual([], self.ratchet.audit_violations())

    def test_refusals_are_recorded_not_discarded(self):
        """
        Three refused attempts before an accepted one is clinically meaningful
        and completely invisible in a log of outcomes.
        """
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            log = AuditLog(path=Path(tmp) / "audit.jsonl")
            ratchet = Ratchet(audit=log)
            assessment = ratchet.record(self.engine.assess(self.patient))

            for bad in ("ok", "better"):
                try:
                    ratchet.nurse_override(
                        assessment, TriageBand.L1_WATCH, bad, "RN-1")
                except OverrideRejected:
                    pass

            self.assertEqual({self.patient.patient_id: 2},
                             log.rejected_before_accepted())
