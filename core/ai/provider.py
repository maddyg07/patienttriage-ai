"""
core/ai/provider.py
===================
The interface every language provider implements, and the structured findings
they are allowed to return.

WHY AN INTERFACE RATHER THAN A DIRECT API CALL
----------------------------------------------
Three reasons, in order of how much they matter.

1. The demo must run with no key, no network and no account. A judge who
   clones the repository on conference wifi gets the deterministic provider and
   a working system, not a stack trace.

2. The scoring engine must not be able to tell which provider produced a
   finding. If it could, the two paths would drift and only one of them would
   be the tested one.

3. A clinical deployment would not use a general-purpose model for this. It
   would use something trained and validated for clinical language, and it
   would be procured, audited and re-validated on its own schedule. That is a
   swap of one class behind this interface, not a rewrite.

WHAT A PROVIDER MAY AND MAY NOT RETURN
--------------------------------------
May: symptoms, their normalised form, the patient's own words as evidence, an
onset, a duration, a location, a progression, a laterality, an intensity the
patient described, denials, stated concerns, and phrases that may explain a
facial difference as pre-existing.

May not: a risk score, a triage band, an acuity, a diagnosis, a disease name
presented as a conclusion, or a recommendation about what happens to the
patient next. Those come from core/risk_engine.py, core/safety_rules.py and
core/ratchet.py, from numbers in data/ that anybody can open and argue with.

`severity` below is the intensity the PATIENT described -- "excruciating",
"mild", "eight out of ten" -- normalised to a small scale. It is a report of
what was said, not a clinical judgement, and the engine treats it as one input
among many rather than as an answer.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional


class ProviderUnavailable(RuntimeError):
    """Raised when a provider cannot serve a request. Never fatal."""


# ---------------------------------------------------------------------------
# Structured findings
# ---------------------------------------------------------------------------

@dataclass
class ExtractedSymptom:
    """
    One symptom, as the patient described it.

    `term` is the engine-scoreable term. `normalised` is the clinical phrasing
    for display. `said` is the patient's own words, kept verbatim, because a
    nurse correcting a wrong extraction needs to see what produced it and
    because an assessment nobody can audit is not an assessment.

    `severity` and `severity_score` are the PATIENT'S own description of
    intensity -- "excruciating", "mild", "eight out of ten" -- and not a
    clinical grade. The field name invites the other reading, which is why it
    is written down here and asserted in tests/test_ai_boundary.py.
    """

    term: str
    normalised: str = ""
    said: str = ""
    severity: Optional[str] = None          # patient's own intensity
    severity_score: Optional[int] = None    # 0-10 where the patient gave one
    onset: str = ""
    duration_hours: Optional[float] = None
    location: str = ""
    laterality: str = ""
    progression: str = ""                   # worsening / stable / improving
    frequency: str = ""
    triggers: str = ""
    associated: List[str] = field(default_factory=list)
    negated: bool = False
    uncertain: bool = False                 # "I think", "maybe", "sort of"
    confidence: float = 0.5                 # extraction confidence, not clinical
    source: str = "speech"                  # speech | typed | nurse
    at_second: float = 0.0
    scoreable: bool = True                  # False: a recognised clinical
                                             # concept the model extracted
                                             # that data/risk_weights.json has
                                             # no weight for yet. Recognised,
                                             # not discarded -- see
                                             # core/ai/model_provider.py.

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Extraction:
    """Everything one provider found in one piece of text."""

    symptoms: List[ExtractedSymptom] = field(default_factory=list)
    denials: List[ExtractedSymptom] = field(default_factory=list)
    concerns: List[Dict[str, str]] = field(default_factory=list)
    baseline_hints: List[Dict[str, str]] = field(default_factory=list)
    emergency_phrases: List[Dict[str, str]] = field(default_factory=list)
    chief_complaint: str = ""
    next_question: str = ""
    pain_score: Optional[int] = None
    provider: str = ""
    degraded: bool = False                  # a fallback served this
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "symptoms": [s.as_dict() for s in self.symptoms],
            "denials": [s.as_dict() for s in self.denials],
            "concerns": list(self.concerns),
            "baseline_hints": list(self.baseline_hints),
            "emergency_phrases": list(self.emergency_phrases),
            "chief_complaint": self.chief_complaint,
            "next_question": self.next_question,
            "pain_score": self.pain_score,
            "provider": self.provider,
            "degraded": self.degraded,
            "note": self.note,
        }

    def terms(self) -> List[str]:
        """Every recognised term, scoreable or not. See `scoreable_terms`."""
        return [s.term for s in self.symptoms]

    def scoreable_terms(self) -> List[str]:
        """Only the terms data/risk_weights.json has a weight for."""
        return [s.term for s in self.symptoms if s.scoreable]

    def unscoreable_terms(self) -> List[str]:
        """
        Recognised clinical concepts with no scoring rule yet -- a gap in the
        weights file, not a gap in what the model saw. Phase 21: these used
        to be silently dropped at the provider boundary. They now reach the
        ledger and the nurse's screen, flagged for review, instead.
        """
        return [s.term for s in self.symptoms if not s.scoreable]


# Keys a provider must never return. Checked at the boundary on every call, so
# a prompt change or a model update cannot quietly move clinical authority out
# of the engine and into a model nobody can inspect.
FORBIDDEN_KEYS = (
    "band", "triage_band", "acuity", "risk", "risk_score", "score",
    "diagnosis", "diagnoses", "differential", "disposition",
    "recommendation", "priority", "esi", "level",
)


def reject_clinical_verdicts(raw: dict) -> None:
    """Raise if a provider tried to return a clinical decision."""
    def walk(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                if key.lower() in FORBIDDEN_KEYS:
                    raise ProviderUnavailable(
                        f"provider returned '{path}{key}': a language provider "
                        f"may return findings, never a clinical verdict. "
                        f"Scoring belongs to core/risk_engine.py.")
                walk(value, f"{path}{key}.")
        elif isinstance(node, list):
            for item in node:
                walk(item, path)
    walk(raw)


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------

class LanguageProvider(ABC):
    name = "abstract"
    kind = "abstract"          # "model" or "deterministic"
    needs_network = False

    @abstractmethod
    def available(self) -> bool:
        """Can this provider serve a request right now?"""

    @abstractmethod
    def extract(self, text: str, context: Optional[dict] = None) -> Extraction:
        """Free text in, structured findings out. Never a clinical verdict."""

    def describe(self) -> str:
        return f"{self.name} ({self.kind})"


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def get_provider(prefer: Optional[str] = None) -> LanguageProvider:
    """
    Return the best available provider, degrading rather than failing.

    Order: an explicitly named vendor, then whichever vendor has a key
    configured, then the deterministic matcher. The matcher has no
    prerequisites and always answers, so this cannot raise and the console
    cannot fail to start.

    Whichever key you have is the one that works. Set
    PATIENTTRIAGE_PROVIDER=local to force the offline path even with a key
    present, which is what the test suite does so the same input gives the
    same findings on every machine.
    """
    from core.ai.local_provider import LocalProvider
    from core.ai.model_provider import VENDORS

    requested = (prefer or os.environ.get("PATIENTTRIAGE_PROVIDER") or "").lower()
    local = LocalProvider()

    if requested in ("local", "deterministic", "offline", "none"):
        return local

    if requested in VENDORS:
        return VENDORS[requested](fallback=local)

    from core.ai.model_provider import SELECTION_ORDER
    for vendor in SELECTION_ORDER:
        provider = VENDORS[vendor](fallback=local)
        if provider.available():
            return provider
    return local


def describe_providers() -> List[dict]:
    """What is configured on this machine, for the banner and both dashboards."""
    from core.ai.local_provider import LocalProvider
    from core.ai.model_provider import (
        AnthropicProvider, GeminiProvider, GroqProvider, OpenAIProvider)

    out = []
    for provider in (GeminiProvider(), GroqProvider(), OpenAIProvider(),
                     AnthropicProvider(), LocalProvider()):
        out.append({
            "name": provider.name,
            "kind": provider.kind,
            "available": provider.available(),
            "needs_network": provider.needs_network,
            "env_key": getattr(provider, "env_key", ""),
            "model": getattr(provider, "model", ""),
        })
    return out
