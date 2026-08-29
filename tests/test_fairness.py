"""
tests/test_fairness.py
======================
CLAIM: A patient with congenital asymmetry, acid-attack scarring or chronic
post-stroke weakness must not be flagged as an emergency because their face is
unusual. Points come from acute CHANGE, never from appearance.

And its harder companion, which is the one that actually protects people:

CLAIM: A weak baseline lowers confidence. It never raises the score.

The obvious alternative -- treat an unverifiable baseline as possibly acute and
escalate -- sounds cautious and is quietly discriminatory. It escalates hardest
on patients without regular care, without records, and without a relative to
speak for them. This file is where that stops being a paragraph in the README.
"""

from __future__ import annotations

from core.enums import FacialBaselineCondition, Tri
from core.facial import fairness_counterfactual, resolve_baseline
from tests.support import ClaimTest, engine, roster, tagged


class TestFacialFairness(ClaimTest):
    claim = ("Facial points come from acute change, never from appearance. "
             "The cause of a documented difference changes nothing.")

    def setUp(self):
        self.engine = engine()

    def test_the_cause_of_a_facial_difference_never_changes_the_score(self):
        """
        Re-score each facial patient once per possible CAUSE of their
        difference, changing nothing else. The facial points must not move.

        A system that flagged the acid-attack survivor but not the congenital
        case would pass a naive fairness check and fail this one.
        """
        for patient in roster():
            if not patient.facial.capture_status.has_data:
                continue
            if not (patient.facial.asymmetry_observed.is_yes
                    or patient.facial.droop_observed.is_yes):
                continue

            result = fairness_counterfactual(patient, self.engine.weights)
            self.assertTrue(
                result.is_fair,
                f"{patient.patient_id}: facial points varied by baseline "
                f"condition {result.points_by_condition} -- the module is "
                f"scoring appearance rather than change")
            self.assertEqual(
                1, len(set(result.points_by_condition.values())),
                f"{patient.patient_id}: is_fair said yes while the points "
                f"differ -- the property and its check disagree")

    def test_a_documented_chronic_difference_scores_zero(self):
        for tag in ("congenital_baseline_respected",
                    "scarring_baseline_respected",
                    "chronic_deficit_respected"):
            for patient in tagged(tag):
                with self.subTest(patient=patient.patient_id, tag=tag):
                    assessment = self.engine.assess(patient)
                    facial = sum(c.points for c in assessment.contributions
                                 if c.source == "facial")
                    self.assertEqual(
                        0, facial,
                        f"{patient.patient_id} was charged {facial} points for "
                        f"having a face that is chronically different")

    def test_an_acute_change_does_score(self):
        """
        The mirror test. A fairness suite that only checked nobody was
        penalised would pass on a module that scored nothing at all.
        """
        for patient in tagged("acute_change_detected"):
            assessment = self.engine.assess(patient)
            facial = sum(c.points for c in assessment.contributions
                         if c.source == "facial")
            self.assertGreater(
                facial, 0,
                f"{patient.patient_id} has an acute facial change and scored "
                f"nothing for it")


class TestWeakBaselineNeverRaisesScore(ClaimTest):
    claim = ("A weak or absent baseline lowers confidence and never raises "
             "the score. Ignorance is not converted into the patient's points.")

    def setUp(self):
        self.engine = engine()

    def test_stripping_the_record_away_does_not_move_the_score(self):
        """
        Take a patient with a documented baseline and degrade its provenance
        one tier at a time. Score flat, confidence falling.
        """
        import copy

        for patient in tagged("chronic_deficit_respected"):
            full = self.engine.assess(patient)
            scores, confidences = [full.risk_score], [full.confidence]

            degraded = copy.deepcopy(patient)
            degraded.history.baseline_notes = ""
            weaker = self.engine.assess(degraded)
            scores.append(weaker.risk_score)
            confidences.append(weaker.confidence)

            self.assertEqual(
                {scores[0]}, set(scores),
                f"{patient.patient_id}: score moved when the baseline record "
                f"was weakened -- ignorance is being charged to the patient")
            self.assertLessEqual(
                confidences[1], confidences[0] + 1e-9,
                f"{patient.patient_id}: a weaker baseline raised confidence")

    def test_an_unknown_baseline_returns_unknown_not_a_guess(self):
        for patient in tagged("unknown_baseline"):
            self.assertEqual(
                Tri.UNKNOWN, patient.facial.acute_change(),
                f"{patient.patient_id}: the module guessed rather than "
                f"reporting that it cannot tell")

    def test_every_baseline_carries_its_provenance(self):
        for patient in roster():
            if not patient.facial.capture_status.has_data:
                continue
            baseline = resolve_baseline(patient)
            self.assertTrue(baseline.label)
            self.assertGreaterEqual(baseline.reliability, 0.0)
            self.assertLessEqual(baseline.reliability, 1.0)
