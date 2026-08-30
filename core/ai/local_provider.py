"""
core/ai/local_provider.py
=========================
The deterministic provider. No network, no key, no account, same answer every
time.

WHAT IT IS FOR
--------------
Three jobs, and it is honest about all of them.

1. THE FLOOR. When the model provider has no key, no network or a bad night,
   this answers. A triage console that stops working because an API is down is
   not a triage console.

2. THE TEST SUBSTRATE. Every test in this repository runs against this
   provider, because a suite whose results depend on what a model felt like
   saying today is not a suite. The model path has its own boundary tests.

3. THE HONEST BASELINE. It is the site-plus-sensation matcher from Phase 17c,
   which is genuinely useful and genuinely limited. It understands "my heart is
   paining a lot" and "my chest is killing me". It does not understand "there's
   this weird pressure, like something's sitting on me", and no amount of
   further phrase-writing will get it there.

WHAT IT IS NOT
--------------
It is not the answer to the natural-language requirement, and the console says
so on screen when it is the one serving. Pretending a matcher is a model is the
exact failure this project has avoided everywhere else, and it is not worth
starting here.
"""

from __future__ import annotations

from typing import Optional

from core.ai.provider import Extraction, ExtractedSymptom, LanguageProvider
from core.narrative import NarrativeReader


class LocalProvider(LanguageProvider):
    name = "deterministic matcher"
    kind = "deterministic"
    needs_network = False

    def __init__(self, reader: Optional[NarrativeReader] = None):
        self.reader = reader or NarrativeReader()

    def available(self) -> bool:
        return True

    def extract(self, text: str, context: Optional[dict] = None) -> Extraction:
        at_second = float((context or {}).get("at_second", 0.0))
        source = (context or {}).get("source", "speech")
        found = self.reader.read_full(text or "")

        symptoms = [
            ExtractedSymptom(
                term=term,
                normalised=term,
                said=found.evidence.get(term, ""),
                severity_score=found.pain_score,
                duration_hours=found.duration_hours,
                # Deliberately mid-range. This matcher has no way to judge how
                # sure it is, and a number invented to look like confidence is
                # worse than an honest constant. The model provider returns a
                # real one; the UI shows which provider produced each finding.
                confidence=0.6,
                source=source,
                at_second=at_second,
            )
            for term in found.reported
        ]
        denials = [
            ExtractedSymptom(term=term, normalised=term, negated=True,
                             said=found.evidence.get(term, ""),
                             confidence=0.6, source=source, at_second=at_second)
            for term in found.denied
        ]

        return Extraction(
            symptoms=symptoms,
            denials=denials,
            concerns=list(found.concerns),
            baseline_hints=list(found.baseline_hints),
            chief_complaint=(text or "").strip()[:160],
            pain_score=found.pain_score,
            provider=self.name,
            note=("Phrase and site matching. Understands common phrasings; "
                  "will miss unusual ones. Not a language model."),
        )
