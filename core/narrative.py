"""
core/narrative.py
=================
Reads what a patient actually said into structured findings.

WHY THE FIRST VERSION FAILED
----------------------------
It matched fixed phrases against a synonym list. In the first live test a
patient said:

    "I am suffering from a severe heart attack, my heart is paining a lot,
     I have been in an accident and my jaw has been deformed."

and the console recognised NOTHING. Not the chest pain, not the concern, not
the accident. Every clinically relevant word in that sentence went past it.

The list was not too short. A list is always too short. "My heart is paining"
is not an unusual way to say chest pain, and neither are the forty other ways
people say it, and writing them all down is not a strategy. People do not
describe symptoms in clinical vocabulary, and a prototype that only understands
patients who already speak like a triage form has understood nothing.

WHAT REPLACED IT
----------------
Three passes, in order, each one auditable in a sitting.

1. SITE PLUS SENSATION. A body part near a word for hurting. "chest", "heart",
   "sternum" are sites for chest pain; "paining", "hurts", "tight", "crushing",
   "killing me" are sensations. Any site within a few words of any sensation,
   inside the same clause, produces the term. This is what catches "my heart is
   paining a lot" without anybody having written that phrase down.

2. DIRECT PHRASES. For findings that have no site -- breathlessness, slurred
   speech, confusion -- a phrase list is still the right tool, so one remains.

3. STATED CONCERNS. What the patient believes is happening to them. Kept
   strictly separate from findings, for a reason set out below.

CONCERNS ARE NOT FINDINGS AND ARE NOT DIAGNOSES
-----------------------------------------------
A patient saying "I am having a heart attack" is telling us something real, and
a triage system that ignores it is throwing away information a nurse would act
on. It is also not a diagnosis, and the moment a system starts treating
self-declared conditions as findings it can be talked into anything.

So a concern is recorded as a concern, carries its own modest weight, and adds
NO symptom the patient did not describe. Saying "heart attack" does not
manufacture a chest pain. In the sentence above the chest pain arrives through
pass 1, from "my heart is paining", which is the patient describing a symptom
rather than naming a disease.

BASELINE HINTS
--------------
The same sentence contains "I have been in an accident and my jaw has been
deformed" -- which is the patient answering the most important question in this
entire system, unprompted, while the operator clicked "new" on the baseline
question. The reader now surfaces phrases like that as a prompt on that
question. It does not answer it, and it never touches the score.

SAFETY NOTE: this is a prototype vocabulary. It will miss things, it has not
been validated against real patient speech, and every term it produces is shown
back to the operator for correction before anything is scored.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
NARRATIVE_CONFIG = REPO_ROOT / "data" / "narrative_config.json"
WEIGHTS_FILE = REPO_ROOT / "data" / "risk_weights.json"


def _load(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return {k: v for k, v in json.load(fh).items() if not k.startswith("_")}


@dataclass
class NarrativeFindings:
    reported: List[str] = field(default_factory=list)
    denied: List[str] = field(default_factory=list)
    concerns: List[Dict[str, str]] = field(default_factory=list)
    baseline_hints: List[Dict[str, str]] = field(default_factory=list)
    pain_score: Optional[int] = None
    duration_hours: Optional[float] = None
    evidence: Dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "reported": list(self.reported),
            "denied": list(self.denied),
            "concerns": list(self.concerns),
            "baseline_hints": list(self.baseline_hints),
            "pain_score": self.pain_score,
            "duration_hours": self.duration_hours,
            "evidence": dict(self.evidence),
        }


class NarrativeReader:
    """
    Free text in, structured findings out.

    The vocabulary of scoreable TERMS is the key set of the `symptoms` block in
    data/risk_weights.json. A term this reader can produce is therefore always
    a term the engine can score, and a test asserts it: a reader with a private
    word list silently collects findings that never reach the calculation.
    """

    def __init__(self, config: Optional[dict] = None, weights: Optional[dict] = None):
        self.cfg = config or _load(NARRATIVE_CONFIG)
        weights = weights or _load(WEIGHTS_FILE)
        self.vocabulary: List[str] = list(weights["symptoms"].keys())

        self.sites: Dict[str, List[str]] = self.cfg.get("sites", {})
        self.sensations: List[str] = self.cfg.get("sensations", [])
        self.window: int = int(self.cfg.get("site_sensation_window", 6))
        self.phrases: Dict[str, List[str]] = self.cfg.get("direct_phrases", {})
        self.concerns: Dict[str, dict] = self.cfg.get("stated_concerns", {})
        self.hints: Dict[str, List[str]] = self.cfg.get("baseline_hints", {})
        self.negations: List[str] = self.cfg.get("negation_markers", [])
        self.breaks: List[str] = self.cfg.get("clause_breaks", [])
        self.durations: Dict[str, float] = self.cfg.get("duration_patterns", {})

    # -- text handling -----------------------------------------------------

    @staticmethod
    def normalise(text: str) -> str:
        """
        Lowercase, strip apostrophes, keep clause punctuation, collapse space.

        Apostrophes go because speech recognition is inconsistent about them:
        "can't", "cant" and "can not" must all reach the same place.
        """
        t = text.lower().replace("'", "").replace("\u2019", "")
        t = re.sub(r"[^a-z0-9,;.\s/]", " ", t)
        return " " + re.sub(r"\s+", " ", t).strip() + " "

    def _clause_before(self, text: str, index: int) -> str:
        """
        The text between the nearest clause boundary and the match.

        Negation is scoped to the clause, not to a character window. "denies
        breathlessness, temperature three days" denies one thing and reports
        another; a fixed lookback denies both.
        """
        start = 0
        for marker in self.breaks:
            pos = text.rfind(marker, 0, index)
            if pos != -1:
                start = max(start, pos + len(marker))
        return text[start:index]

    @staticmethod
    def _snippet(text: str, lo: int, hi: int) -> str:
        """
        The patient's own words that produced a term, cut at word boundaries.

        Shown to the operator beside every recognised term. A wrong match
        should be obvious at a glance rather than mysterious, and a matcher
        this shallow will produce wrong matches.
        """
        lo = max(0, lo - 18)
        hi = min(len(text), hi)
        while lo > 0 and text[lo] != " ":
            lo -= 1
        while hi < len(text) and text[hi] != " ":
            hi += 1
        return text[lo:hi].strip()

    def _negated(self, text: str, index: int) -> bool:
        clause = self._clause_before(text, index)
        return any(marker in clause for marker in self.negations)

    # -- pass 1: site plus sensation --------------------------------------

    def _site_sensation(self, text: str) -> List[Tuple[str, bool, str]]:
        """
        A body part near a word for hurting, inside one clause.

        Returns (term, negated, evidence). The evidence string is what the
        operator sees: the actual words that produced the term, so a wrong
        match is obvious rather than mysterious.
        """
        out: List[Tuple[str, bool, str]] = []
        tokens = text.split()
        positions = {}
        cursor = 0
        for i, tok in enumerate(tokens):
            cursor = text.find(tok, cursor)
            positions[i] = cursor
            cursor += len(tok)

        sensation_at = [i for i, tok in enumerate(tokens)
                        if tok.strip(",;.") in self.sensations]
        # Multi-word sensations ("killing me", "on fire").
        for phrase in (s for s in self.sensations if " " in s):
            idx = text.find(" " + phrase + " ")
            while idx != -1:
                before = text[:idx].split()
                sensation_at.append(len(before))
                idx = text.find(" " + phrase + " ", idx + 1)

        for term, site_words in self.sites.items():
            if term not in self.vocabulary:
                continue
            hits = []
            for site in site_words:
                needle = " " + site + " "
                at = text.find(needle)
                while at != -1:
                    site_token = len(text[:at].split())
                    near = [i for i in sensation_at
                            if abs(i - site_token) <= self.window]
                    if near:
                        lo = min(positions.get(min(near + [site_token]), at), at)
                        hi = max(positions.get(max(near + [site_token]), at), at)
                        # Negation is judged at the SITE, which is where the
                        # patient names the thing they are denying. The index
                        # passed is the WORD, not the leading space: slicing at
                        # the space cuts a leading "no " in half and a denial
                        # at the very start of a sentence is missed.
                        hits.append((self._negated(text, at + 1),
                                     self._snippet(text, lo, hi + 24)))
                    at = text.find(needle, at + 1)
            if not hits:
                continue
            # Every mention of the site is examined, not just the first. "no
            # chest pain earlier, my chest is killing me now" mentions it
            # twice and only the second one matters: a symptom reported
            # anywhere outranks a denial elsewhere in the same account.
            affirmed = [h for h in hits if not h[0]]
            negated, snippet = (False, affirmed[0][1]) if affirmed else hits[0]
            out.append((term, negated, snippet))
        return out

    # -- pass 2: direct phrases -------------------------------------------

    def _direct(self, text: str) -> List[Tuple[str, bool, str]]:
        """
        Phrase matching, for findings that have no site to anchor to.

        Every vocabulary TERM is also matched literally, because a patient who
        says "headache" has said headache and should not need the word to
        appear in a synonym list to be understood. Dropping that in the rewrite
        broke exactly that case, which is why it is now first in the list
        rather than assumed.
        """
        out: List[Tuple[str, bool, str]] = []
        for term in self.vocabulary:
            phrases = [term] + list(self.phrases.get(term, []))
            for phrase in phrases:
                at = text.find(" " + phrase)
                if at == -1:
                    continue
                snippet = self._snippet(text, at, at + len(phrase) + 14)
                out.append((term, self._negated(text, at + 1), snippet))
                break
        return out

    # -- pass 3: stated concerns ------------------------------------------

    def _stated_concerns(self, text: str) -> List[Dict[str, str]]:
        """
        Whole-word matching, unlike the symptom passes.

        A symptom alias may match inside a word, because "cough" should catch
        "coughing". A concern must not: the alias "mi" matched "mild" and
        "migraine" and put a cardiac concern on the nurse's screen in two
        scenarios where nobody mentioned their heart. A false concern costs the
        same trust as a false alert.
        """
        found: List[Dict[str, str]] = []
        for key, spec in self.concerns.items():
            for alias in spec.get("aliases", []):
                # Word boundaries, not bare substrings. The normalised text
                # keeps clause punctuation, so "heart attack," must still
                # match while "migraine" must not match "mi".
                match = re.search(r"(?<![a-z])" + re.escape(alias) + r"(?![a-z])",
                                  text)
                if match is None:
                    continue
                at = match.start() - 1
                if self._negated(text, at + 1):
                    break
                found.append({
                    "concern": key,
                    "label": spec.get("label", key),
                    "evidence": self._snippet(text, at, at + len(alias) + 20),
                })
                break
        return found

    # -- baseline hints ----------------------------------------------------

    def _baseline_hints(self, text: str) -> List[Dict[str, str]]:
        found: List[Dict[str, str]] = []
        for key, phrases in self.hints.items():
            for phrase in phrases:
                at = text.find(" " + phrase)
                if at == -1:
                    continue
                found.append({
                    "hint": key,
                    "evidence": self._snippet(text, at, at + len(phrase) + 22),
                })
                break
        return found

    # -- numbers -----------------------------------------------------------

    def pain_score(self, text: str) -> Optional[int]:
        lowered = self.normalise(text)
        words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                 "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
        m = re.search(r"\b(\d{1,2})\s*(?:/|out of)\s*10\b", lowered)
        if m:
            v = int(m.group(1))
            return v if 0 <= v <= 10 else None
        m = re.search(r"\b(" + "|".join(words) + r")\s*(?:/|out of)\s*(?:10|ten)\b",
                      lowered)
        if m:
            return words[m.group(1)]
        return None

    def duration_hours(self, text: str) -> Optional[float]:
        lowered = self.normalise(text)
        words = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4,
                 "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
        pattern = (r"(\d+(?:\.\d+)?|" + "|".join(words) + r")\s+("
                   + "|".join(self.durations) + r")\b")
        m = re.search(pattern, lowered)
        if not m:
            return None
        raw, unit = m.group(1), m.group(2)
        amount = float(raw) if raw.replace(".", "").isdigit() else float(words[raw])
        return round(amount * self.durations[unit], 3)

    # -- public ------------------------------------------------------------

    def read_full(self, text: str) -> NarrativeFindings:
        if not text or not text.strip():
            return NarrativeFindings()
        norm = self.normalise(text)

        reported: List[str] = []
        denied: List[str] = []
        evidence: Dict[str, str] = {}

        for term, negated, snippet in self._site_sensation(norm) + self._direct(norm):
            bucket = denied if negated else reported
            if term not in bucket:
                bucket.append(term)
                evidence.setdefault(term, snippet)

        # A term reported anywhere wins over a denial elsewhere. The denial is
        # dropped from the denial list but the conflict has already been seen
        # by the operator, and the engine's own conflict detector works on the
        # confirmed flags rather than on this.
        denied = [d for d in denied if d not in reported]

        return NarrativeFindings(
            reported=reported,
            denied=denied,
            concerns=self._stated_concerns(norm),
            baseline_hints=self._baseline_hints(norm),
            pain_score=self.pain_score(text),
            duration_hours=self.duration_hours(text),
            evidence=evidence,
        )

    def read(self, text: str) -> Tuple[List[str], List[str]]:
        """Backwards-compatible pair, used everywhere that predates concerns."""
        f = self.read_full(text)
        return f.reported, f.denied


__all__ = ["NarrativeReader", "NarrativeFindings"]
