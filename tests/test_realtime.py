"""
tests/test_realtime.py
======================
CLAIM: The emergency gate runs before anything that can be slow or fail, it
stops routine questioning, and nothing automated stands it down.

CLAIM: There is one assessment state. The patient screen and the nurse screen
are views of it, and a nurse override reaches both without either screen
holding a copy.

WHY THE ORDER MATTERS MORE THAN THE FEATURE
-------------------------------------------
Every one of these could be built in a way that demos identically and fails in
the room. The gate could run after extraction, and work perfectly until the
model has a slow night. Each screen could hold its own state, and agree
perfectly until a nurse edits during a transcript fragment. The tests here are
about ORDER and OWNERSHIP, because those are what a demo cannot show you.
"""

from __future__ import annotations

from core.ai.local_provider import LocalProvider
from core.ai.provider import Extraction, LanguageProvider
from core.notes import generate
from core.session import MULTIMODAL_WINDOW_SECONDS, ClinicSession
from tests.support import ClaimTest, has_teeth


class BrokenProvider(LanguageProvider):
    """A provider that always fails. Stands in for a bad night at the API."""

    name = "broken"
    kind = "model"

    def available(self) -> bool:
        return False

    def extract(self, text, context=None) -> Extraction:
        raise RuntimeError("the model is down")


def session(provider=None) -> ClinicSession:
    return ClinicSession("TEST-1", provider or LocalProvider())


class TestTheGateRunsFirst(ClaimTest):
    claim = ("The emergency gate runs on the raw words, before extraction, "
             "before scoring, before anything that can be slow.")

    def test_an_emergency_fires_even_when_extraction_is_completely_broken(self):
        """
        The test this file exists for.

        A gate that runs after extraction works right up until the model is
        down, and then a patient saying "I can't breathe" is a patient the
        system did not notice. The rule layer has no dependencies on purpose.
        """
        s = session(BrokenProvider())
        try:
            s.hear("I can't breathe and I'm going to pass out", at_second=5)
        except RuntimeError:
            pass                     # extraction blew up, as designed
        self.assertTrue(s.emergency.active,
                        "extraction failing took the emergency gate with it")
        self.assertIn("E1_airway_breathing",
                      [t.trigger_id for t in s.emergency.triggers])

    def test_an_emergency_stops_routine_questioning(self):
        s = session()
        s.hear("I've had a mild headache since this morning", at_second=3)
        self.assertTrue(s.routine_questions_allowed)
        s.hear("I can't breathe", at_second=9)
        self.assertFalse(s.routine_questions_allowed,
                         "questioning continued after the gate fired")

    def test_the_triggering_words_are_recorded_verbatim(self):
        s = session()
        s.hear("honestly I think I'm having a heart attack", at_second=11)
        trigger = next(t for t in s.emergency.triggers
                       if t.trigger_id == "E3_cardiac")
        self.assertIn("heart attack", trigger.evidence)
        self.assertTrue(trigger.at_clock)
        self.assertEqual(trigger.at_second, 11)

    def test_ordinary_speech_does_not_trigger(self):
        s = session()
        for line in ("I've had a mild headache since this morning",
                     "my head is absolutely killing me",
                     "I've been feeling nauseous and a bit dizzy"):
            s.hear(line, at_second=4)
        self.assertFalse(s.emergency.active,
                         f"ordinary speech triggered: "
                         f"{[t.trigger_id for t in s.emergency.triggers]}")

    def test_nothing_automated_stands_an_emergency_down(self):
        """
        The Ratchet's asymmetry, applied to the gate. Once fired, only a named
        human clears it, with a reason.
        """
        s = session()
        s.hear("I can't breathe", at_second=5)
        self.assertTrue(s.emergency.active)
        for benign in ("actually I feel much better now", "I'm fine really",
                       "it has completely passed"):
            s.hear(benign, at_second=30)
        self.assertTrue(s.emergency.active,
                        "the emergency cleared itself when the patient "
                        "said they felt better")

        # Every active trigger has to be dismissed, individually, each with a
        # reason. One sentence can legitimately fire several layers and
        # clearing one of them is not clearing the emergency.
        for trigger in list(s.emergency.active_triggers):
            s.nurse_dismiss_trigger(trigger.trigger_id, "settled on arrival, "
                                    "speaking full sentences", nurse="N. Sharma")
        self.assertFalse(s.emergency.active)
        dismissal = next(t for t in s.emergency.triggers
                         if t.trigger_id == "E1_airway_breathing")
        self.assertEqual(dismissal.dismissed_by, "N. Sharma")
        self.assertTrue(dismissal.dismiss_reason)

    @has_teeth
    def test_the_gate_can_actually_fire(self):
        s = session()
        s.hear("I can't breathe", at_second=1)
        self.assertTrue(s.emergency.active,
                        "if this fails every assertion above passes vacuously")


class TestOneStateTwoViews(ClaimTest):
    claim = ("One assessment state. Both screens are views of it and neither "
             "holds a copy.")

    def test_every_change_reaches_every_subscriber(self):
        s = session()
        patient_frames, nurse_frames = [], []
        s.subscribe(patient_frames.append)
        s.subscribe(nurse_frames.append)

        s.hear("my chest is killing me", at_second=6)
        s.nurse_set_severity("chest pain", 9, nurse="N. Sharma")

        self.assertEqual(len(patient_frames), len(nurse_frames))
        self.assertGreaterEqual(len(nurse_frames), 2)
        self.assertEqual(patient_frames[-1]["symptoms"],
                         nurse_frames[-1]["symptoms"],
                         "the two views diverged")

    def test_the_nurse_decision_and_the_ai_estimate_are_both_kept(self):
        """
        Overwriting the machine's answer with the human's throws away the
        disagreement, and the disagreements are where any future validation
        would have to start.
        """
        s = session()
        s.hear("my chest hurts, about a 4 out of 10", at_second=4)
        s.nurse_set_severity("chest pain", 9, nurse="N. Sharma")
        entry = s.ledger["chest pain"]
        self.assertEqual(entry.nurse_severity, 9)
        self.assertEqual(entry.severity, 9)
        self.assertTrue(entry.overridden)
        self.assertNotEqual(entry.ai_severity, 9,
                            "the nurse's number overwrote the AI's")

    def test_a_removed_symptom_is_marked_and_never_deleted(self):
        s = session()
        s.hear("my chest is killing me", at_second=3)
        s.nurse_remove_symptom("chest pain", "referred pain from the rib "
                               "injury", nurse="N. Sharma")
        entry = s.ledger["chest pain"]
        self.assertFalse(entry.active)
        self.assertEqual(entry.removed_by, "N. Sharma")
        self.assertTrue(any("referred pain" in e.text for e in s.events))

    def test_every_override_writes_an_attributed_event(self):
        s = session()
        s.hear("my chest is killing me", at_second=3)
        s.nurse_set_severity("chest pain", 8, nurse="N. Sharma")
        s.nurse_add_symptom("breathlessness", 6, nurse="N. Sharma")
        overrides = [e for e in s.events if e.kind == "override"]
        self.assertEqual(len(overrides), 2)
        for event in overrides:
            self.assertEqual(event.actor, "N. Sharma")
            self.assertTrue(event.at_clock)


class TestMultimodalIsTimeAligned(ClaimTest):
    claim = ("Two signals contradict each other only when they are close "
             "enough in time to be about the same moment.")

    def test_a_contradiction_inside_the_window_is_recorded(self):
        s = session()
        s.observe_visual("distress", "apparent facial discomfort", at_second=40)
        s.observe_audio("distress", "strained voice quality", at_second=41)
        s.hear("I'm completely fine", at_second=43)
        self.assertTrue(any(e.kind == "contradiction" for e in s.events))
        self.assertEqual(len(s.review_flags), 1)

    def test_a_contradiction_is_a_review_flag_and_not_an_emergency(self):
        """
        The first version routed this through the emergency gate. A patient who
        says "I'm fine" while grimacing would get a resus alert, and a few of
        those a shift is how alarm fatigue is built and how the one real
        emergency gets scrolled past.

        It is a review flag: prominent to the nurse, invisible to the patient,
        and questioning CONTINUES -- a patient underplaying their symptoms is
        exactly who there is more to ask.
        """
        s = session()
        s.observe_visual("distress", "apparent facial discomfort", at_second=40)
        s.observe_audio("distress", "strained voice quality", at_second=41)
        s.hear("I'm completely fine", at_second=43)
        self.assertFalse(s.emergency.active,
                         "a contradiction declared a full emergency")
        self.assertTrue(s.routine_questions_allowed,
                        "questioning stopped for a patient who is underplaying "
                        "symptoms, which is the opposite of what should happen")
        self.assertEqual(s.review_flags[0]["status"], "unreviewed")

    def test_an_old_observation_does_not_contradict_a_new_statement(self):
        """
        The failure a naive implementation makes. A distress observation from
        forty seconds ago is not evidence about what the patient just said, and
        treating it as one manufactures a contradiction out of two unrelated
        moments.
        """
        s = session()
        far = MULTIMODAL_WINDOW_SECONDS * 4
        s.observe_visual("distress", "apparent facial discomfort", at_second=5)
        s.observe_audio("distress", "strained voice quality", at_second=6)
        s.hear("I'm completely fine", at_second=5 + far)
        self.assertFalse(any(e.kind == "contradiction" for e in s.events),
                         "a stale observation contradicted a fresh statement")

    def test_a_contradiction_never_deletes_the_patients_statement(self):
        s = session()
        s.observe_visual("distress", "apparent facial discomfort", at_second=20)
        s.observe_audio("distress", "strained voice", at_second=21)
        s.hear("I'm completely fine", at_second=22)
        said = [t["text"] for t in s.transcript]
        self.assertIn("I'm completely fine", said,
                      "the patient's own words were discarded")
        self.assertTrue(any(e.kind == "contradiction" for e in s.events))

    def test_one_channel_alone_is_not_a_contradiction(self):
        s = session()
        s.observe_visual("distress", "apparent facial discomfort", at_second=20)
        s.hear("I'm completely fine", at_second=21)
        self.assertEqual(s.review_flags, [],
                         "one observed channel was enough to call a "
                         "contradiction; it must be rare enough to mean "
                         "something")


class TestNotesAreAssembledNotInvented(ClaimTest):
    claim = ("Every line of the notes traces to the event log. Nothing is "
             "paraphrased into existence and there is no diagnosis.")

    def test_the_notes_quote_only_what_was_said(self):
        s = session()
        s.hear("my chest is killing me and I can't breathe", at_second=8)
        notes = generate(s.snapshot())
        self.assertIn("chest is killing me", notes)
        for invented in ("myocardial", "infarction", "diagnosis:",
                         "IMPRESSION", "likely cause"):
            self.assertNotIn(invented, notes,
                             f"the notes contain '{invented}'")

    def test_the_notes_carry_the_sections_a_nurse_needs(self):
        s = session()
        s.hear("my chest is killing me", at_second=5)
        notes = generate(s.snapshot())
        for heading in ("CHIEF CONCERN", "SYMPTOMS", "TIMELINE",
                        "MULTIMODAL OBSERVATIONS", "RISK AND ESCALATION",
                        "IMPORTANT STATEMENTS", "ITEMS REQUIRING NURSE REVIEW"):
            self.assertIn(heading, notes, f"missing section: {heading}")

    def test_the_notes_declare_themselves_a_draft(self):
        notes = generate(session().snapshot())
        self.assertIn("AI-GENERATED DRAFT", notes)
        self.assertIn("not a diagnosis", notes.lower())

    def test_stopped_questioning_is_flagged_for_review(self):
        s = session()
        s.hear("I can't breathe", at_second=4)
        notes = generate(s.snapshot())
        self.assertIn("Routine questioning was stopped", notes,
                      "the notes do not say the history is incomplete")

    def test_a_degraded_extraction_is_flagged_for_review(self):
        """
        A real degradation, not a flag set by hand: the model provider with no
        key falls back and marks the extraction, and the session picks that up
        from the extraction rather than being told.
        """
        from core.ai.model_provider import AnthropicProvider
        s = session(AnthropicProvider(api_key=""))
        s.hear("my chest hurts", at_second=3)
        self.assertTrue(s.degraded,
                        "the session did not notice it was running degraded")
        self.assertIn("fallback matcher", generate(s.snapshot()))
