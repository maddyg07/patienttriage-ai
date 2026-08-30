"""
tests/test_contradiction_evidence.py
====================================
Phase 21. CLAIM: a multimodal discrepancy needs sustained or corroborated
evidence. One frame is not a contradiction.

THE FAILURE THIS FILE EXISTS FOR
---------------------------------
`ClinicSession._check_contradiction` asked whether ANY qualifying observation
existed inside the time window:

    def recent(entries) -> bool:
        return any(abs(e["at_second"] - at_second) <= WINDOW ...)

One observation was enough. So a single frame of apparent grimacing -- a
blink, a head turn, a compression artefact, someone shifting in a plastic
chair -- landing within twelve seconds of "I'm fine" produced a logged
contradiction against the patient. That is the exact case the brief rejects:
a transient visual candidate treated as though it were a sustained finding.

WHAT COUNTS NOW
---------------
Two ways to be meaningful, one way to be noise.

  * CORROBORATED: both channels disagree with the statement independently.
    A single observation in each is already two witnesses, and an acute
    problem severe enough to show on a face usually shows in the voice too.
  * SUSTAINED: one channel, but it held -- repeated inside the window, or
    marked persistent by whatever produced it.
  * Otherwise: a flicker, logged as `transient_candidate` so the stage is
    visibly shown to have run and decided NO, and dropped.

WHAT DID NOT CHANGE, AND MUST NOT
---------------------------------
A contradiction is still a REVIEW FLAG, never an emergency, and never a claim
about the patient's honesty. It says two channels disagree and asks a human to
look. Deception is not a finding this system is entitled to produce, and
"patient is lying" is not a triage category.
"""

from __future__ import annotations

from core.ai.local_provider import LocalProvider
from core.session import ClinicSession
from tests.support import ClaimTest


def session() -> ClinicSession:
    return ClinicSession("CONTRA", LocalProvider())


def contradicted(s: ClinicSession) -> bool:
    return any(e.kind == "contradiction" for e in s.events)


def transient(s: ClinicSession) -> bool:
    return any(e.kind == "transient_candidate" for e in s.events)


class TestOneFrameIsNotAContradiction(ClaimTest):
    claim = ("A single observation in a single channel is a flicker, not a "
             "discrepancy, however close in time it lands.")

    def test_a_lone_visual_blip_is_rejected(self):
        s = session()
        s.observe_visual("grimacing", "possible grimace", at_second=20)
        s.hear("I'm fine", at_second=21)
        self.assertFalse(contradicted(s),
                         "one frame of grimacing produced a contradiction")

    def test_the_rejection_is_still_recorded(self):
        """
        Rejected is not the same as unseen. The stage must be visibly shown to
        have run and decided NO, or a debug view cannot tell 'nothing was
        observed' from 'something was observed and dismissed'.
        """
        s = session()
        s.observe_visual("grimacing", "possible grimace", at_second=20)
        s.hear("I'm fine", at_second=21)
        self.assertTrue(transient(s))

    def test_a_lone_audio_blip_is_rejected(self):
        s = session()
        s.observe_audio("strain", "one strained phrase", at_second=20)
        s.hear("I'm okay", at_second=21)
        self.assertFalse(contradicted(s))


class TestMeaningfulEvidenceStillFires(ClaimTest):
    claim = ("Filtering flickers must not cost the discrepancies that matter.")

    def test_cross_modal_corroboration(self):
        """The brief's own example: persistent distress plus strained speech."""
        s = session()
        s.observe_visual("distress", "apparent facial discomfort", at_second=20)
        s.observe_audio("strain", "strained voice", at_second=21)
        s.hear("I'm completely fine", at_second=22)
        self.assertTrue(contradicted(s))
        self.assertTrue(s.review_flags)

    def test_one_channel_that_held(self):
        s = session()
        for at in (18, 20, 21):
            s.observe_visual("distress", "persistent distress", at_second=at)
        s.hear("I'm fine", at_second=22)
        self.assertTrue(contradicted(s),
                        "a sustained single-channel finding was discarded")

    def test_an_explicitly_persistent_observation_counts_alone(self):
        """
        A frame aggregator that saw something in 8 of 10 frames has already
        done the persistence work. Its verdict is honoured without needing to
        be re-observed.
        """
        s = session()
        s.observe_visual("distress", "distress in 8 of 10 frames", at_second=20,
                         measurements={"persistence": "8_of_10_frames"})
        s.visual_observations[-1]["persistence"] = True
        s.hear("I'm fine", at_second=21)
        self.assertTrue(contradicted(s))


class TestTheStatementSideStillMatters(ClaimTest):
    claim = ("A neutral face is not evidence against a reported symptom. "
             "People feel pain without displaying it.")

    def test_reported_pain_with_a_calm_face_is_not_a_contradiction(self):
        s = session()
        s.hear("I'm in a lot of pain", at_second=10)
        self.assertFalse(contradicted(s))

    def test_distress_without_a_denial_is_not_a_contradiction(self):
        s = session()
        s.observe_visual("distress", "apparent discomfort", at_second=20)
        s.observe_audio("strain", "strained voice", at_second=21)
        s.hear("my head has been hurting since this morning", at_second=22)
        self.assertFalse(contradicted(s),
                         "distress alongside a reported symptom is agreement, "
                         "not disagreement")


class TestItNeverBecomesAnAccusation(ClaimTest):
    claim = ("A discrepancy is a reason to look, not an emergency and not a "
             "judgement about the patient.")

    def test_it_does_not_declare_an_emergency(self):
        s = session()
        s.observe_visual("distress", "apparent facial discomfort", at_second=20)
        s.observe_audio("strain", "strained voice", at_second=21)
        s.hear("I'm completely fine", at_second=22)
        self.assertFalse(s.emergency.active)

    def test_it_does_not_stop_questioning(self):
        """
        A patient underplaying symptoms is exactly who there is MORE to ask.
        """
        s = session()
        s.observe_visual("distress", "apparent facial discomfort", at_second=20)
        s.observe_audio("strain", "strained voice", at_second=21)
        s.hear("I'm completely fine", at_second=22)
        self.assertTrue(s.routine_questions_allowed)

    def test_no_language_of_deception_reaches_the_flag(self):
        s = session()
        s.observe_visual("distress", "apparent facial discomfort", at_second=20)
        s.observe_audio("strain", "strained voice", at_second=21)
        s.hear("I'm completely fine", at_second=22)
        blob = " ".join(str(f) for f in s.review_flags).lower()
        for word in ("lying", "lie", "deception", "dishonest", "faking",
                     "malinger"):
            self.assertNotIn(word, blob,
                             f"the flag accuses the patient: {word!r}")

    def test_the_patients_own_words_survive(self):
        s = session()
        s.observe_visual("distress", "apparent facial discomfort", at_second=20)
        s.observe_audio("strain", "strained voice", at_second=21)
        s.hear("I'm completely fine", at_second=22)
        self.assertIn("I'm completely fine",
                      [t["text"] for t in s.transcript])
