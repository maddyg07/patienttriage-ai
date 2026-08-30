"""
tests/test_emergency_context.py
================================
Phase 21. CLAIM: the emergency gate reasons about whose emergency, and when,
before it trusts a phrase match.

THE FAILURE THIS FILE EXISTS FOR
---------------------------------
core/emergency.py matches phrases and mechanisms by plain substring search.
That is fast and auditable, and until Phase 21 it was also blind to four very
common sentence shapes:

  * "I am NOT having a heart attack."            -- denial
  * "My father had a heart attack last year."    -- someone else, in the past
  * "If someone had a heart attack, ..."         -- a hypothetical question
  * "I had X five years ago but I'm here for Y." -- history, not chief complaint

Each one contains a phrase the gate is built to catch, and none of them is a
current, first-person emergency. A gate that cannot tell them apart is not
"deliberately over-sensitive" (see the module docstring) -- it is simply
wrong, and wrong in the specific direction that trains a nurse to stop
trusting the alarm, which is its own patient-safety failure.

Every test below is a case core/emergency.py could not see before Phase 21.
Genuine emergencies (Tests 8-9) and the Phase-17 mechanism regression
(Test 10 / test_emergency_breadth.py) are re-asserted here too, because a fix
for false positives that costs true positives is not a fix.
"""

from __future__ import annotations

from core.emergency import EmergencyGate
from tests.support import ClaimTest


def gate() -> EmergencyGate:
    return EmergencyGate()


def fired(text: str):
    return {t.trigger_id for t in gate().evaluate(text=text)}


class TestNegationIsNotAnEmergency(ClaimTest):
    claim = "A denied phrase does not fire the gate that phrase belongs to."

    def test_denied_cardiac_phrase(self):
        self.assertEqual(fired("I am not having a heart attack."), set())

    def test_denied_symptom(self):
        self.assertEqual(fired("I don't have chest pain."), set())

    def test_ruled_out_phrase_does_not_fire(self):
        self.assertEqual(fired("They told me a heart attack was ruled out."),
                         set())


class TestHistoricalEventsAreNotCurrentEmergencies(ClaimTest):
    claim = ("A catastrophic thing that happened in the patient's past is "
             "history, not a reason to interrupt today's assessment.")

    def test_years_ago(self):
        self.assertEqual(fired("My father had a heart attack last year."),
                         set())

    def test_past_stroke_reported_as_history(self):
        self.assertEqual(
            fired("The doctor told me last year that I had a stroke."), set())

    def test_history_does_not_mask_the_actual_complaint(self):
        """
        The historical clause must not fire, but the sentence still needs to
        reach a human being for the headache -- just through the normal
        pipeline, not the emergency interrupt. This test only asserts the
        emergency gate; the headache is the assessment's job, not the gate's.
        """
        text = ("I had a heart attack five years ago but I'm here today "
                "because of a mild headache.")
        self.assertEqual(fired(text), set(),
                         "the five-year-old heart attack should not "
                         "interrupt routine questioning")


class TestHypotheticalsAreNotEmergencies(ClaimTest):
    claim = "A patient asking what something would feel like is not reporting it."

    def test_if_someone_had(self):
        self.assertEqual(
            fired("If someone had a heart attack, what would it feel like?"),
            set())

    def test_out_of_curiosity(self):
        self.assertEqual(
            fired("Just out of curiosity, what does chest pain feel like?"),
            set())


class TestThirdPersonIsNotThePatient(ClaimTest):
    claim = "Something that happened to someone else is not the patient's emergency."

    def test_a_friends_stroke(self):
        self.assertEqual(fired("My friend had a stroke last month."), set())


class TestRealEmergenciesStillFire(ClaimTest):
    claim = ("Context-awareness only ever removes false positives -- it must "
             "never cost a genuine, current, first-person emergency.")

    def test_airway_and_consciousness(self):
        triggers = fired("I can't breathe and I'm about to pass out.")
        self.assertIn("E1_airway_breathing", triggers)
        self.assertIn("E2_consciousness", triggers)

    def test_chest_pressure_and_breathlessness(self):
        triggers = fired("I am having severe chest pressure and I can't breathe.")
        self.assertTrue(triggers, "a genuine combined emergency stopped firing")

    def test_mechanism_regression_still_fires(self):
        """The Phase 17 fatal-car-accident case, re-asserted after Phase 21."""
        text = ("I've been in a fatal car accident my friend is dead and my "
                "leg is amputated due to the accident")
        triggers = fired(text)
        self.assertIn("M1_major_trauma", triggers)
        self.assertIn("M3_limb_catastrophe", triggers)
        self.assertIn("M7_death_at_scene", triggers)

    def test_current_denial_does_not_suppress_a_later_real_complaint(self):
        """
        Negation is scoped to its own clause. Denying one thing and then
        reporting a different, genuine emergency in the same breath must
        still fire on the second half.
        """
        text = "I am not having a heart attack, but I cannot breathe at all."
        triggers = fired(text)
        self.assertIn("E1_airway_breathing", triggers)


class TestSuppressionsAreAuditable(ClaimTest):
    claim = ("A phrase the gate declined to act on is recorded, not silently "
             "dropped -- so a nurse or a debug view can see why.")

    def test_suppressed_negation_is_recorded(self):
        g = gate()
        g.evaluate(text="I am not having a heart attack.")
        self.assertTrue(g.last_suppressions,
                        "a negated cardiac phrase left no audit trail")
        self.assertEqual(g.last_suppressions[0]["reason"], "negated")

    def test_suppressions_reset_between_calls(self):
        g = gate()
        g.evaluate(text="I am not having a heart attack.")
        self.assertTrue(g.last_suppressions)
        g.evaluate(text="hello")
        self.assertEqual(g.last_suppressions, [])
