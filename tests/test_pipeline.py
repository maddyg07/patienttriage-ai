"""
tests/test_pipeline.py
======================
CLAIM: The score is not computed and then explained. It is computed BY building
the explanation, so the two cannot drift apart.

CLAIM: Waiting does not make a patient sicker.

CLAIM: The same patient state always produces the same result.

Plus the KNOWN GAPS section at the bottom: properties this project has implied
and does not currently hold. They are pinned with `expectedFailure` rather than
deleted, so they appear in every run as a gap and so the suite tells us if one
is ever fixed or ever gets worse. A suite that only contains things that pass
is a suite that has stopped looking.
"""

from __future__ import annotations

import copy
import unittest

from core.enums import CaptureStatus
from simulation.clock import SimulationClock
from tests.support import ClaimTest, engine, roster, tagged


class TestExplanationIsTheCalculation(ClaimTest):
    claim = ("The score is computed by building the explanation, so the two "
             "cannot drift apart.")

    def setUp(self):
        self.engine = engine()

    def test_the_panel_sums_to_the_score(self):
        for patient in roster():
            assessment = self.engine.assess(patient)
            total = sum(c.points for c in assessment.contributions)
            expected = max(0.0, min(100.0, total))
            self.assertAlmostEqual(
                expected, assessment.risk_score, places=6,
                msg=f"{patient.patient_id}: the explanation does not add up to "
                    f"the score. There is a post-hoc attribution layer.")

    def test_every_contribution_is_labelled_and_placed(self):
        for patient in roster():
            for c in self.engine.assess(patient).contributions:
                self.assertTrue(c.label.strip(),
                                f"{patient.patient_id}: unlabelled contribution")
                self.assertTrue(c.domain.strip(),
                                f"{patient.patient_id}: {c.label} has no domain")

    def test_a_domain_cap_is_visible_as_its_own_line(self):
        """
        When a cap bites it appears in the panel, so the arithmetic a nurse can
        see still reconciles. A cap applied silently would make the explanation
        wrong by exactly the amount it removed.
        """
        capped = 0
        for patient in roster():
            assessment = self.engine.assess(patient)
            for note in assessment.cap_notes:
                if not note.startswith("domain_cap:"):
                    continue
                capped += 1
                domain = note.split(":", 1)[1].split()[0]
                self.assertTrue(
                    any(c.source == "cap" and c.domain == domain
                        for c in assessment.contributions),
                    f"{patient.patient_id}: {domain} was capped and no line "
                    f"in the panel says so")
        self.assertGreater(capped, 0, "no domain cap fired anywhere")

    def test_the_band_never_falls_below_the_score_band(self):
        """Rules and uncertainty can raise a band. Neither can lower one."""
        for patient in roster():
            assessment = self.engine.assess(patient)
            score_band = self.engine.hospital.thresholds.band_for_score(
                assessment.risk_score)
            self.assertNeverLower(score_band, assessment.band,
                                  patient.patient_id)


class TestWaitingDoesNotMakeAPatientSicker(ClaimTest):
    claim = ("Waiting does not raise a patient's score. It lowers our "
             "confidence, because what ages is our picture, not the patient.")

    def setUp(self):
        self.engine = engine()

    def test_the_same_state_scores_the_same_two_hours_later(self):
        for patient in roster():
            early = self.engine.assess(patient, now_minute=patient.arrival_minute)
            late = self.engine.assess(patient, now_minute=patient.arrival_minute + 120)
            self.assertEqual(
                early.risk_score, late.risk_score,
                f"{patient.patient_id}: the score moved purely from waiting")

    def test_waiting_costs_confidence(self):
        for patient in roster():
            if patient.vitals.measured_at_minute is None:
                continue
            early = self.engine.assess(patient, now_minute=patient.arrival_minute)
            late = self.engine.assess(patient, now_minute=patient.arrival_minute + 120)
            self.assertLess(
                late.confidence, early.confidence,
                f"{patient.patient_id}: two hours passed and we are just as "
                f"confident")

    def test_the_control_patient_never_moves(self):
        """
        Without a patient who stays put, an escalation proves nothing: a system
        that escalated everyone who waited long enough would look identical.
        """
        for patient in tagged("no_spurious_escalation"):
            clock = SimulationClock(self.engine, roster())
            timeline = clock.run()
            records = timeline.for_patient(patient.patient_id)
            self.assertGreater(len(records), 1,
                               f"{patient.patient_id} was never re-assessed")
            bands = {r.final_band for r in records}
            self.assertEqual(
                1, len(bands),
                f"{patient.patient_id} is the control and moved: {bands}")

    def test_nothing_in_the_engine_reads_waiting_time(self):
        """
        Structural, not behavioural. `overdue_by` is display-only, and the
        check is that no engine calls it -- a behavioural test would pass on a
        system that read it and happened not to use it yet.
        """
        from pathlib import Path

        repo = Path(__file__).resolve().parent.parent
        offenders = []
        for path in list((repo / "core").glob("*.py")) + \
                list((repo / "simulation").glob("*.py")):
            if path.name == "config.py":
                continue        # defines it; does not consume it
            text = path.read_text(encoding="utf-8")
            if "overdue_by(" in text:
                offenders.append(path.name)
        self.assertEqual(
            [], offenders,
            f"waiting time is read inside the engine by {offenders}. It is "
            f"meant to be displayed to a person, never scored.")

        # simulation/surge.py does read care_target_for(), and that read is
        # legitimate and worth being explicit about rather than silently
        # excluding: it snapshots the targets in order to ASSERT they have not
        # moved under load. Reading a value to prove nobody changed it is the
        # opposite of consuming it.
        surge = (repo / "simulation" / "surge.py").read_text(encoding="utf-8")
        self.assertIn("assert_invariants", surge)
        self.assertNotIn("overdue_by(", surge)


class TestDeterminism(ClaimTest):
    claim = "The same patient state always produces the same result."

    def setUp(self):
        self.engine = engine()

    def test_assessing_twice_gives_the_same_answer(self):
        for patient in roster():
            first = self.engine.assess(patient)
            second = self.engine.assess(patient)
            self.assertEqual(first.risk_score, second.risk_score)
            self.assertEqual(first.band, second.band)
            self.assertEqual(first.confidence, second.confidence)

    def test_assessing_does_not_mutate_the_patient(self):
        """
        The engine is stateless by contract. A scorer that quietly edited its
        input would make every downstream comparison meaningless.
        """
        for patient in roster():
            before = copy.deepcopy(patient)
            self.engine.assess(patient)
            self.assertEqual(before.vitals, patient.vitals,
                             f"{patient.patient_id}: vitals were mutated")
            self.assertEqual(before.self_report.symptoms,
                             patient.self_report.symptoms,
                             f"{patient.patient_id}: symptoms were mutated")

    def test_the_whole_shift_replays_identically(self):
        first = SimulationClock(self.engine, roster()).run()
        second = SimulationClock(self.engine, roster()).run()
        self.assertEqual(
            [(r.at_minute, r.patient_id, r.final_band) for r in first.records],
            [(r.at_minute, r.patient_id, r.final_band) for r in second.records])


class TestKnownGaps(ClaimTest):
    claim = ("KNOWN GAPS -- properties this project implies and does not yet "
             "hold. Pinned so they cannot be forgotten.")

    def setUp(self):
        self.engine = engine()

    @unittest.expectedFailure
    def test_removing_information_never_raises_confidence(self):
        """
        GAP, found by this suite. Deleting a dissenting signal can RAISE
        confidence, because the agreement driver computes its split over the
        modalities that spoke -- so silencing one removes a disagreement.

        13 of 210 single-item deletions across the roster increase confidence.
        Not measuring the thing that disagrees should never be a way to look
        more certain.

        The fix is a pessimistic split: assume a silent modality takes whichever
        side makes the disagreement worst, which is this project's own "unknown
        never becomes no" rule applied to the one driver that breaks it. It is
        not shipped in the same commit as the suite that found it, because that
        would mean changing every confidence figure in the repository and the
        thing that checks them at the same time.

        Marked expectedFailure rather than deleted. If somebody fixes it, this
        reports an unexpected success and should be promoted to a real test.
        """
        raised = []
        for patient in roster():
            baseline = self.engine.assess(patient).confidence
            for field in patient.vitals.present_fields():
                stripped = copy.deepcopy(patient)
                setattr(stripped.vitals, field, None)
                if self.engine.assess(stripped).confidence > baseline + 1e-9:
                    raised.append((patient.patient_id, field))
            for modality in ("facial", "voice", "observed"):
                broken = copy.deepcopy(patient)
                getattr(broken, modality).capture_status = CaptureStatus.FAILED
                if self.engine.assess(broken).confidence > baseline + 1e-9:
                    raised.append((patient.patient_id, modality))

        self.assertEqual([], raised, f"{len(raised)} deletions raised confidence")

    @unittest.expectedFailure
    def test_the_acute_on_chronic_facial_case_is_in_the_roster(self):
        """
        GAP, open since Phase 6. A patient with a documented facial asymmetry
        whose family says it got worse TODAY is arguably the hardest facial
        case in clinical practice. The module handles it correctly -- P015 with
        change_reported_as_new=yes goes 66 to 81 -- but no authored patient
        exercises it, so the demo never shows the case that matters most.
        """
        self.assertTrue(
            tagged("acute_on_chronic"),
            "no patient exercises the acute-on-chronic facial branch")
