"""
tests/test_unscored_concepts.py
===============================
Phase 21. CLAIM: a clinical concept the extractor genuinely recognised is
never silently discarded just because data/risk_weights.json has no weight
for it.

THE FAILURE THIS FILE EXISTS FOR
---------------------------------
core/ai/model_provider.py prompts the model with an explicit ALLOWED TERMS
list built from the keys of the `symptoms` block in data/risk_weights.json,
and then filtered the response against the same list. Any term outside it was
turned into None and dropped:

    if term not in allowed:
        dropped.append(term)
        return None

So a model that correctly understood "I've been coughing up blood" as
hemoptysis produced nothing at all, because nobody had yet written a weight
for hemoptysis. The console showed no finding. The nurse never saw the word.
The audit log had no record that the system had understood and then thrown
away the most alarming thing in the sentence.

That is the "unknown concept = discard it" behaviour the brief names
explicitly as the thing to eliminate.

WHAT REPLACED IT, AND WHAT DID NOT CHANGE
-----------------------------------------
The term is kept, flagged `scoreable=False`, logged under its own event kind,
and broken out in the session snapshot for the nurse.

What deliberately did NOT change: the risk engine still scores only against
its own weight keys, and `_payload()` passes only scoreable terms to it. An
unscored concept therefore contributes exactly zero points -- the same zero it
contributed when it was being discarded. This is important. The fix makes the
gap VISIBLE; it does not paper over the gap by inventing a number for a
concept nobody has calibrated. "Recognised but not independently scored, nurse
review" is an honest state. A made-up weight would not be.
"""

from __future__ import annotations

from core.ai.local_provider import LocalProvider
from core.ai.provider import ExtractedSymptom, Extraction
from core.session import ClinicSession
from tests.support import ClaimTest


class StubProvider(LocalProvider):
    """
    A provider standing in for a model that recognised one weighted concept
    and one unweighted one. Deterministic, so this file tests the pipeline
    rather than a model's mood.
    """

    name = "stub"

    def extract(self, text, context=None):
        return Extraction(
            symptoms=[
                ExtractedSymptom(term="chest pain", normalised="chest pain",
                                 said=text, confidence=0.9, scoreable=True),
                ExtractedSymptom(term="hemoptysis",
                                 normalised="coughing up blood",
                                 said=text, confidence=0.93, scoreable=False),
            ],
            chief_complaint=text, provider="stub")


def session() -> ClinicSession:
    s = ClinicSession("UNSCORED", StubProvider())
    s.hear("my chest hurts and there was blood when I coughed", at_second=5)
    return s


class TestTheConceptSurvives(ClaimTest):
    claim = ("An unweighted clinical concept reaches the ledger and the "
             "nurse's screen instead of being dropped at the boundary.")

    def test_it_is_in_the_ledger(self):
        self.assertIn("hemoptysis", session().ledger)

    def test_it_is_in_the_snapshot(self):
        snap = session().snapshot()
        terms = [s["term"] for s in snap["symptoms"]]
        self.assertIn("hemoptysis", terms)

    def test_it_is_broken_out_for_the_nurse(self):
        snap = session().snapshot()
        unscored = [s["term"] for s in snap["unscored_concepts"]]
        self.assertEqual(unscored, ["hemoptysis"])

    def test_it_is_flagged_for_review(self):
        entry = session().ledger["hemoptysis"].as_dict()
        self.assertFalse(entry["scoreable"])
        self.assertTrue(entry["needs_review"])

    def test_the_patients_own_words_are_kept(self):
        """A nurse correcting a wrong extraction needs to see what produced it."""
        self.assertTrue(session().ledger["hemoptysis"].said)


class TestItDoesNotQuietlyBecomePoints(ClaimTest):
    claim = ("Surfacing an unweighted concept must not also invent a weight "
             "for it. Visibility is the fix; a fabricated number is not.")

    def test_it_is_withheld_from_the_scoring_payload(self):
        payload = session()._payload()
        self.assertIn("chest pain", payload["added_symptoms"])
        self.assertNotIn("hemoptysis", payload["added_symptoms"],
                         "an uncalibrated concept was passed to the engine")

    def test_weighted_terms_still_reach_the_engine(self):
        self.assertTrue(session()._payload()["added_symptoms"])


class TestItIsAuditable(ClaimTest):
    claim = ("'We understood this and have no rule for it' is logged as its "
             "own kind of event, not blurred into a normal finding.")

    def test_a_distinct_event_kind_is_written(self):
        kinds = [e.kind for e in session().events]
        self.assertIn("unscored_concept", kinds)

    def test_a_weighted_finding_still_logs_as_a_symptom(self):
        kinds = [e.kind for e in session().events]
        self.assertIn("symptom", kinds)


class TestTheBoundaryStillHolds(ClaimTest):
    claim = ("Keeping unweighted concepts does not widen what a provider is "
             "allowed to return. Clinical authority stays in the engine.")

    def test_the_provider_still_cannot_return_a_verdict(self):
        from core.ai.provider import reject_clinical_verdicts, ProviderUnavailable
        with self.assertRaises(Exception):
            reject_clinical_verdicts({"triage_band": "L4", "symptoms": []})
