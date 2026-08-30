"""
core/ai/model_provider.py
=========================
The model-backed language layer. One base class, three vendor adapters.

WHY THREE VENDORS
-----------------
Because whichever key you have should be the one that works. The differences
between OpenAI, Gemini and Anthropic here are a URL, an auth header and where
the text sits in the response envelope. Everything that matters -- the prompt,
the boundary check, the vocabulary mapping, the fallback -- is shared, so the
three cannot drift apart and a bug fixed once is fixed everywhere.

WHAT THE MODEL IS ASKED TO DO, AND WHY IT IS MORE THAN EXTRACTION NOW
---------------------------------------------------------------------
The first version asked only for symptom extraction and left danger detection
to a phrase list. A patient said "I have been in a fatal car accident, my
friend is dead and my leg is amputated" and the system called it NORMAL,
because no phrase list contains every way a human being can be in mortal
danger and one built from medical vocabulary does not contain trauma at all.

So the model now answers three questions in one call:

  1. WHAT SYMPTOMS did the patient describe, mapped to terms the engine scores.
  2. IS ANY OF THIS LIFE-THREATENING, in its own judgement, with the words that
     made it think so. No list. This is the layer that catches the sentence
     nobody wrote down.
  3. WHAT SHOULD BE ASKED NEXT, given everything so far.

WHAT IT STILL MAY NOT RETURN
----------------------------
A band, a score, an acuity, a diagnosis, a differential, a disposition. Those
come from data/risk_weights.json and core/safety_rules.py, where every number
is inspectable and arguable. `reject_clinical_verdicts` discards any response
containing one, and the request falls back.

A `danger` verdict is not an exception to that. The model says "this may be
life-threatening and here is the sentence"; the gate decides what happens, the
nurse is notified, and the engine scores independently. The model never sets a
band.

FAILURE
-------
Every failure -- no key, no network, a timeout, malformed JSON, a rejected
response -- falls back to the deterministic matcher AND to the rule-based gate,
marks the extraction degraded, and says so on both dashboards.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

from core.ai.provider import (
    Extraction,
    ExtractedSymptom,
    LanguageProvider,
    ProviderUnavailable,
    reject_clinical_verdicts,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WEIGHTS_FILE = REPO_ROOT / "data" / "risk_weights.json"
TIMEOUT_SECONDS = 14

SYSTEM_PROMPT = """\
You are the language layer of an emergency department triage system. You read \
what a patient said and return structured findings. Something else decides \
what happens to the patient.

Return ONLY a JSON object. No prose, no markdown fences.

{
  "symptoms": [
    {
      "term": "<one of the ALLOWED TERMS, exactly>",
      "normalised": "<short clinical phrasing>",
      "said": "<the patient's own words that led to this, verbatim>",
      "severity": "<the patient's own intensity words, or empty>",
      "severity_score": <0-10 if the patient gave or implied one, else null>,
      "onset": "<when it started, in their terms, or empty>",
      "duration_hours": <number or null>,
      "location": "", "laterality": "", "progression": "", "frequency": "",
      "triggers": "", "associated": ["<other allowed terms>"],
      "uncertain": <true if they hedged>,
      "confidence": <0.0-1.0>
    }
  ],
  "denials": [ { "term": "...", "said": "..." } ],
  "concerns": [ { "concern": "...", "label": "believes they are ...",
                  "evidence": "<their words>" } ],
  "baseline_hints": [ { "hint": "...", "evidence": "<their words>" } ],
  "danger": {
    "life_threatening": <true|false>,
    "signals": [
      { "phrase": "<their exact words>",
        "why": "<why this may be immediately life-threatening>",
        "category": "<airway|breathing|circulation|neurological|trauma|\
haemorrhage|burns|poisoning|obstetric|psychiatric|sepsis|other>" }
    ]
  },
  "next_question": "<the single most useful thing to ask next, or empty>",
  "chief_complaint": "<one short line in the patient's framing>"
}

RULES

1. `term` MUST be one of the ALLOWED TERMS, spelled exactly. If something has \
no allowed term, leave it out of `symptoms` and mention it in \
`chief_complaint`. Never invent a term.

2. Interpret MEANING, not keywords. "something sitting on my chest", "a weird \
pressure in there" are chest pain. "I can't get enough air" is breathlessness. \
"every time I stand the room goes" is dizziness. Handle colloquial speech, \
Indian English, incomplete sentences and speech-to-text errors.

3. `danger` is the most important field and it has NO list behind it. Judge \
for yourself whether what this person described may kill them soon. This \
includes things that are not symptoms at all: a car accident, an amputation, a \
stabbing, a fall from height, a burn, a drowning, a death at the scene, a \
patient who cannot be woken, a woman whose baby has stopped moving, someone \
who has taken an overdose or intends to harm themselves. A patient describing \
what HAPPENED to them has told you as much as one describing what they FEEL. \
Set `life_threatening` true and quote the words that decided it.

4. Be over-sensitive on `danger` and precise on `symptoms`. A false alarm \
costs a nurse thirty seconds. A miss is the failure this system exists to \
reduce. When unsure, flag it.

5. A denial is information. "no chest pain" belongs in `denials`, never \
dropped and never in `symptoms`.

6. `concerns` are what the patient BELIEVES is happening ("I'm having a heart \
attack"). Record the belief; do NOT add a symptom they did not describe.

7. `baseline_hints` are phrases suggesting a facial or physical difference is \
pre-existing: an old accident, a burn, surgery, since birth, a previous stroke.

8. `next_question` is the one thing most worth knowing next, phrased for a \
frightened person. Leave it empty if `danger.life_threatening` is true -- when \
somebody may be dying you do not ask them about onset.

9. NEVER return a triage band, acuity, risk score, priority, diagnosis, \
differential or disposition. A response containing any of those is discarded \
and the system falls back. You describe; you do not decide.
"""


def _load_vocabulary() -> List[str]:
    with open(WEIGHTS_FILE, "r", encoding="utf-8") as fh:
        return sorted(json.load(fh)["symptoms"].keys())


class HTTPModelProvider(LanguageProvider):
    """
    Shared behaviour. Subclasses supply the three things that differ.

    Standard library only: the whole call is one `urllib.request` POST with a
    JSON body. This repository has installed nothing since Phase 1 and a
    vendor SDK per provider would end that for a saving of about ten lines.
    """

    kind = "model"
    needs_network = True
    env_key = ""
    default_model = ""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None,
                 fallback: Optional[LanguageProvider] = None):
        self.api_key = api_key if api_key is not None else os.environ.get(self.env_key, "")
        self.model = model or os.environ.get("PATIENTTRIAGE_MODEL") or self.default_model
        self._fallback = fallback
        self._vocabulary: Optional[List[str]] = None

    @property
    def vocabulary(self) -> List[str]:
        if self._vocabulary is None:
            self._vocabulary = _load_vocabulary()
        return self._vocabulary

    def available(self) -> bool:
        return bool(self.api_key)

    def describe(self) -> str:
        return f"{self.name} · {self.model}"

    # -- vendor hooks ------------------------------------------------------

    def _endpoint(self) -> str:
        raise NotImplementedError

    def _headers(self) -> Dict[str, str]:
        raise NotImplementedError

    def _request_body(self, prompt: str) -> dict:
        raise NotImplementedError

    def _text_from(self, payload: dict) -> str:
        raise NotImplementedError

    # -- the call ----------------------------------------------------------

    def _prompt(self, text: str, context: Optional[dict]) -> str:
        history = (context or {}).get("said_so_far", "")
        known = (context or {}).get("known_symptoms", [])
        parts = [f"ALLOWED TERMS (use exactly, or omit):\n{', '.join(self.vocabulary)}"]
        if known:
            parts.append(f"ALREADY RECORDED: {', '.join(known)}")
        if history:
            parts.append(f"EARLIER IN THIS CONVERSATION (context only, do not "
                         f"re-extract):\n{history[-1500:]}")
        parts.append(f"THE PATIENT JUST SAID:\n{text}")
        return "\n\n".join(parts)

    def _post(self, prompt: str) -> dict:
        request = urllib.request.Request(
            self._endpoint(),
            data=json.dumps(self._request_body(prompt)).encode("utf-8"),
            method="POST", headers=self._headers())
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8")[:200]
            except Exception:                               # noqa: BLE001
                pass
            raise ProviderUnavailable(f"HTTP {exc.code} {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderUnavailable(f"network: {exc}") from exc

        raw = self._text_from(payload).strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end != -1:
            raw = raw[start:end + 1]
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise ProviderUnavailable(f"model did not return JSON: {exc}") from exc

    # -- public ------------------------------------------------------------

    def extract(self, text: str, context: Optional[dict] = None) -> Extraction:
        if not (text or "").strip():
            return Extraction(provider=self.describe())
        if not self.available():
            return self._degrade(text, context, f"no {self.env_key} set")
        try:
            raw = self._post(self._prompt(text, context))
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
        dropped: List[str] = []

        def make(entry: dict, negated: bool = False) -> Optional[ExtractedSymptom]:
            """
            Build a symptom for ANY term the model recognised, scoreable or
            not.

            Phase 21: this used to `return None` for a term outside
            `allowed`, which is a silent-discard bug wearing a docstring --
            the model had genuinely recognised a clinical concept
            ("hemoptysis", "syncope", a phrasing nobody wrote a weight for
            yet) and the system threw it away rather than showing it to a
            nurse. A recognised-but-unscored concept is a gap in
            data/risk_weights.json, and the correct response to a gap is to
            surface it, not to pretend it was never seen. `scoreable=False`
            is that surfacing: the risk engine only ever scores against its
            own weight keys, so an unscored term contributes zero to the
            number exactly as before -- nothing about the score changes,
            only what the nurse gets to see.
            """
            if not isinstance(entry, dict):
                return None
            term = str(entry.get("term", "")).strip().lower()
            if not term:
                return None
            scoreable = term in allowed
            if not scoreable:
                dropped.append(term)
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
                confidence=_clamp(entry.get("confidence", 0.75)),
                source=source, at_second=at_second,
                scoreable=scoreable)

        symptoms = [s for s in (make(e) for e in raw.get("symptoms") or []) if s]
        denials = [s for s in (make(e, True) for e in raw.get("denials") or []) if s]

        danger = raw.get("danger") or {}
        signals = []
        for item in danger.get("signals") or []:
            if not isinstance(item, dict):
                continue
            phrase = str(item.get("phrase", "")).strip()
            if phrase:
                signals.append({"phrase": phrase,
                                "why": str(item.get("why", "flagged by the model")),
                                "category": str(item.get("category", "other"))})
        # A model that says life_threatening with no quotable words is still
        # saying something. Kept, with the flag as its own evidence, because
        # dropping it would silently discard the layer that exists to catch
        # what no list contains.
        if danger.get("life_threatening") and not signals:
            signals.append({"phrase": str(raw.get("chief_complaint", ""))[:120],
                            "why": "the model judged this may be life-threatening",
                            "category": "other"})

        note = ""
        if dropped:
            note = ("recognised but outside the scoreable vocabulary (shown "
                    "to the nurse, not counted in the score): "
                    + ", ".join(sorted(set(dropped))))

        return Extraction(
            symptoms=symptoms, denials=denials,
            concerns=[c for c in (raw.get("concerns") or []) if isinstance(c, dict)],
            baseline_hints=[h for h in (raw.get("baseline_hints") or [])
                            if isinstance(h, dict)],
            emergency_phrases=signals,
            chief_complaint=str(raw.get("chief_complaint") or ""),
            next_question=str(raw.get("next_question") or ""),
            pain_score=next((s.severity_score for s in symptoms
                             if s.severity_score is not None), None),
            provider=self.describe(), note=note)

    def _degrade(self, text: str, context: Optional[dict], why: str) -> Extraction:
        fallback = self._fallback
        if fallback is None:
            from core.ai.local_provider import LocalProvider
            fallback = LocalProvider()
        result = fallback.extract(text, context)
        result.degraded = True
        result.provider = f"{fallback.name} (fallback)"
        result.note = (f"{self.name} unavailable ({why}); phrase matching served "
                       f"this. Unusual phrasings may be missed. The rule-based "
                       f"emergency gate is unaffected and still running.")
        return result


# ---------------------------------------------------------------------------
# Vendors
# ---------------------------------------------------------------------------

class OpenAIProvider(HTTPModelProvider):
    name = "OpenAI"
    env_key = "OPENAI_API_KEY"
    default_model = "gpt-4o-mini"

    def _endpoint(self) -> str:
        return os.environ.get("OPENAI_BASE_URL",
                              "https://api.openai.com/v1") + "/chat/completions"

    def _headers(self) -> Dict[str, str]:
        return {"content-type": "application/json",
                "authorization": f"Bearer {self.api_key}"}

    def _request_body(self, prompt: str) -> dict:
        return {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                         {"role": "user", "content": prompt}],
        }

    def _text_from(self, payload: dict) -> str:
        return payload["choices"][0]["message"]["content"]


class GeminiProvider(HTTPModelProvider):
    name = "Google Gemini"
    env_key = "GOOGLE_API_KEY"
    # Flash and Flash-Lite are the models on Google's free tier. Free-tier
    # model availability moves; if this 404s, PATIENTTRIAGE_MODEL overrides it
    # and ai.google.dev/gemini-api/docs/models has the current list.
    default_model = "gemini-2.5-flash"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.api_key:
            self.api_key = os.environ.get("GEMINI_API_KEY", "")

    def _endpoint(self) -> str:
        return (f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{self.model}:generateContent")

    def _headers(self) -> Dict[str, str]:
        return {"content-type": "application/json",
                "x-goog-api-key": self.api_key}

    def _request_body(self, prompt: str) -> dict:
        return {
            "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0,
                                 "responseMimeType": "application/json"},
        }

    def _text_from(self, payload: dict) -> str:
        return payload["candidates"][0]["content"]["parts"][0]["text"]


class GroqProvider(OpenAIProvider):
    """
    Groq. OpenAI-compatible wire format, so it inherits everything.

    Here because it has a permanent free tier with no credit card and no
    credit balance to run out of -- which is exactly the failure that sent
    this project looking for alternatives. It serves open-weight models rather
    than frontier ones, which for extracting symptoms from a sentence is a
    trade worth making when the alternative is a phrase matcher.
    """

    name = "Groq"
    env_key = "GROQ_API_KEY"
    default_model = "llama-3.3-70b-versatile"

    def _endpoint(self) -> str:
        return os.environ.get("GROQ_BASE_URL",
                              "https://api.groq.com/openai/v1") + "/chat/completions"


class AnthropicProvider(HTTPModelProvider):
    name = "Anthropic Claude"
    env_key = "ANTHROPIC_API_KEY"
    default_model = "claude-sonnet-4-6"

    def _endpoint(self) -> str:
        return "https://api.anthropic.com/v1/messages"

    def _headers(self) -> Dict[str, str]:
        return {"content-type": "application/json", "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01"}

    def _request_body(self, prompt: str) -> dict:
        return {"model": self.model, "max_tokens": 1600, "temperature": 0,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": prompt}]}

    def _text_from(self, payload: dict) -> str:
        return "".join(b.get("text", "") for b in payload.get("content", [])
                       if b.get("type") == "text")


VENDORS = {
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
    "google": GeminiProvider,
    "groq": GroqProvider,
    "anthropic": AnthropicProvider,
    "claude": AnthropicProvider,
}

# Tried in this order when no vendor is named. The two free tiers come first,
# deliberately: a key that works costs nothing to try and an OpenAI account
# with no credits fails in exactly the same way as no key at all, except that
# it looks configured.
SELECTION_ORDER = ("gemini", "groq", "openai", "anthropic")


def _clamp(value, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return 0.6


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
