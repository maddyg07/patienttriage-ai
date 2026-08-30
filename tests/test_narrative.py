"""
tests/test_narrative.py
=======================
CLAIM: A patient describing a symptom in ordinary words has described it. The
reader does not require them to use clinical vocabulary.

CLAIM: What a patient believes is happening is recorded as a concern, weighted
as a concern, and never becomes a finding or a diagnosis.

WHY THESE EXIST
---------------
A patient said, out loud, into a live microphone:

    "I am suffering from a severe heart attack, my heart is paining a lot,
     I have been in an accident and my jaw has been deformed."

The console recognised nothing. Not the chest pain, not the concern, not the
accident that explained the facial difference the operator was about to
misclassify as new. Every clinically relevant word went past a matcher that
wanted the phrase "chest pain" and would accept nothing else.

Each test below is one sentence from that failure, or one just like it.
"""

from __future__ import annotations

from core.intake_bridge import assess_payload
from core.narrative import NarrativeReader
from tests.support import ClaimTest, has_teeth

TRANSCRIPT = ("I am suffering from a severe Heart Attack my heart is paining "
              "a lot I have been in accident and my jaw has been deformed")


class TestOrdinaryWordsAreUnderstood(ClaimTest):
    claim = ("A body part near a word for hurting is a symptom, however the "
             "patient phrases it.")

    def setUp(self):
        self.r = NarrativeReader()

    def test_the_sentence_that_recognised_nothing(self):
        f = self.r.read_full(TRANSCRIPT)
        self.assertIn("chest pain", f.reported,
                      "'my heart is paining a lot' still does not reach chest pain")
        self.assertTrue(any(c["concern"] == "heart attack" for c in f.concerns))
        self.assertTrue(any(h["hint"] == "accident" for h in f.baseline_hints))

    def test_many_ways_of_saying_the_same_thing_all_land(self):
        for phrasing in ("my heart is paining a lot",
                         "my chest is killing me",
                         "chest hurts",
                         "pain in my chest",
                         "tightness across my chest",
                         "crushing pressure in my chest",
                         "my chest feels heavy",
                         "burning in my chest"):
            self.assertIn("chest pain", self.r.read_full(phrasing).reported,
                          f"'{phrasing}' was not understood")

    def test_other_sites_work_the_same_way(self):
        self.assertIn("abdominal pain", self.r.read_full("my tummy hurts").reported)
        self.assertIn("abdominal pain", self.r.read_full("stomach is aching").reported)
        self.assertIn("headache", self.r.read_full("my head is pounding").reported)

    def test_every_term_the_reader_produces_can_be_scored(self):
        """
        A reader with a private word list silently collects findings that never
        reach the calculation. The vocabulary must BE the engine's.
        """
        from core.risk_engine import WEIGHTS_FILE, _load
        scoreable = set(_load(WEIGHTS_FILE)["symptoms"].keys())
        self.assertEqual(set(self.r.vocabulary), scoreable)
        for term in list(self.r.sites) + list(self.r.phrases):
            if term in self.r.vocabulary:
                continue
            self.assertNotIn(term, self.r.read_full("chest hurts").reported)

    def test_every_recognised_term_carries_the_words_that_produced_it(self):
        """A shallow matcher makes wrong matches. They must be visible."""
        f = self.r.read_full(TRANSCRIPT)
        for term in f.reported:
            self.assertTrue(f.evidence.get(term),
                            f"'{term}' was recognised with no evidence snippet")

    @has_teeth
    def test_the_matcher_does_not_simply_match_everything(self):
        """
        A reader generous enough to pass the tests above could be one that
        returns a symptom for any sentence. It must not.
        """
        for benign in ("I came to collect a prescription for my mother",
                       "the parking outside was very difficult",
                       "I am here about an appointment next Tuesday"):
            self.assertEqual(self.r.read_full(benign).reported, [],
                             f"'{benign}' produced a symptom")


class TestNegationSurvivesTheRewrite(ClaimTest):
    claim = "A denial applies to its own clause and is kept, never discarded."

    def setUp(self):
        self.r = NarrativeReader()

    def test_a_denial_at_the_very_start_of_a_sentence(self):
        f = self.r.read_full("no chest pain but my head is pounding")
        self.assertIn("chest pain", f.denied)
        self.assertNotIn("chest pain", f.reported)
        self.assertIn("headache", f.reported)

    def test_a_denial_does_not_leak_past_a_comma(self):
        f = self.r.read_full("denies breathlessness, temperature for three days")
        self.assertIn("breathlessness", f.denied)
        self.assertIn("fever", f.reported)

    def test_a_term_reported_anywhere_outranks_a_denial_elsewhere(self):
        f = self.r.read_full("no chest pain earlier. my chest is killing me now")
        self.assertIn("chest pain", f.reported)
        self.assertNotIn("chest pain", f.denied)


class TestConcernsAreNotFindings(ClaimTest):
    claim = ("A stated concern carries its own weight and never becomes a "
             "symptom the patient did not describe.")

    def setUp(self):
        self.r = NarrativeReader()

    def test_naming_a_disease_adds_no_symptom(self):
        f = self.r.read_full("I think I am having a heart attack")
        self.assertEqual(f.reported, [],
                         "naming a condition manufactured a finding")
        self.assertTrue(any(c["concern"] == "heart attack" for c in f.concerns))

    def test_a_concern_alone_cannot_drive_the_band(self):
        """
        Modest weight, on purpose. Somebody saying the words with entirely
        normal observations must not reach an urgent band on the words alone.
        """
        result = assess_payload({
            "patient_id": "LIVE-C", "age_years": 30, "history_tier": "partial",
            "transcript": "I think I am having a heart attack",
            "heart_rate": 74, "respiratory_rate": 15, "spo2": 99,
            "temperature_c": 36.7, "systolic_bp": 120, "diastolic_bp": 78,
            "consciousness": "alert", "facial_capture_status": "ok",
            "asymmetry_observed": "no", "droop_observed": "no",
            "voice_capture_status": "ok",
        })
        self.assertIn("heart attack", result["stated_concerns"])
        self.assertIn(result["band_code"], ("L1", "L2"),
                      "a self-declared condition with normal observations "
                      "reached an urgent band on the words alone")

    def test_a_concern_alongside_real_findings_does_raise_the_band(self):
        """The other half. Ignoring the sentence would be its own failure."""
        without = assess_payload({
            "patient_id": "LIVE-A", "age_years": 20, "history_tier": "zero",
            "transcript": "my heart is paining a lot",
            "heart_rate": 140, "spo2": 95, "temperature_c": 38.3,
            "pain_score": 8, "consciousness": "alert",
            "facial_capture_status": "ok", "asymmetry_observed": "no",
            "droop_observed": "no", "voice_capture_status": "ok"})
        with_concern = assess_payload({
            "patient_id": "LIVE-B", "age_years": 20, "history_tier": "zero",
            "transcript": "I am having a severe heart attack, my heart is "
                          "paining a lot",
            "heart_rate": 140, "spo2": 95, "temperature_c": 38.3,
            "pain_score": 8, "consciousness": "alert",
            "facial_capture_status": "ok", "asymmetry_observed": "no",
            "droop_observed": "no", "voice_capture_status": "ok"})
        self.assertGreater(with_concern["risk_score"], without["risk_score"])

    def test_a_denied_concern_is_not_recorded(self):
        f = self.r.read_full("I am not having a heart attack, I just feel tired")
        self.assertEqual(f.concerns, [])


class TestBaselineHintsPromptButNeverAnswer(ClaimTest):
    claim = ("Phrases explaining a facial difference as pre-existing prompt "
             "the baseline question. They never answer it and never score.")

    def setUp(self):
        self.r = NarrativeReader()

    def test_an_accident_in_the_patients_own_words_is_surfaced(self):
        f = self.r.read_full("I have been in an accident and my jaw is deformed")
        hints = {h["hint"] for h in f.baseline_hints}
        self.assertTrue(hints & {"accident", "long standing"})

    def test_congenital_and_burn_phrasings_are_surfaced(self):
        self.assertIn("congenital", {h["hint"] for h in
                      self.r.read_full("my face has been like this since birth")
                          .baseline_hints})
        self.assertIn("burn", {h["hint"] for h in
                      self.r.read_full("I had an acid burn years ago")
                          .baseline_hints})

    def test_a_hint_does_not_set_the_baseline_or_move_the_score(self):
        """
        The hint is a prompt for the operator. If it silently answered the
        baseline question it would be doing the single most consequential
        thing in this system without anybody deciding it.
        """
        base = dict(patient_id="LIVE-H", age_years=40, history_tier="zero",
                    heart_rate=88, respiratory_rate=17, spo2=97,
                    temperature_c=36.8, systolic_bp=128, diastolic_bp=78,
                    consciousness="alert", facial_capture_status="ok",
                    asymmetry_observed="yes", droop_observed="yes",
                    voice_capture_status="ok")
        plain = assess_payload(dict(base, transcript="I feel unwell"))
        hinted = assess_payload(dict(
            base, transcript="I feel unwell, I was in an accident years ago"))
        self.assertEqual(plain["risk_score"], hinted["risk_score"],
                         "a baseline hint moved the score")
        self.assertEqual(plain["band_code"], hinted["band_code"])
