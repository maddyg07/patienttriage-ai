"""
tests/test_question_loop.py
===========================
CLAIM: A question is asked, the answer is heard, the next question is
different, and the intake ends.

THE FAILURE THIS FILE EXISTS FOR
--------------------------------
The console asked a question. The patient answered. The transcript logged the
answer. And then nothing happened: the same question stayed on screen, no new
one appeared, and the assessment never ended.

`questions_asked` was READ when choosing what to ask next and WRITTEN nowhere.
So the same question won every time, forever. There was also no terminal state
at all, which meant that even with the loop fixed a patient would eventually sit
looking at a screen that had quietly stopped.

CLAIM: The uncertainty engine reports a missing modality only when it is
actually missing.

It said "facial capture not attempted" on every single encounter, including
ones where the patient sat in front of a live camera throughout, because
nothing ever told it the camera had run. A confidence penalty for a missing
modality is correct. Charging it while the modality is running is a lie in the
audit trail, and it appeared on every screenshot.
"""

from __future__ import annotations

from core.ai.local_provider import LocalProvider
from tests.support import ClaimTest, has_teeth


def clinic():
    import scripts.run_clinic as rc
    return rc.Clinic("medium_ed")


class TestTheLoopAdvances(ClaimTest):
    claim = "Answering a question produces a different question."

    def test_the_same_question_is_never_asked_twice(self):
        c = clinic()
        s = c.open()
        seen, replies = [], ["my chest has been hurting since this morning",
                             "it started around 7am", "about an 8 out of 10",
                             "it is getting worse", "nobody has checked",
                             "I take amlodipine"]
        at = 5
        for reply in replies:
            text, qid, _ = c.next_question(s)
            if not text:
                break
            self.assertNotIn(qid, seen,
                             f"'{qid}' was asked twice; the loop is stuck")
            seen.append(qid)
            s.record_question(qid, text, "")
            s.hear(reply, at)
            at += 12
        self.assertGreaterEqual(len(seen), 4,
                                "the loop stalled after a single question")

    def test_an_answer_is_attached_to_the_question_it_answered(self):
        c = clinic()
        s = c.open()
        text, qid, why = c.next_question(s)
        s.record_question(qid, text, why)
        s.hear("my chest has been hurting since this morning", 6)
        entry = s.questions_asked[0]
        self.assertEqual(entry["id"], qid)
        self.assertIn("chest", entry["answer"])
        self.assertIsNotNone(entry["answered_at_second"])

    def test_the_intake_reaches_a_terminal_state(self):
        c = clinic()
        s = c.open()
        at = 5
        for _ in range(12):
            text, qid, why = c.next_question(s)
            if not text:
                self.assertEqual(why, "exhausted",
                                 "the loop went quiet without saying why")
                break
            s.record_question(qid, text, why)
            s.hear("yes, about that", at)
            at += 10
        else:
            self.fail("the question loop never ended")
        s.finish()
        self.assertTrue(s.complete)
        self.assertTrue(s.completed_reason)

    def test_an_emergency_ends_questioning_immediately(self):
        c = clinic()
        s = c.open()
        text, qid, why = c.next_question(s)
        s.record_question(qid, text, why)
        s.hear("I can't breathe and my leg is amputated", 8)
        self.assertEqual(c.next_question(s), ("", "", ""))

    @has_teeth
    def test_a_question_is_produced_at_all(self):
        """
        If next_question returned nothing for everything, the uniqueness and
        termination assertions above would all pass on an empty loop.
        """
        text, qid, why = clinic().next_question(clinic().open())
        self.assertTrue(text)
        self.assertTrue(qid)


class TestCaptureStatusIsTruthful(ClaimTest):
    claim = "A modality is reported missing only when it is actually missing."

    def test_a_running_camera_is_not_reported_as_not_attempted(self):
        c = clinic()
        s = c.open()
        s.hear("my chest hurts", 4)
        before = " ".join(s.last_result["uncertainty_drivers"])
        self.assertIn("facial capture not attempted", before)

        s.set_capture(facial="ok", voice="ok")
        after = " ".join(s.last_result["uncertainty_drivers"])
        self.assertNotIn("facial capture not attempted", after,
                         "the camera ran and the engine still says it did not")
        self.assertNotIn("voice capture not attempted", after)

    def test_a_refused_camera_is_reported_and_is_not_the_same_as_ok(self):
        c = clinic()
        s = c.open()
        s.set_capture(facial="refused", voice="ok")
        s.hear("my chest hurts", 4)
        drivers = " ".join(s.last_result["uncertainty_drivers"])
        self.assertIn("facial", drivers,
                      "a refused camera vanished from the uncertainty account")

    def test_a_pain_score_in_any_utterance_is_heard(self):
        """
        The system asked how bad it was, was told "about an 8 out of 10", and
        still recorded "pain score not obtained". It asked, was answered, and
        did not listen.
        """
        c = clinic()
        s = c.open()
        s.hear("my chest hurts", 4)
        s.hear("about an 8 out of 10", 16)
        drivers = " ".join(s.last_result["uncertainty_drivers"])
        self.assertNotIn("pain score not obtained", drivers)
        self.assertEqual(s.reported_pain, 8)


class TestTheScanDoesNotFakeItsOwnStability(ClaimTest):
    claim = ("The stability gate is fed a measured spread, never a constant "
             "chosen so it always passes.")

    def test_a_single_frame_with_no_spread_is_rejected(self):
        from core.capture_fusion import CaptureFusion
        result = CaptureFusion().fuse(
            {"index": 0.40, "spread": 1.0, "brightness": 130,
             "structure": 0.25, "gradient": 0.05, "frames": 1}, None)
        self.assertEqual(result.facial.strength, "unreliable")

    def test_only_a_strong_reading_raises_an_observation(self):
        """
        A "possible" reading used to raise a facial asymmetry observation on a
        nurse's screen -- on the strength of a measurement the fuser had itself
        called too close to call.
        """
        from core.capture_fusion import CaptureFusion
        borderline = CaptureFusion().fuse(
            {"index": 0.24, "spread": 0.02, "brightness": 130,
             "structure": 0.25, "gradient": 0.05, "frames": 7}, None)
        self.assertEqual(borderline.facial.strength, "possible")
        self.assertIsNone(borderline.facial.suggestion)


class TestTheLoopDoesNotRunItself(ClaimTest):
    claim = ("A question stays on screen until it is answered. Redrawing the "
             "page does not advance the conversation.")

    def test_repeated_frames_do_not_burn_through_the_ladder(self):
        """
        The runaway.

        Every server-sent frame carries the next question. The page rendered
        one, reported it asked, the server recomputed and sent the following
        one, the page reported that asked, and so on. Seven questions were
        logged in the same second, before the patient had said a word, and the
        intake then declared itself complete.

        The patient's screen showed "Finished. A nurse has your details."
        having asked nothing at all.
        """
        c = clinic()
        s = c.open()
        for _ in range(12):                    # twelve SSE frames, patient silent
            text, qid, why = c.next_question(s)
            if text:
                s.record_question(qid, text, why)
        self.assertEqual(len(s.questions_asked), 1,
                         f"{len(s.questions_asked)} questions were asked "
                         f"without the patient saying anything")
        self.assertFalse(s.complete,
                         "the intake completed before the patient spoke")

    def test_the_pending_question_is_returned_unchanged(self):
        c = clinic()
        s = c.open()
        first = c.next_question(s)
        s.record_question(first[0] and first[1], first[0], first[2])
        for _ in range(5):
            self.assertEqual(c.next_question(s)[1], first[1],
                             "the question changed while it was still pending")

    def test_answering_is_the_only_thing_that_advances_it(self):
        c = clinic()
        s = c.open()
        text, qid, why = c.next_question(s)
        s.record_question(qid, text, why)
        c.next_question(s)
        c.next_question(s)
        self.assertEqual(c.next_question(s)[1], qid)
        s.hear("my stomach is paining", 9)
        self.assertNotEqual(c.next_question(s)[1], qid,
                            "the question did not move on after an answer")

    def test_an_intake_with_no_speech_never_completes(self):
        """
        Reaching the end of the ladder on an empty transcript means the ladder
        ran itself, not that the history is complete.
        """
        c = clinic()
        s = c.open()
        for _ in range(20):
            text, qid, why = c.next_question(s)
            if text:
                s.record_question(qid, text, why)
        self.assertNotEqual(c.next_question(s)[2], "exhausted",
                            "the intake called itself exhausted having heard "
                            "nothing")

    @has_teeth
    def test_the_runaway_check_can_fail(self):
        """
        Drive the loop the way the broken page did -- clearing the pending
        question each time, which is what "asked means received" amounted to --
        and confirm the ladder does burn through. If it does not, the test
        above is passing for the wrong reason.
        """
        c = clinic()
        s = c.open()
        for _ in range(12):
            text, qid, why = c.next_question(s)
            if text:
                s.record_question(qid, text, why)
                s.pending_question = None      # the old, broken behaviour
        self.assertGreater(len(s.questions_asked), 1)


class TestDegradationSaysWhy(ClaimTest):
    claim = "A DEGRADED badge carries the reason it degraded."

    def test_a_failing_provider_reports_a_reason(self):
        from core.ai.model_provider import OpenAIProvider
        from core.session import ClinicSession
        s = ClinicSession("D", OpenAIProvider(api_key="sk-not-a-real-key"))
        s.hear("my stomach is paining", 5)
        self.assertTrue(s.degraded)
        self.assertTrue(s.degraded_reason,
                        "the console shows DEGRADED with no explanation, which "
                        "tells a nurse the system is worse without telling "
                        "them what to do about it")
        self.assertIn("degraded_reason", s.snapshot())
