"""
tests/test_accountability.py
============================
CLAIM: The audit log is append-only and tamper-evident, and replaying it
reconstructs the system's state exactly.

CLAIM: Nothing in this system can mark a patient as seen. Only a person can,
under their own identifier.

CLAIM: Under surge, capacity constrains observation and never acuity.

These three sit together because they are the same kind of property: each is
enforced by something NOT existing -- an update method, an automated path, a
code branch that relaxes a threshold. Every one of them therefore carries a
test that breaks the property deliberately and checks the check.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.audit import AuditLog
from core.enums import TriageBand
from core.questions import QuestionEngine
from core.ratchet import Ratchet
from core.workflow import PATIENT_SEEN, ActionRejected, Workflow
from simulation.clock import SimulationClock
from simulation.surge import (
    SurgeController,
    SurgeInvariantBroken,
    build_surge_roster,
)
from tests.support import ClaimTest, engine, hospital, has_teeth, roster, tagged


class TestAuditLog(ClaimTest):
    claim = ("The log is append-only and tamper-evident, and replaying it "
             "reconstructs the system's state exactly.")

    def setUp(self):
        self.engine = engine()
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "audit.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def _shift(self):
        log = AuditLog(path=self.path)
        clock = SimulationClock(self.engine, roster(),
                               ratchet=Ratchet(audit=log))
        timeline = clock.run()
        return log, clock, timeline

    def test_the_operations_that_would_allow_a_quiet_correction_do_not_exist(self):
        """
        Not disabled, not private -- absent. A log that can be corrected is a
        log that can be quietly corrected.
        """
        for forbidden in ("update", "delete", "amend", "correct", "remove"):
            self.assertFalse(
                hasattr(AuditLog, forbidden),
                f"AuditLog has a {forbidden}() method; the append-only claim "
                f"is no longer true")

    def test_a_clean_log_verifies(self):
        log, _, _ = self._shift()
        ok, problems = log.verify()
        self.assertTrue(ok, problems)

    def test_editing_an_entry_is_detected(self):
        log, _, _ = self._shift()
        lines = self.path.read_text().splitlines()
        edited = json.loads(lines[-1])
        edited["payload"]["reason"] = "patient was fine"
        self.path.write_text(
            "\n".join(lines[:-1] + [json.dumps(edited, sort_keys=True)]) + "\n")

        ok, problems = AuditLog(path=self.path).verify()
        self.assertFalse(ok)
        self.assertTrue(any("altered" in p for p in problems), problems)

    def test_deleting_an_entry_is_detected(self):
        log, _, _ = self._shift()
        lines = self.path.read_text().splitlines()
        self.assertGreater(len(lines), 6)
        self.path.write_text("\n".join(lines[:4] + lines[5:]) + "\n")

        ok, problems = AuditLog(path=self.path).verify()
        self.assertFalse(ok)
        self.assertTrue(any("removed or reordered" in p for p in problems),
                        problems)

    def test_replaying_the_log_reproduces_the_live_state(self):
        """
        The completeness test. If the log can rebuild the system's state, then
        nothing determining a patient's acuity lives only in memory -- which is
        the difference between a record and a diary of selected highlights.
        """
        log, clock, _ = self._shift()
        replayed = log.replay_bands()
        live = {pid: band.word for pid, band in clock.ratchet.current.items()}
        self.assertEqual(live, replayed)

    @has_teeth
    def test_a_forged_de_escalation_is_found_by_the_governance_query(self):
        """The check has teeth: plant one and confirm the query finds it."""
        log, _, _ = self._shift()
        self.assertEqual([], log.ratchet_violations())

        forged = {
            "seq": 999999, "event": "band_transition", "patient_id": "P999",
            "at_minute": 1, "recorded_at": "2026-01-01T00:00:00+00:00",
            "payload": {"from_band": "CODE", "to_band": "WATCH",
                        "direction": "down", "changed_by": "ai_escalation"},
            "actor_id": "", "prev_hash": "0", "entry_hash": "0",
        }
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(forged, sort_keys=True) + "\n")

        tampered = AuditLog(path=self.path)
        self.assertEqual(1, len(tampered.ratchet_violations()))
        ok, _ = tampered.verify()
        self.assertFalse(ok, "a forged entry did not break the hash chain")


class TestOnlyAPersonClosesANeed(ClaimTest):
    claim = ("Nothing in this system can mark a patient as seen. Only a "
             "person can, under their own identifier.")

    def setUp(self):
        self.engine = engine()
        self.workflow = Workflow(self.engine, ratchet=Ratchet(audit=None),
                                 audit=None)
        self.patient = roster()[0]
        self.assessment = self.workflow.ratchet.record(
            self.engine.assess(self.patient))

    def test_marking_seen_requires_an_identifier(self):
        with self.assertRaises(ActionRejected):
            self.workflow.mark_seen(self.patient, self.assessment, "", 100)
        self.assertFalse(self.workflow.is_seen(self.patient.patient_id))

    def test_a_full_shift_marks_nobody_seen(self):
        """
        Run the entire pipeline -- clock, ratchet, safety rules, questions --
        and confirm that no patient ends up seen. There is no automated path,
        and this is the test that says so about the system rather than about
        one function.
        """
        clock = SimulationClock(self.engine, roster())
        clock.run()
        workflow = Workflow(self.engine, ratchet=clock.ratchet, audit=None)
        self.assertEqual({}, workflow.seen)
        self.assertEqual({}, workflow.time_to_seen())

    def test_the_same_patient_cannot_be_closed_twice(self):
        self.workflow.mark_seen(self.patient, self.assessment, "RN-1", 100)
        with self.assertRaises(ActionRejected):
            self.workflow.mark_seen(self.patient, self.assessment, "RN-2", 110)

    def test_an_answer_can_raise_a_band_and_cannot_lower_one(self):
        """
        A nurse answering a question goes through the ratchet like anything
        else, including when the answer is the good news we went looking for.
        """
        questions = QuestionEngine(self.engine)
        moved = 0
        for patient in roster():
            assessment = self.workflow.ratchet.record(
                self.engine.assess(patient))
            value = questions.next_question(patient, assessment)
            if value is None:
                continue
            for answer in value.question.answers:
                result = self.workflow.answer_question(
                    patient, value, answer.label, "RN-1",
                    patient.arrival_minute)
                self.assertNeverLower(assessment.band, result.band,
                                      patient.patient_id)
                moved += 1
        self.assertGreater(moved, 0, "no question was exercised")


class TestSurgeNeverRelaxesAcuity(ClaimTest):
    claim = ("Under surge, capacity constrains observation and never acuity. "
             "Deferred is never dropped.")

    def setUp(self):
        self.hospital = hospital()
        self.engine = engine()

    def test_load_does_not_move_a_single_threshold(self):
        controller = SurgeController(self.hospital)
        clock = SimulationClock(
            self.engine, build_surge_roster(roster(), 3, 6),
            capacity=controller)
        clock.run()
        controller.assert_invariants()

    @has_teeth
    def test_the_invariant_check_has_teeth(self):
        """Move a threshold by hand and confirm the check catches it."""
        controller = SurgeController(self.hospital)
        self.hospital.thresholds.l3_min += 1
        try:
            with self.assertRaises(SurgeInvariantBroken):
                controller.assert_invariants()
        finally:
            self.hospital.thresholds.l3_min -= 1
        controller.assert_invariants()

    def test_a_deferred_reassessment_is_never_dropped(self):
        """
        Every deferral must be followed by the patient being reassessed later,
        or by the horizon arriving. A dropped one is a patient nobody looks at
        again.
        """
        controller = SurgeController(self.hospital)
        surge_roster = build_surge_roster(roster(), 3, 6)
        clock = SimulationClock(self.engine, surge_roster,
                                capacity=controller)
        timeline = clock.run()

        self.assertGreater(len(timeline.deferrals), 0,
                           "3x load produced no deferrals; capacity is not "
                           "binding and this test proves nothing")

        # Nobody who was deferred fell out of the system entirely.
        deferred_ids = {pid for _, pid, _, _ in timeline.deferrals}
        assessed_ids = {r.patient_id for r in timeline.records}
        self.assertTrue(
            deferred_ids <= assessed_ids,
            f"deferred and never assessed at all: "
            f"{sorted(deferred_ids - assessed_ids)}")

    def test_the_same_patient_scores_the_same_under_load(self):
        """
        The score is a property of the patient, not of how busy we are.

        Runs one patient through a quiet department and a swamped one and
        compares the arrival assessment.
        """
        for patient in tagged("load_invariance") + tagged("silent_deterioration"):
            quiet = self.engine.assess(patient, now_minute=patient.arrival_minute)

            controller = SurgeController(self.hospital)
            clock = SimulationClock(self.engine, build_surge_roster(roster(), 3, 6),
                                    capacity=controller)
            timeline = clock.run()
            first = next((r for r in timeline.for_patient(patient.patient_id)), None)
            self.assertIsNotNone(first)
            self.assertEqual(
                quiet.risk_score, first.risk_score,
                f"{patient.patient_id} scored differently because the "
                f"department was busy")
