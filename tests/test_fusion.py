"""
tests/test_fusion.py
====================
CLAIM: A sensor that cannot see does not get to speak. Every gate in
core/capture_fusion.py exists because the ungated version produced a false
candidate in front of a real person, and each test below pins one of those
failures so it cannot come back.

And the claim the whole module is for:

CLAIM: Neither channel decides alone. A facial candidate with fluent,
well-sustained speech is reported as uncorroborated and carries no suggestion,
because a droop a camera can see usually travels with dysarthria, and a picture
that does not cohere is when to ask rather than assert.

WHY THESE TESTS EXIST AT ALL
----------------------------
The first live intake console was tested by its author, who sat in even
lighting and started speaking immediately. It passed. Somebody else sat down
under a window and paused to collect their thoughts, and it reported strong
facial asymmetry and breathlessness in a healthy person.

That is the ordinary way sensor demos fail: not in the code, but in the gap
between the conditions the author happened to test in and the conditions
everybody else has. These tests encode the second set.
"""

from __future__ import annotations

from core.capture_fusion import (
    NONE,
    POSSIBLE,
    STRONG,
    UNRELIABLE,
    CaptureFusion,
)
from tests.support import ClaimTest, has_teeth


def cam(**over) -> dict:
    """A clean camera reading: even lighting, still head, face in frame."""
    base = {"index": 0.10, "spread": 0.02, "brightness": 120,
            "structure": 0.22, "gradient": 0.05, "frames": 9}
    base.update(over)
    return base


def aud(**over) -> dict:
    """A clean audio reading: someone talking normally for several seconds."""
    base = {"speech_seconds": 6.0, "snr": 5.0, "breaks_per_10s": 1.0,
            "median_phrase_ms": 1500, "longest_phrase_ms": 3000}
    base.update(over)
    return base


class TestTheCameraRefusesBadConditions(ClaimTest):
    claim = ("A symmetry reading is rejected before it is interpreted when "
             "the conditions that make it meaningless are present.")

    def setUp(self):
        self.f = CaptureFusion()

    def test_side_lighting_is_rejected_not_reported_as_asymmetry(self):
        """
        The failure that started this module. A symmetric face under a window
        produced a strong-asymmetry candidate, because a shadow and a droop are
        both "one side is darker".
        """
        r = self.f.fuse(cam(index=0.31, gradient=0.34), aud())
        self.assertEqual(r.facial.strength, UNRELIABLE)
        self.assertIsNone(r.facial.suggestion)
        self.assertIn("gradient", r.facial.reasons[0])

    def test_a_flickering_reading_is_rejected(self):
        """A real facial difference does not change between frames."""
        r = self.f.fuse(cam(index=0.40, spread=0.12), aud())
        self.assertEqual(r.facial.strength, UNRELIABLE)
        self.assertIn("flicker", " ".join(r.facial.reasons))

    def test_a_blank_wall_is_rejected(self):
        r = self.f.fuse(cam(index=0.45, structure=0.01), aud())
        self.assertEqual(r.facial.strength, UNRELIABLE)
        self.assertIn("wall", " ".join(r.facial.reasons))

    def test_too_few_frames_is_rejected(self):
        r = self.f.fuse(cam(index=0.45, frames=2), aud())
        self.assertEqual(r.facial.strength, UNRELIABLE)

    def test_unreliable_is_not_the_same_as_nothing_found(self):
        """
        The distinction the whole design rests on. A sensor that could not see
        must not be recorded as a sensor that looked and found nothing, because
        only the second is evidence.
        """
        blind = self.f.fuse(cam(gradient=0.9), aud())
        looked = self.f.fuse(cam(index=0.04), aud())
        self.assertEqual(blind.facial.strength, UNRELIABLE)
        self.assertEqual(looked.facial.strength, NONE)
        self.assertIsNone(blind.facial.suggestion)
        self.assertEqual(looked.facial.suggestion, "no")

    @has_teeth
    def test_a_clean_strong_reading_still_gets_through(self):
        """
        Gates that reject everything are not safety, they are a broken sensor.
        A well-lit, stable, structured reading above threshold must still
        produce a strong candidate, or every assertion above passes vacuously.
        """
        r = self.f.fuse(cam(index=0.40),
                        aud(breaks_per_10s=4.5, median_phrase_ms=700,
                            longest_phrase_ms=1300))
        self.assertEqual(r.facial.strength, STRONG)
        self.assertEqual(r.facial.suggestion, "yes")


class TestTheMicrophoneRefusesBadConditions(ClaimTest):
    claim = ("Silence before somebody starts talking is not a respiratory "
             "sign, and neither is a short answer.")

    def setUp(self):
        self.f = CaptureFusion()

    def test_a_late_start_produces_no_breathlessness_candidate(self):
        """
        The second failure that started this module. The browser now trims
        leading silence before measuring, so a late start reaches the fuser as
        ordinary speech rather than as a recording full of pauses.
        """
        r = self.f.fuse(cam(), aud(speech_seconds=5.0, breaks_per_10s=1.1,
                                   longest_phrase_ms=3100))
        self.assertEqual(r.breathlessness.strength, NONE)
        self.assertEqual(r.sentence.strength, NONE)

    def test_barely_speaking_is_unreliable_rather_than_alarming(self):
        r = self.f.fuse(cam(), aud(speech_seconds=1.1, breaks_per_10s=9.0,
                                   median_phrase_ms=300, longest_phrase_ms=500))
        self.assertEqual(r.breathlessness.strength, UNRELIABLE)
        self.assertEqual(r.sentence.strength, UNRELIABLE)
        self.assertIsNone(r.breathlessness.suggestion)

    def test_a_noisy_room_is_unreliable(self):
        r = self.f.fuse(cam(), aud(snr=1.2, breaks_per_10s=8.0))
        self.assertEqual(r.breathlessness.strength, UNRELIABLE)

    def test_one_sustained_phrase_settles_the_sentence_question(self):
        """
        Somebody who cannot finish a sentence does not manage a three-second
        phrase once. A single sustained phrase outweighs any amount of
        pause-counting.
        """
        r = self.f.fuse(cam(), aud(breaks_per_10s=7.0, median_phrase_ms=600,
                                   longest_phrase_ms=3000))
        self.assertEqual(r.sentence.strength, NONE)

    def test_breaks_are_counted_per_speech_time_not_in_total(self):
        """A long answer must not be penalised for containing more pauses."""
        short = self.f.fuse(cam(), aud(speech_seconds=3.0, breaks_per_10s=2.0))
        long = self.f.fuse(cam(), aud(speech_seconds=20.0, breaks_per_10s=2.0))
        self.assertEqual(short.breathlessness.strength,
                         long.breathlessness.strength)


class TestTheChannelsAnswerToEachOther(ClaimTest):
    claim = ("Neither channel decides alone. An uncorroborated candidate is "
             "reported as uncorroborated and carries no suggestion.")

    def setUp(self):
        self.f = CaptureFusion()

    def test_a_strong_face_with_fluent_speech_is_downgraded_and_unsuggested(self):
        r = self.f.fuse(cam(index=0.38),
                        aud(breaks_per_10s=0.8, median_phrase_ms=1800,
                            longest_phrase_ms=3400))
        self.assertEqual(r.agreement, "channels disagree")
        self.assertEqual(r.facial.strength, POSSIBLE)
        self.assertIsNone(r.facial.suggestion)
        self.assertTrue(r.facial.corroboration.startswith("Not corroborated"))

    def test_a_face_and_a_voice_pointing_the_same_way_reinforce(self):
        r = self.f.fuse(cam(index=0.24),
                        aud(breaks_per_10s=4.5, median_phrase_ms=700,
                            longest_phrase_ms=1300))
        self.assertEqual(r.agreement, "channels agree")
        self.assertEqual(r.facial.strength, STRONG)
        self.assertIn("Corroborated", r.facial.corroboration)

    def test_a_lone_usable_channel_never_suggests(self):
        r = self.f.fuse(cam(index=0.40), aud(speech_seconds=0.4))
        self.assertEqual(r.agreement, "camera only")
        self.assertIsNone(r.facial.suggestion)
        self.assertEqual(r.facial.strength, POSSIBLE)

    def test_breathlessness_without_visible_distress_is_uncorroborated(self):
        r = self.f.fuse(cam(visible_distress=False),
                        aud(breaks_per_10s=7.0, median_phrase_ms=600,
                            longest_phrase_ms=900))
        self.assertEqual(r.breathlessness.strength, POSSIBLE)
        self.assertIsNone(r.breathlessness.suggestion)
        self.assertTrue(
            r.breathlessness.corroboration.startswith("Not corroborated"))

    def test_breathlessness_with_visible_distress_is_corroborated(self):
        r = self.f.fuse(cam(visible_distress=True),
                        aud(breaks_per_10s=7.0, median_phrase_ms=600,
                            longest_phrase_ms=900))
        self.assertIn("Corroborated", r.breathlessness.corroboration)
        self.assertEqual(r.breathlessness.suggestion, "yes")

    def test_no_usable_channel_says_so_and_suggests_nothing(self):
        r = self.f.fuse(cam(gradient=0.9), aud(speech_seconds=0.2))
        self.assertEqual(r.agreement, "no usable channel")
        for cand in (r.facial, r.breathlessness, r.sentence):
            self.assertIsNone(cand.suggestion)

    def test_fusion_never_produces_a_band_or_a_score(self):
        """
        Corroboration changes what the operator is ASKED. It has no route to a
        score, a band or a confidence figure, and no key here should suggest
        otherwise.
        """
        r = self.f.fuse(cam(index=0.4), aud()).as_dict()
        flat = str(r).lower()
        for word in ("band", "risk_score", "confidence", "l4", "code", "acuity"):
            self.assertNotIn(word, flat,
                             f"'{word}' appears in a fusion result; the "
                             f"capture layer has started making clinical "
                             f"judgements")


class TestNegationIsScopedToTheClause(ClaimTest):
    claim = ("A denial applies to its own clause. 'denies breathlessness, "
             "temperature three days' denies one thing and reports another.")

    def setUp(self):
        from core.intake_bridge import SymptomReader
        self.reader = SymptomReader()

    def test_a_denial_does_not_leak_past_a_comma(self):
        reported, denied = self.reader.read(
            "denies breathlessness, temperature for three days")
        self.assertIn("breathlessness", denied)
        self.assertIn("fever", reported)
        self.assertNotIn("fever", denied)

    def test_a_denial_does_not_leak_past_and_or_but(self):
        reported, denied = self.reader.read(
            "no chest pain but I do have a headache")
        self.assertIn("chest pain", denied)
        self.assertIn("headache", reported)

        reported, denied = self.reader.read(
            "I have chest pain and no breathlessness")
        self.assertIn("chest pain", reported)
        self.assertIn("breathlessness", denied)

    def test_a_run_of_denials_stays_denied(self):
        reported, denied = self.reader.read("no fever, no cough, no vomiting")
        self.assertEqual(reported, [])
        for term in ("fever", "cough", "vomiting"):
            self.assertIn(term, denied)

    def test_spoken_symptoms_reach_the_engine_without_any_typing(self):
        """
        The third reported failure. The transcript is read on submission and
        always was; nothing on screen said so, which from the operator's chair
        is the same as it not happening. This asserts the underlying path.
        """
        from core.intake_bridge import assess_payload
        result = assess_payload({
            "patient_id": "LIVE-SPOKEN", "age_years": 58,
            "history_tier": "partial",
            "transcript": "I have crushing chest pain and I am sweating "
                          "and feel sick",
            "heart_rate": 118, "respiratory_rate": 26, "spo2": 91,
            "systolic_bp": 92, "diastolic_bp": 58,
            "consciousness": "alert", "voice_capture_status": "ok",
            "facial_capture_status": "ok", "asymmetry_observed": "no",
            "droop_observed": "no", "skin_pallor_or_cyanosis": "yes",
        })
        self.assertIn("chest pain", result["symptoms"])
        self.assertIn("sweating", result["symptoms"])
        self.assertGreater(result["risk_score"], 0)
