"""
tests/test_emergency_breadth.py
===============================
CLAIM: The gate covers categories of emergency, not a list of medical phrases
somebody happened to think of.

THE FAILURE THIS FILE EXISTS FOR
--------------------------------
A patient said, into a live microphone:

    "I've been in a fatal car accident, my friend is dead and my leg is
     amputated due to the accident."

The system scored 0/100 and displayed NORMAL.

The gate at the time had ten phrase groups and every one of them was medical:
cardiac, respiratory, neurological, allergic, overdose. The patient had
described what HAPPENED TO THEM rather than what they were feeling, and a gate
built entirely out of symptom language had nothing to match.

Adding "amputated" to the list would have fixed that sentence and left the next
one broken. What was missing was a whole way of looking: an emergency is not
only a symptom, and a triage system that can only recognise symptoms cannot
recognise a road traffic accident.

Every test below is a category that version could not see.
"""

from __future__ import annotations

from core.ai.local_provider import LocalProvider
from core.emergency import EmergencyGate
from core.session import ClinicSession
from tests.support import ClaimTest, has_teeth

SCREENSHOT = ("I've been in a fatal car accident my friend is dead and my leg "
              "is amputated due to the accident")


def gate() -> EmergencyGate:
    return EmergencyGate()


def fired(text: str):
    return {t.trigger_id for t in gate().evaluate(text=text)}


class TestTheCaseThatFailed(ClaimTest):
    claim = ("The fatal-car-accident sentence reaches a nurse. It scored zero "
             "and displayed NORMAL in the version this test was written for.")

    def test_the_exact_sentence_declares_an_emergency(self):
        session = ClinicSession("REGRESSION", LocalProvider())
        session.hear(SCREENSHOT, at_second=14)
        self.assertTrue(session.emergency.active,
                        "the fatal-car-accident sentence is still not an "
                        "emergency")
        self.assertFalse(session.routine_questions_allowed)

    def test_it_fires_on_the_mechanism_not_on_a_symptom(self):
        """
        The sentence contains no symptom at all. If it only passes because
        somebody added a symptom term for 'amputated', the underlying blindness
        is still there.
        """
        triggers = gate().evaluate(text=SCREENSHOT)
        layers = {t.layer for t in triggers}
        self.assertIn("mechanism", layers,
                      f"no mechanism trigger fired; layers were {layers}")

    def test_each_element_of_the_sentence_fires_on_its_own(self):
        self.assertIn("M1_major_trauma", fired("I was in a car accident"))
        self.assertIn("M3_limb_catastrophe", fired("my leg is amputated"))
        self.assertIn("M7_death_at_scene", fired("my friend is dead"))


class TestTheCategoriesTheOldGateCouldNotSee(ClaimTest):
    claim = "Emergencies that are not symptoms still reach the gate."

    def test_blunt_trauma(self):
        for line in ("I fell off a ladder onto concrete",
                     "a car hit me and I was thrown from my bike",
                     "I was run over", "my hand was crushed in the machine",
                     "I fell down the stairs"):
            self.assertTrue(fired(line), f"no trigger for: {line}")

    def test_penetrating_trauma(self):
        for line in ("someone stabbed me", "I've been shot",
                     "the glass went in deep"):
            self.assertTrue(fired(line), f"no trigger for: {line}")

    def test_limb_catastrophe(self):
        for line in ("my finger was cut clean off", "I lost my arm",
                     "the bone is sticking out of my leg"):
            self.assertTrue(fired(line), f"no trigger for: {line}")

    def test_burns_and_electrical(self):
        for line in ("I got badly burnt at work", "it was an acid burn",
                     "I was electrocuted", "my sleeve caught fire"):
            self.assertTrue(fired(line), f"no trigger for: {line}")

    def test_head_and_spine(self):
        for line in ("I was knocked unconscious", "I can't feel my legs",
                     "I landed on my head"):
            self.assertTrue(fired(line), f"no trigger for: {line}")

    def test_obstetric(self):
        for line in ("I can't feel the baby moving",
                     "my waters have broken and there's blood"):
            self.assertTrue(fired(line), f"no trigger for: {line}")

    def test_self_harm_and_poisoning(self):
        for line in ("I took all the tablets", "I tried to kill myself",
                     "I want to die"):
            self.assertTrue(fired(line), f"no trigger for: {line}")

    def test_asphyxia_and_drowning(self):
        for line in ("he was pulled from the water",
                     "there was a lot of smoke inhalation"):
            self.assertTrue(fired(line), f"no trigger for: {line}")

    def test_extreme_severity_language_about_the_body(self):
        for line in ("the pain in my stomach is absolutely unbearable",
                     "this is the worst pain of my life"):
            self.assertTrue(fired(line), f"no trigger for: {line}")


class TestItDoesNotFireOnEverything(ClaimTest):
    claim = ("Breadth is not the same as firing constantly. A gate that "
             "triggers on ordinary speech is a gate nobody reads.")

    def test_ordinary_complaints_do_not_trigger(self):
        for line in ("I've had a mild headache since this morning",
                     "my head is absolutely killing me",
                     "I've been feeling nauseous and a bit dizzy",
                     "I twisted my ankle playing football",
                     "I've had a sore throat for two days",
                     "I need a repeat prescription",
                     "my daughter has a temperature of 38"):
            self.assertEqual(fired(line), set(),
                             f"ordinary speech triggered: {line} -> {fired(line)}")

    def test_an_intensifier_alone_is_not_an_emergency(self):
        """
        "severe" next to a body part is a patient at the top of their scale.
        "severe" next to traffic is a Tuesday. Without the anchor requirement
        the severity layer fires on half of ordinary conversation.
        """
        self.assertEqual(fired("the traffic was severe this morning"), set())
        self.assertEqual(fired("it was a terrible day at work"), set())
        self.assertTrue(fired("the pain in my chest is severe"))

    @has_teeth
    def test_the_negative_cases_are_reachable(self):
        """
        If the gate returned nothing for everything, every assertion above
        would pass. One positive and one negative, in the same test.
        """
        self.assertEqual(fired("I've had a mild headache"), set())
        self.assertTrue(fired("I can't breathe"))


class TestNoLayerCanSilenceAnother(ClaimTest):
    claim = ("Each layer fires alone. The union is taken, not a consensus, so "
             "a model being down cannot remove a rule trigger.")

    def test_a_mechanism_trigger_survives_with_no_model_signals(self):
        triggers = gate().evaluate(text=SCREENSHOT, model_phrases=[])
        self.assertTrue(triggers)

    def test_a_model_signal_fires_with_no_rule_match(self):
        """
        The layer that exists for the sentence nobody wrote down. A phrase with
        no rule behind it must still reach the nurse when a model flags it.
        """
        triggers = gate().evaluate(
            text="the thing in my belly is doing something very wrong",
            model_phrases=[{"phrase": "the thing in my belly",
                            "why": "may indicate an abdominal catastrophe"}])
        self.assertTrue(any(t.layer == "model" for t in triggers))

    def test_observations_fire_with_no_speech_at_all(self):
        triggers = gate().evaluate(text="", observations={"spo2": 84})
        self.assertIn("O2_critical_hypoxia", {t.trigger_id for t in triggers})


class TestAnOpenGateFloorsTheBand(ClaimTest):
    claim = ("An active emergency floors the band at CODE. It never inflates "
             "the score to justify it.")

    def test_a_trauma_with_no_scoreable_symptom_still_reaches_code(self):
        """
        The console showed "risk 0/100, L1 WATCH, status EMERGENCY" for a
        patient describing an amputation. Architecturally correct and
        completely indefensible on a screen somebody reads in seconds.
        """
        session = ClinicSession("FLOOR", LocalProvider())
        session.hear(SCREENSHOT, at_second=14)
        result = session.last_result
        self.assertEqual(result["band_code"], "L4")
        self.assertTrue(any("EMERGENCY_GATE" in r
                            for r in result["safety_rules"]))

    def test_the_score_underneath_is_left_honest(self):
        """
        The floor raises the band. It does not touch the number, because the
        number is right: no scoreable symptom was described. Inflating it to
        make the band look earned would put a fiction in the audit trail.
        """
        session = ClinicSession("FLOOR2", LocalProvider())
        session.hear("my leg is amputated", at_second=4)
        self.assertEqual(session.last_result["risk_score"], 0.0)
        self.assertEqual(session.last_result["band_code"], "L4")

    def test_the_floor_only_raises(self):
        session = ClinicSession("FLOOR3", LocalProvider())
        session.set_observations(heart_rate=118, respiratory_rate=26, spo2=91,
                                 consciousness="alert")
        session.hear("my chest is crushing me and I can't breathe", at_second=6)
        before = session.last_result["band_code"]
        self.assertEqual(before, "L4")
        for trigger in list(session.emergency.active_triggers):
            session.nurse_dismiss_trigger(trigger.trigger_id, "reassessed",
                                          nurse="N. Sharma")
        self.assertFalse(session.emergency.active)
        # The ratchet holds the band even after the gate stands down. Only a
        # nurse changing the acuity itself can lower it.
        self.assertEqual(session.last_result["band_code"], "L4")
