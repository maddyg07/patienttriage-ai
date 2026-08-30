"""
core/capture_fusion.py
======================
Phase 17b. Turns raw sensor MEASUREMENTS into candidates, and makes the two
sensors answer to each other before either is allowed to suggest anything.

WHY THIS FILE EXISTS
--------------------
The first version of the intake console had each sensor decide alone. The
camera compared two halves of a rectangle and announced asymmetry; the
microphone counted pauses and announced breathlessness. Both were wrong within
minutes of real use, and they were wrong in the way single-sensor systems are
always wrong:

  * Side lighting makes a symmetric face asymmetric. So does turning your head,
    and so does sitting slightly off-centre. The camera cannot tell any of that
    from a droop, because a droop and a shadow are both "one side is darker".

  * Silence before someone starts talking is not breathlessness. Neither is a
    short answer, or a pause to think. The microphone cannot tell any of that
    from respiratory distress, because they are all "a gap in the audio".

Raising thresholds does not fix this. It trades one error for the other. What
fixes it is refusing to let a single channel speak on its own.

THE THREE THINGS THIS MODULE DOES
---------------------------------
1. QUALITY GATING. A measurement is discarded before it is interpreted if the
   conditions that would make it meaningless are present: an unstable reading
   across frames, a strong lighting gradient, a region with no facial structure
   in it, an utterance with almost no speech in it. An unreliable sensor
   reports UNRELIABLE, which is a different thing from reporting nothing found.

2. CROSS-MODAL CORROBORATION. In an acute neurological event the face and the
   voice tend to fail together: a droop severe enough for a camera to see is
   usually accompanied by dysarthria. A facial candidate with entirely fluent,
   well-sustained speech is therefore a WEAKER candidate than the same reading
   alongside slurred, effortful speech -- not because the finding is less real,
   but because the pattern is less coherent, and an incoherent pattern is
   exactly when a system should ask rather than assert.

   The same holds the other way. A breathlessness pattern in the audio with a
   visibly comfortable patient is weaker than one with visible distress.

3. HONEST DOWNGRADING. Corroboration can only weaken a CANDIDATE and change
   what the operator is asked. It cannot touch a score, a band or a confidence
   figure. Those come from confirmed flags, downstream, exactly as before.

WHY IT IS IN PYTHON AND NOT IN THE PAGE
---------------------------------------
Because it is reasoning, and reasoning in this project lives where it can be
tested. The browser measures -- luminance grids and amplitude envelopes, things
a browser is genuinely good at -- and posts numbers. Everything that decides
what those numbers MEAN happens here, under tests/test_fusion.py.

SAFETY NOTE: every threshold below is a SIMULATED DEMONSTRATION VALUE tuned so
a live demo behaves sensibly on ordinary laptop hardware. None of it is a
measurement standard.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
FUSION_CONFIG = REPO_ROOT / "data" / "fusion_config.json"


def _load(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return {k: v for k, v in json.load(fh).items() if not k.startswith("_")}


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------

NONE = "none"
POSSIBLE = "possible"
STRONG = "strong"
UNRELIABLE = "unreliable"

# What each candidate strength suggests to the operator. UNRELIABLE and NONE
# are deliberately different: one means the sensor could not see, the other
# means it saw and found nothing. Only the second is evidence.
SUGGESTION = {
    NONE: "no",
    POSSIBLE: None,        # no suggestion -- the operator decides unaided
    STRONG: "yes",
    UNRELIABLE: None,
}


@dataclass
class Candidate:
    """One sensor's proposal, with everything needed to argue with it."""

    name: str
    strength: str = UNRELIABLE
    suggestion: Optional[str] = None
    reasons: List[str] = field(default_factory=list)
    corroboration: str = ""
    measurements: Dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "strength": self.strength,
            "suggestion": self.suggestion,
            "reasons": list(self.reasons),
            "corroboration": self.corroboration,
            "measurements": dict(self.measurements),
        }


@dataclass
class FusionResult:
    facial: Candidate
    breathlessness: Candidate
    sentence: Candidate
    agreement: str = ""
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "facial": self.facial.as_dict(),
            "breathlessness": self.breathlessness.as_dict(),
            "sentence": self.sentence.as_dict(),
            "agreement": self.agreement,
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# The fuser
# ---------------------------------------------------------------------------

class CaptureFusion:
    def __init__(self, config: Optional[dict] = None):
        self.cfg = config or _load(FUSION_CONFIG)

    # -- camera ------------------------------------------------------------

    def _facial_candidate(self, cam: dict) -> Candidate:
        """
        Interpret the camera measurements, rejecting the conditions that make
        a symmetry reading meaningless before reading anything into it.
        """
        c = self.cfg["camera"]
        out = Candidate("facial_asymmetry", measurements=dict(cam))

        frames = int(cam.get("frames", 0) or 0)
        if frames < c["min_frames"]:
            out.strength = UNRELIABLE
            out.reasons.append(
                f"only {frames} usable frame(s); needs {c['min_frames']} to "
                f"tell a stable finding from a flicker")
            return out

        brightness = float(cam.get("brightness", 0) or 0)
        if brightness < c["min_brightness"] or brightness > c["max_brightness"]:
            out.strength = UNRELIABLE
            out.reasons.append(
                f"mean luminance {brightness:.0f} is outside the usable range "
                f"{c['min_brightness']}-{c['max_brightness']}")
            return out

        structure = float(cam.get("structure", 0) or 0)
        if structure < c["min_structure"]:
            out.strength = UNRELIABLE
            out.reasons.append(
                f"too little structure in the region (score {structure:.2f}); "
                f"the camera may be looking at a wall rather than a face")
            return out

        gradient = float(cam.get("gradient", 0) or 0)
        if gradient > c["max_gradient"]:
            out.strength = UNRELIABLE
            out.reasons.append(
                f"strong left-to-right lighting gradient ({gradient:.2f}); "
                f"a shadow and a droop look identical to this measure")
            return out

        spread = float(cam.get("spread", 1) or 0)
        if spread > c["max_spread"]:
            out.strength = UNRELIABLE
            out.reasons.append(
                f"reading moved by {spread:.3f} across frames; a real facial "
                f"difference does not flicker, so this is head movement or noise")
            return out

        index = float(cam.get("index", 0) or 0)
        if index >= c["strong"]:
            out.strength = STRONG
            out.reasons.append(
                f"symmetry index {index:.3f}, stable across {frames} frames, "
                f"above the strong threshold {c['strong']}")
        elif index >= c["possible"]:
            out.strength = POSSIBLE
            out.reasons.append(
                f"symmetry index {index:.3f}, between {c['possible']} and "
                f"{c['strong']}; too close to call from the image alone")
        else:
            out.strength = NONE
            out.reasons.append(
                f"symmetry index {index:.3f}, below the candidate threshold "
                f"{c['possible']}")
        return out

    # -- audio -------------------------------------------------------------

    def _audio_candidates(self, aud: dict) -> tuple:
        """
        Interpret the speech envelope.

        Everything here is computed on TRIMMED speech: the leading silence
        before somebody starts talking and the trailing silence after they stop
        are removed by the browser before these numbers are produced. A late
        start is not a respiratory sign, and the first version of this console
        scored it as one.
        """
        a = self.cfg["audio"]
        breath = Candidate("breathlessness", measurements=dict(aud))
        sentence = Candidate("cannot_finish_sentence", measurements=dict(aud))

        speech_s = float(aud.get("speech_seconds", 0) or 0)
        if speech_s < a["min_speech_seconds"]:
            for cand in (breath, sentence):
                cand.strength = UNRELIABLE
                cand.reasons.append(
                    f"only {speech_s:.1f}s of speech; needs "
                    f"{a['min_speech_seconds']}s before a breathing pattern "
                    f"means anything")
            return breath, sentence

        snr = float(aud.get("snr", 0) or 0)
        if snr < a["min_snr"]:
            for cand in (breath, sentence):
                cand.strength = UNRELIABLE
                cand.reasons.append(
                    f"signal-to-noise {snr:.1f} is too low; room noise is "
                    f"being counted as speech")
            return breath, sentence

        breaks = float(aud.get("breaks_per_10s", 0) or 0)
        if breaks >= a["breaks_strong"]:
            breath.strength = STRONG
            breath.reasons.append(
                f"{breaks:.1f} mid-speech breaks per 10s of speech, above "
                f"{a['breaks_strong']}")
        elif breaks >= a["breaks_possible"]:
            breath.strength = POSSIBLE
            breath.reasons.append(
                f"{breaks:.1f} breaks per 10s; within the range ordinary "
                f"conversational pauses also produce")
        else:
            breath.strength = NONE
            breath.reasons.append(
                f"{breaks:.1f} breaks per 10s, below {a['breaks_possible']}")

        median_ms = float(aud.get("median_phrase_ms", 0) or 0)
        longest_ms = float(aud.get("longest_phrase_ms", 0) or 0)
        if longest_ms >= a["sustained_phrase_ms"]:
            sentence.strength = NONE
            sentence.reasons.append(
                f"sustained a {longest_ms:.0f}ms phrase; somebody who cannot "
                f"finish a sentence does not manage that once")
        elif median_ms < a["short_phrase_ms"]:
            sentence.strength = POSSIBLE
            sentence.reasons.append(
                f"median phrase {median_ms:.0f}ms, under {a['short_phrase_ms']}ms, "
                f"and nothing longer than {longest_ms:.0f}ms")
        else:
            sentence.strength = NONE
            sentence.reasons.append(
                f"median phrase {median_ms:.0f}ms is within ordinary range")
        return breath, sentence

    # -- fusion ------------------------------------------------------------

    def fuse(self, camera: Optional[dict], audio: Optional[dict]) -> FusionResult:
        cam_data = camera or {}
        aud_data = audio or {}

        facial = self._facial_candidate(cam_data) if camera else \
            Candidate("facial_asymmetry", UNRELIABLE,
                      reasons=["camera channel not used"])
        breath, sentence = self._audio_candidates(aud_data) if audio else (
            Candidate("breathlessness", UNRELIABLE,
                      reasons=["audio channel not used"]),
            Candidate("cannot_finish_sentence", UNRELIABLE,
                      reasons=["audio channel not used"]),
        )

        notes: List[str] = []
        agreement = "not assessed"

        speech_usable = sentence.strength != UNRELIABLE
        face_usable = facial.strength not in (UNRELIABLE,)

        # ---- the cross-modal step ----------------------------------------
        if face_usable and speech_usable:
            speech_fluent = (sentence.strength == NONE
                             and breath.strength == NONE)

            if facial.strength in (STRONG, POSSIBLE) and speech_fluent:
                # Coherence check. A droop the camera can see usually travels
                # with dysarthria. Fluent speech does not disprove the finding,
                # but it makes the picture incoherent, and an incoherent
                # picture is when to ask rather than assert.
                agreement = "channels disagree"
                if facial.strength == STRONG:
                    facial.strength = POSSIBLE
                    facial.reasons.append(
                        "downgraded from strong: speech is fluent and well "
                        "sustained, which is unusual alongside a droop this "
                        "visible")
                facial.corroboration = (
                    "Not corroborated by the voice channel. In an acute "
                    "neurological event the face and the voice usually fail "
                    "together. Either this is not acute, or it is early -- "
                    "and the camera cannot tell which. Ask.")
                notes.append(
                    "facial candidate stands alone; no suggestion offered")

            elif facial.strength in (STRONG, POSSIBLE) and not speech_fluent:
                agreement = "channels agree"
                facial.corroboration = (
                    "Corroborated by the voice channel: speech is also "
                    "abnormal. Two independent modalities pointing the same "
                    "way is the pattern that matters here.")
                if facial.strength == POSSIBLE and \
                        sentence.strength in (POSSIBLE, STRONG):
                    facial.strength = STRONG
                    facial.reasons.append(
                        "raised from possible: the voice channel independently "
                        "flags abnormal speech")
                notes.append("facial and voice channels agree")

            elif facial.strength == NONE and not speech_fluent:
                agreement = "voice only"
                notes.append(
                    "speech is abnormal with a symmetric face; that points "
                    "away from a facial cause and towards breathing, and the "
                    "engine will see it as a respiratory signal, not a "
                    "neurological one")
            else:
                agreement = "both channels quiet"

        elif face_usable and not speech_usable:
            agreement = "camera only"
            facial.corroboration = (
                "The voice channel produced nothing usable, so this reading "
                "has no second opinion behind it. Treat it as one input.")
            if facial.strength == STRONG:
                facial.strength = POSSIBLE
                facial.reasons.append(
                    "downgraded from strong: no corroborating channel")
            notes.append("only one channel usable; no suggestion offered")

        elif speech_usable and not face_usable:
            agreement = "voice only"
            notes.append("camera unusable; the voice channel stands alone")

        else:
            agreement = "no usable channel"
            notes.append(
                "neither sensor produced a usable reading. Nothing is "
                "suggested. Answer from what you can see and hear, and the "
                "uncertainty engine will record that a modality was missing.")

        # ---- breathlessness corroboration --------------------------------
        if breath.strength in (POSSIBLE, STRONG):
            distress = cam_data.get("visible_distress")
            if distress is True:
                breath.corroboration = (
                    "Corroborated: the operator has marked visible distress.")
            elif distress is False:
                if breath.strength == STRONG:
                    breath.strength = POSSIBLE
                    breath.reasons.append(
                        "downgraded from strong: no visible distress reported")
                breath.corroboration = (
                    "Not corroborated. A breathing pattern in the audio with a "
                    "comfortable-looking patient is more often a recording "
                    "artefact than respiratory distress.")
            else:
                breath.corroboration = (
                    "No observation of distress recorded either way, so this "
                    "pattern stands on its own.")

        for cand in (facial, breath, sentence):
            cand.suggestion = SUGGESTION.get(cand.strength)
            # A candidate the fuser has flagged as uncorroborated never carries
            # a suggestion, whatever its strength.
            if cand.corroboration.startswith("Not corroborated"):
                cand.suggestion = None

        return FusionResult(facial, breath, sentence, agreement, notes)


def fuse(camera: Optional[dict], audio: Optional[dict]) -> dict:
    return CaptureFusion().fuse(camera, audio).as_dict()


__all__ = ["CaptureFusion", "Candidate", "FusionResult", "fuse",
           "NONE", "POSSIBLE", "STRONG", "UNRELIABLE"]
