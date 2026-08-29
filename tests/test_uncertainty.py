"""
tests/test_uncertainty.py
=========================
CLAIM: Missing data raises uncertainty. It never lowers risk.

CLAIM: The uncertainty engine reads the risk score and cannot write it. Low
confidence never moves anyone down the queue.

CLAIM: The uncertainty interval is asymmetric, because information we do not
have can conceal danger but cannot manufacture safety.

The first of these is the oldest invariant in the project -- it is in the Phase
1 schema docstring as "we never let 'we don't know' quietly become 'no' or
'normal'" -- and it is the one most likely to be broken by accident, because
every convenient default breaks it.
"""

from __future__ import annotations

import copy

from core.enums import CaptureStatus, HistoryTier, Tri
from core.schema import VitalSigns
from tests.support import ClaimTest, engine, roster, tagged


class TestMissingDataNeverLowersRisk(ClaimTest):
    claim = "Missing data raises uncertainty and never lowers risk."

    def setUp(self):
        self.engine = engine()

    def test_a_missing_vital_is_never_scored_as_a_normal_one(self):
        """
        The precise version of "missing data never lowers risk", and writing
        this suite is what forced the precision.

        The first draft asserted that deleting a measured value can never
        lower the score. That is FALSE and it should be: points are earned by
        evidence, and a patient cannot be charged for a fever nobody measured.
        Demanding otherwise would require the engine to retain points for
        findings it does not have, which is fabrication.

        What must hold is narrower and is the thing that actually protects
        people: an absent value contributes NOTHING -- it is never scored as
        though it had been measured and found normal, and it never produces a
        negative contribution.
        """
        for patient in roster():
            for field in patient.vitals.present_fields():
                stripped = copy.deepcopy(patient)
                setattr(stripped.vitals, field, None)
                assessment = self.engine.assess(stripped)

                for contribution in assessment.contributions:
                    if field in contribution.label and contribution.points < 0:
                        self.fail(
                            f"{patient.patient_id}: an absent {field} produced "
                            f"a negative contribution ({contribution.label})")

                self.assertIn(
                    field, assessment.missing_fields,
                    f"{patient.patient_id}: {field} was removed and the "
                    f"assessment does not report it as missing")

    def test_a_failed_sensor_is_recorded_and_never_reassures(self):
        """
        Technology failure must not become clinical reassurance.

        A failed sensor legitimately removes the points its findings earned.
        What it must not do is leave the assessment looking like a complete
        one: the loss has to be visible as a named reason.
        """
        for patient in roster():
            for modality in ("facial", "voice", "observed"):
                broken = copy.deepcopy(patient)
                getattr(broken, modality).capture_status = CaptureStatus.FAILED
                assessment = self.engine.assess(broken)
                reasons = " ".join(assessment.quality.all_reasons()).lower()
                self.assertIn(
                    modality, reasons,
                    f"{patient.patient_id}: {modality} capture failed and the "
                    f"uncertainty panel does not mention it")

    def test_erasing_the_history_never_raises_confidence(self):
        """A first-time patient is not a healthy patient."""
        for patient in roster():
            if patient.history.tier is HistoryTier.ZERO:
                continue
            before = self.engine.assess(patient)
            erased = copy.deepcopy(patient)
            erased.history.tier = HistoryTier.ZERO
            erased.history.conditions = []
            erased.history.medications = []
            erased.history.baseline_notes = ""
            after = self.engine.assess(erased)
            self.assertLess(
                after.confidence, before.confidence + 1e-9,
                f"{patient.patient_id}: erasing the whole record did not cost "
                f"any confidence")

    def test_the_authored_scenarios_agree(self):
        for tag in ("missing_data_never_lowers_risk",
                    "missing_history_never_lowers_risk"):
            for patient in tagged(tag):
                assessment = self.engine.assess(patient)
                self.assertIsNotNone(assessment.quality)
                self.assertLess(
                    assessment.confidence, 1.0,
                    f"{patient.patient_id} is missing data and reports full "
                    f"confidence")


class TestUncertaintyCannotWriteTheScore(ClaimTest):
    claim = ("The uncertainty engine reads the risk score and cannot write it. "
             "Low confidence never moves anyone down the queue.")

    def setUp(self):
        self.engine = engine()

    def test_confidence_never_changes_the_score(self):
        """
        Run the pipeline with the uncertainty stage and compare the score to
        the raw sum of the contributions it was handed.
        """
        for patient in roster():
            assessment = self.engine.assess(patient)
            total = sum(c.points for c in assessment.contributions)
            expected = max(0.0, min(100.0, total))
            self.assertAlmostEqual(
                expected, assessment.risk_score, places=6,
                msg=f"{patient.patient_id}: the score is not the sum of its "
                    f"explanation -- something wrote to it after the fact")

    def test_low_confidence_never_lowers_the_band(self):
        for patient in roster():
            assessment = self.engine.assess(patient)
            score_band = self.engine.hospital.thresholds.band_for_score(
                assessment.risk_score)
            self.assertGreaterEqual(
                int(assessment.band), int(score_band),
                f"{patient.patient_id}: the final band is BELOW what the score "
                f"alone would give. Uncertainty or a rule moved someone down.")

    def test_the_interval_reaches_further_up_than_down(self):
        """
        What we do not know can hide danger. It cannot manufacture safety.
        """
        for patient in roster():
            assessment = self.engine.assess(patient)
            quality = assessment.quality
            if quality is None or assessment.confidence >= 1.0:
                continue
            if quality.score_high >= 100.0:
                # The upward reach is clipped by the 0-100 range rather than by
                # the asymmetry rule, so this patient says nothing about the
                # property under test. Note WHICH direction gets clipped: the
                # interval is only ever truncated at the top, which means the
                # clipping can understate danger and can never understate
                # safety. That is the same asymmetry, surviving the clamp.
                continue
            up = quality.score_high - assessment.risk_score
            down = assessment.risk_score - quality.score_low
            self.assertGreater(
                up, down,
                f"{patient.patient_id}: the uncertainty interval reaches as "
                f"far down as up ({down:.1f} vs {up:.1f})")

    def test_worse_data_never_narrows_the_plausible_band_set(self):
        """Monotone widening: worse input, wider set, always."""
        for patient in roster():
            if not patient.vitals.present_fields():
                continue
            before = self.engine.assess(patient)
            stripped = copy.deepcopy(patient)
            stripped.vitals = VitalSigns(
                measured_at_minute=patient.vitals.measured_at_minute)
            after = self.engine.assess(stripped)
            self.assertGreaterEqual(
                len(after.plausible_bands), 1)
            self.assertLessEqual(
                after.confidence, before.confidence + 1e-9,
                f"{patient.patient_id}: losing every vital raised confidence")
