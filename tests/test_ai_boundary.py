"""
tests/test_ai_boundary.py
=========================
CLAIM: A model reads language. The deterministic engine does the scoring. No
provider can return a clinical verdict, and no prompt change or model update
can move that line without failing the build.

WHY THIS FILE IS THE MOST IMPORTANT ONE IN THE AI PACKAGE
---------------------------------------------------------
Handing symptom extraction to a language model is the right call: no phrase
list covers the hundreds of ways somebody says their chest hurts, and Phase 17
proved it by failing on "my heart is paining a lot".

Handing the BAND to a language model would undo everything this project has
built. The score is defensible because every point traces to a line in
data/risk_weights.json that anybody can open and argue with. A model that
returns "L4" has no such trace, gives a different answer on Tuesday, and cannot
be cross-examined.

The prompt says so. A prompt is a request. These tests, and the boundary check
they exercise, are the control.
"""

from __future__ import annotations

from core.ai import get_provider
from core.ai.claude_provider import ClaudeProvider
from core.ai.local_provider import LocalProvider
from core.ai.provider import (
    Extraction,
    ExtractedSymptom,
    LanguageProvider,
    ProviderUnavailable,
    reject_clinical_verdicts,
)
from tests.support import ClaimTest, has_teeth


class TestNoProviderMayReturnAVerdict(ClaimTest):
    claim = "A provider returning a band, a score or a diagnosis is rejected."

    def test_a_band_is_rejected(self):
        for verdict in ({"band": "L4"}, {"triage_band": "CODE"},
                        {"acuity": 1}, {"risk_score": 88}, {"score": 70},
                        {"diagnosis": "myocardial infarction"},
                        {"differential": ["MI", "PE"]},
                        {"recommendation": "resus bay"},
                        {"disposition": "admit"}, {"priority": "immediate"}):
            with self.assertRaises(ProviderUnavailable,
                                   msg=f"{verdict} was allowed through"):
                reject_clinical_verdicts(verdict)

    def test_a_verdict_nested_anywhere_is_rejected(self):
        with self.assertRaises(ProviderUnavailable):
            reject_clinical_verdicts(
                {"symptoms": [{"term": "chest pain", "band": "L4"}]})
        with self.assertRaises(ProviderUnavailable):
            reject_clinical_verdicts({"meta": {"inner": {"diagnosis": "x"}}})

    def test_ordinary_findings_pass(self):
        reject_clinical_verdicts({
            "symptoms": [{"term": "chest pain", "said": "my chest hurts",
                          "severity": "crushing", "confidence": 0.9}],
            "denials": [{"term": "breathlessness"}],
            "concerns": [{"concern": "heart attack"}],
            "chief_complaint": "chest pain",
        })

    @has_teeth
    def test_the_rejection_check_is_reachable(self):
        """
        A check that rejects everything would pass the tests above and break
        the product. The clean payload above must survive, and it must survive
        for the right reason: because nothing in it is a verdict.
        """
        clean = {"symptoms": [{"term": "headache", "confidence": 0.8}]}
        reject_clinical_verdicts(clean)
        clean["symptoms"][0]["level"] = 3
        with self.assertRaises(ProviderUnavailable):
            reject_clinical_verdicts(clean)


class TestTheExtractionSchemaCarriesNoVerdict(ClaimTest):
    claim = "The structures a provider returns have no field for a verdict."

    def test_no_forbidden_field_exists_on_the_dataclasses(self):
        forbidden = {"band", "risk", "risk_score", "acuity", "diagnosis",
                     "disposition", "priority", "triage_band"}
        for cls in (ExtractedSymptom, Extraction):
            fields = set(cls.__dataclass_fields__)
            overlap = fields & forbidden
            self.assertEqual(overlap, set(),
                             f"{cls.__name__} has clinical-verdict fields: {overlap}")

    def test_severity_is_the_patients_word_not_a_clinical_grade(self):
        """
        `severity` exists and is allowed. It is what the patient said about
        their own pain, which is an input. The docstring has to keep saying so,
        because the field name invites the other reading.
        """
        self.assertIn("PATIENT", ExtractedSymptom.__doc__ or "",
                      "the severity field's meaning is no longer documented")


class TestTheDeterministicProviderAlwaysAnswers(ClaimTest):
    claim = ("The console works with no key, no network and no account. The "
             "deterministic provider has no prerequisites.")

    def test_it_is_always_available(self):
        self.assertTrue(LocalProvider().available())

    def test_selection_never_raises_and_never_returns_none(self):
        for prefer in ("local", "claude", "", "nonsense"):
            provider = get_provider(prefer)
            self.assertIsInstance(provider, LanguageProvider)

    def test_it_understands_ordinary_speech(self):
        found = LocalProvider().extract("my chest is killing me and I feel sick")
        terms = found.terms()
        self.assertIn("chest pain", terms)
        self.assertIn("nausea", terms)

    def test_it_does_not_invent_a_confidence_it_cannot_compute(self):
        """
        A matcher has no way to judge how sure it is. It returns one honest
        constant rather than a number shaped like confidence, and the interface
        shows which provider produced each finding.
        """
        found = LocalProvider().extract("my chest is killing me")
        self.assertEqual({s.confidence for s in found.symptoms}, {0.6})


class TestTheModelProviderDegradesRatherThanFails(ClaimTest):
    claim = ("Every model failure falls back to the matcher and is marked "
             "degraded. It never stops the console and never silently changes "
             "what the system can see.")

    def test_no_key_degrades_and_still_returns_findings(self):
        provider = ClaudeProvider(api_key="")
        self.assertFalse(provider.available())
        found = provider.extract("my chest is killing me")
        self.assertTrue(found.degraded)
        self.assertIn("chest pain", found.terms())
        self.assertIn("fallback", found.provider.lower())

    def test_the_degraded_flag_reaches_the_interface(self):
        found = ClaudeProvider(api_key="").extract("my head is pounding")
        self.assertTrue(found.as_dict()["degraded"],
                        "a degraded extraction is indistinguishable from a "
                        "model one in the payload the console renders")
        self.assertTrue(found.note, "a degraded extraction gave no reason")

    def test_it_only_ever_maps_to_terms_the_engine_can_score(self):
        from core.risk_engine import WEIGHTS_FILE, _load
        scoreable = set(_load(WEIGHTS_FILE)["symptoms"].keys())
        self.assertEqual(set(ClaudeProvider(api_key="").vocabulary), scoreable)

    def test_a_term_outside_the_vocabulary_is_dropped_and_reported(self):
        """
        A finding the model saw that the engine has no weight for is a gap in
        the weights file. Dropping it silently would make the vocabulary look
        complete when it is not.
        """
        provider = ClaudeProvider(api_key="x")
        built = provider._build(
            {"symptoms": [{"term": "chest pain"}, {"term": "hemoptysis"}]}, {})
        self.assertEqual(built.terms(), ["chest pain"])
        self.assertIn("hemoptysis", built.note)
