"""
core/ai/claude_provider.py
==========================
The model provider. Reads what a patient said and returns structured findings.

WHY urllib AND NOT THE SDK
--------------------------
This repository has installed nothing since Phase 1 and that property has been
worth more than any single feature. The Messages API is one HTTPS POST with a
JSON body, and `urllib.request` in the standard library does that. No wheel to
build, no version to pin, nothing to go wrong on a laptop that has never seen
this project.

WHAT IT IS ASKED FOR
--------------------
Findings, in the vocabulary the engine can already score, with the patient's
own words attached to each one. Onset, duration, laterality, progression,
denials, stated concerns, and phrases that may explain a facial difference as
pre-existing.

WHAT IT IS FORBIDDEN
--------------------
Any clinical verdict. The prompt says so, and saying so is not enough: every
response passes through `reject_clinical_verdicts` before it is used, and a
response containing a band, a score, an acuity or a diagnosis is DISCARDED and
the request falls back. A prompt is a request; a boundary check is a control.

TERMS THE ENGINE CANNOT SCORE
-----------------------------
The model is given the scoreable vocabulary and asked to map to it. Anything
outside it is dropped at the boundary and recorded in `note`, so a finding
never silently exists in the interface without existing in the calculation.
This is a real limitation and the console shows it rather than hiding it.

FAILURE
-------
Every failure -- no key, no network, a timeout, malformed JSON, a rejected
response -- falls back to the deterministic provider and marks the extraction
`degraded`. The console shows a degraded badge. A model being unavailable
slows the system down; it never stops it and never silently changes what it
can see.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from core.ai.provider import (
    Extraction,
    ExtractedSymptom,
    LanguageProvider,
    ProviderUnavailable,
    reject_clinical_verdicts,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WEIGHTS_FILE = REPO_ROOT / "data" / "risk_weights.json"

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-4-6"
TIMEOUT_SECONDS = 12

SYSTEM_PROMPT = """\
You extract clinical findings from what a patient said during emergency \
department intake. You are a language layer inside a larger system.

Return ONLY a JSON object. No prose, no markdown fences.

{
  "symptoms": [
    {
      "term": "<one of the allowed terms, exactly>",
      "normalised": "<short clinical phrasing>",
      "said": "<the patient's own words that led to this, verbatim>",
      "severity": "<the patient's own intensity words, or empty>",
      "severity_score": <0-10 if the patient gave a number, else null>,
      "onset": "<when it started, in the patient's terms, or empty>",
      "duration_hours": <number or null>,
      "location": "<body location mentioned, or empty>",
      "laterality": "<left|right|bilateral|empty>",
      "progression": "<worsening|improving|stable|empty>",
      "frequency": "<constant|intermittent|empty>",
      "triggers": "<what brings it on, or empty>",
      "associated": ["<other allowed terms mentioned alongside>"],
      "uncertain": <true if the patient hedged: "I think", "maybe", "sort of">,
      "confidence": <0.0-1.0, how sure you are this is what they meant>
    }
  ],
  "denials": [ { "term": "...", "said": "..." } ],
  "concerns": [ { "concern": "...", "label": "believes they are ...",
                  "evidence": "<their words>" } ],
  "baseline_hints": [ { "hint": "...", "evidence": "<their words>" } ],
  "emergency_phrases": [ { "phrase": "<their exact words>",
                           "why": "<why this may be life-threatening>" } ],
  "chief_complaint": "<one short line in the patient's framing>"
}

RULES

1. `term` MUST be one of the allowed terms given below, spelled exactly. If \
what the patient described has no allowed term, leave it out of `symptoms` and \
say so in `chief_complaint`. Never invent a term.

2. Interpret meaning, not keywords. "something sitting on my chest", "a weird \
pressure in there", "my chest feels tight" are all chest pain. "I can't get \
enough air", "I keep having to catch my breath" are breathlessness. "every \
time I stand up the room goes" is dizziness. Handle colloquial speech, Indian \
English phrasing, incomplete sentences and speech-to-text errors.

3. A denial is information. "no chest pain" belongs in `denials`, never \
dropped and never in `symptoms`.

4. `concerns` are what the patient BELIEVES is happening: "I'm having a heart \
attack", "I think I'm dying". Record the belief. Do NOT add a symptom they did \
not describe because of it.

5. `baseline_hints` are phrases suggesting a facial or physical difference is \
pre-existing rather than new: an old accident, a burn, surgery, since birth, a \
previous stroke, "it's always looked like this".

6. `emergency_phrases` are the patient's own words that may signal an \
immediately life-threatening situation. Quote them exactly. Do not judge how \
urgent they are and do not assign a level.

7. NEVER return a triage band, an acuity, a risk score, a priority, a \
diagnosis, a differential or a recommendation. You extract what was said. \
Something else decides what it means. A response containing any of those is \
discarded.

8. `severity` and `severity_score` are the PATIENT'S description of intensity, \
not your clinical judgement.

9. If the text contains nothing clinical, return empty lists. Do not \
manufacture findings from small talk.
"""


class ClaudeProvider(LanguageProvider):
    name = "Claude (language extraction)"
    kind = "model"
    needs_network = True

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None,
                 fallback: Optional[LanguageProvider] = None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model or os.environ.get("PATIENTTRIAGE_MODEL", DEFAULT_MODEL)
        self._fallback = fallback
        self._vocabulary = None

    # -- vocabulary --------------------------------------------------------

    @property
    def vocabulary(self):
        """
        The terms the engine can score. Loaded from the same weights file the
        engine reads, so a term the model may return is always a term that
        reaches the calculation.
        """
        if self._vocabulary is None:
            with open(WEIGHTS_FILE, "r", encoding="utf-8") as fh:
                self._vocabulary = sorted(json.load(fh)["symptoms"].keys())
        return self._vocabulary

    def available(self) -> bool:
        return bool(self.api_key)

    # -- the call ----------------------------------------------------------

    def _post(self, text: str, context: Optional[dict]) -> dict:
        history = (context or {}).get("said_so_far", "")
        prompt = (
            f"ALLOWED TERMS (use these exactly, or omit the finding):\n"
            f"{', '.join(self.vocabulary)}\n\n"
        )
        if history:
            prompt += (f"EARLIER IN THIS CONVERSATION (context only, do not "
                       f"re-extract):\n{history[-1200:]}\n\n")
        prompt += f"THE PATIENT JUST SAID:\n{text}"

        body = json.dumps({
            "model": self.model,
            "max_tokens": 1400,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        }).encode("utf-8")

        request = urllib.request.Request(
            API_URL, data=body, method="POST",
            headers={
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": API_VERSION,
            })
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ProviderUnavailable(f"HTTP {exc.code} from the API") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderUnavailable(f"network: {exc}") from exc

        parts = [b.get("text", "") for b in payload.get("content", [])
                 if b.get("type") == "text"]
        raw = "".join(parts).strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise ProviderUnavailable(f"model did not return JSON: {exc}") from exc

    # -- public ------------------------------------------------------------

    def extract(self, text: str, context: Optional[dict] = None) -> Extraction:
        if not (text or "").strip():
            return Extraction(provider=self.name)
        if not self.available():
            return self._degrade(text, context, "no ANTHROPIC_API_KEY set")

        try:
            raw = self._post(text, context)
            # The boundary. A prompt asks; this enforces.
            reject_clinical_verdicts(raw)
        except ProviderUnavailable as exc:
            return self._degrade(text, context, str(exc))
        except Exception as exc:                            # noqa: BLE001
            return self._degrade(text, context, f"{type(exc).__name__}: {exc}")

        return self._build(raw, context)

    def _build(self, raw: dict, context: Optional[dict]) -> Extraction:
        at_second = float((context or {}).get("at_second", 0.0))
        source = (context or {}).get("source", "speech")
        allowed = set(self.vocabulary)
        dropped = []

        def make(entry: dict, negated: bool = False) -> Optional[ExtractedSymptom]:
            term = str(entry.get("term", "")).strip().lower()
            if term not in allowed:
                if term:
                    dropped.append(term)
                return None
            return ExtractedSymptom(
                term=term,
                normalised=str(entry.get("normalised") or term),
                said=str(entry.get("said") or ""),
                severity=str(entry.get("severity") or "") or None,
                severity_score=_int_or_none(entry.get("severity_score")),
                onset=str(entry.get("onset") or ""),
                duration_hours=_float_or_none(entry.get("duration_hours")),
                location=str(entry.get("location") or ""),
                laterality=str(entry.get("laterality") or ""),
                progression=str(entry.get("progression") or ""),
                frequency=str(entry.get("frequency") or ""),
                triggers=str(entry.get("triggers") or ""),
                associated=[a for a in (entry.get("associated") or [])
                            if a in allowed],
                negated=negated,
                uncertain=bool(entry.get("uncertain")),
                confidence=_clamp(entry.get("confidence", 0.7)),
                source=source,
                at_second=at_second,
            )

        symptoms = [s for s in (make(e) for e in raw.get("symptoms", []) or []) if s]
        denials = [s for s in (make(e, True) for e in raw.get("denials", []) or []) if s]

        note = ""
        if dropped:
            # Said out loud rather than swallowed. A finding the model saw that
            # the engine has no weight for is a gap in the weights file, and
            # hiding it would make the vocabulary look complete when it is not.
            note = ("outside the scoreable vocabulary and therefore not "
                    "counted: " + ", ".join(sorted(set(dropped))))

        return Extraction(
            symptoms=symptoms,
            denials=denials,
            concerns=[c for c in (raw.get("concerns") or []) if isinstance(c, dict)],
            baseline_hints=[h for h in (raw.get("baseline_hints") or [])
                            if isinstance(h, dict)],
            emergency_phrases=[p for p in (raw.get("emergency_phrases") or [])
                               if isinstance(p, dict)],
            chief_complaint=str(raw.get("chief_complaint") or ""),
            pain_score=next((s.severity_score for s in symptoms
                             if s.severity_score is not None), None),
            provider=self.name,
            note=note,
        )

    def _degrade(self, text: str, context: Optional[dict], why: str) -> Extraction:
        fallback = self._fallback
        if fallback is None:
            from core.ai.local_provider import LocalProvider
            fallback = LocalProvider()
        result = fallback.extract(text, context)
        result.degraded = True
        result.provider = f"{fallback.name} (fallback)"
        result.note = (f"the model provider was unavailable ({why}); phrase "
                       f"matching served this. Unusual phrasings will be missed.")
        return result


def _clamp(value, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return 0.5


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
