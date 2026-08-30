"""
core/ai
=======
The language layer, kept behind an interface.

THE LINE THIS PACKAGE DRAWS
---------------------------
A model reads LANGUAGE. The deterministic engine does the SCORING.

A patient saying "my chest feels like something is sitting on it" is a language
problem, and a language model is the right tool: no phrase list ever covers the
hundreds of ways people describe the same sensation, and Phase 17 proved that
by failing on "my heart is paining a lot".

Deciding that chest pain is worth eighteen points in the circulatory domain is
not a language problem. It is a clinical weighting that has to be inspectable,
identical every time it runs, and arguable line by line in front of a judge or
a clinician. A model that produces a band cannot offer any of that.

So a provider in this package may return findings. It may never return a score,
a band, an acuity, a confidence in the engine's sense, or a diagnosis, and
tests/test_ai_boundary.py fails the build if one does.
"""

from core.ai.model_provider import (
    AnthropicProvider,
    GeminiProvider,
    GroqProvider,
    HTTPModelProvider,
    OpenAIProvider,
    VENDORS,
)
from core.ai.provider import (
    Extraction,
    ExtractedSymptom,
    LanguageProvider,
    ProviderUnavailable,
    describe_providers,
    get_provider,
)

__all__ = [
    "AnthropicProvider", "Extraction", "ExtractedSymptom", "GeminiProvider", "GroqProvider",
    "HTTPModelProvider", "LanguageProvider", "OpenAIProvider",
    "ProviderUnavailable", "VENDORS", "describe_providers", "get_provider",
]
