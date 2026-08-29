"""
core/enums.py
=============
The vocabulary of PatientTriage.ai.

Everything in this file is a fixed set of allowed values. Putting them here
(instead of using loose strings like "yes" / "high" scattered across the code)
means a typo becomes a crash instead of a silent clinical bug.

SAFETY NOTE: Every threshold or band boundary referenced from this file is a
SIMULATED DEMONSTRATION VALUE. None of it is a clinical guideline.
"""

from enum import Enum, IntEnum


# ---------------------------------------------------------------------------
# 1. THE TRI-STATE  --  the single most important type in this project
# ---------------------------------------------------------------------------

class Tri(Enum):
    """
    A three-valued answer: YES / NO / UNKNOWN.

    Ordinary booleans cannot express the difference between:
        "the patient does NOT have facial asymmetry"      -> Tri.NO
        "we do not KNOW whether they have asymmetry"      -> Tri.UNKNOWN

    In triage those two are completely different situations. A plain
    True/False would silently collapse UNKNOWN into NO, which is exactly the
    "missing data reads as healthy" failure Round 2 forbids.
    """

    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"

    @property
    def is_known(self) -> bool:
        """True only when we actually have an answer."""
        return self is not Tri.UNKNOWN

    @property
    def is_yes(self) -> bool:
        return self is Tri.YES

    @property
    def is_no(self) -> bool:
        return self is Tri.NO

    def __str__(self) -> str:
        return self.value


# ---------------------------------------------------------------------------
# 2. TRIAGE BANDS  --  the Round 1 four-level ladder
# ---------------------------------------------------------------------------

class TriageBand(IntEnum):
    """
    Round 1 acuity ladder. L4 is the MOST urgent.

    This is an IntEnum on purpose: it gives us free, unambiguous comparison.
        TriageBand.L3_PULL > TriageBand.L2_LOOK   -> True

    The Ratchet Engine (Phase 8) is essentially one line of this comparison,
    so getting the ordering right here matters more than it looks.

    NOTE ON NUMBERING: in the standard ESI scale, Level 1 is the most critical
    -- the opposite of ours. We therefore always display the WORD (CODE / PULL
    / LOOK / WATCH) more prominently than the number, and we ship an ESI
    cross-walk in docs/assumptions.md.
    """

    L1_WATCH = 1
    L2_LOOK = 2
    L3_PULL = 3
    L4_CODE = 4

    @property
    def code(self) -> str:
        """'L3'"""
        return f"L{self.value}"

    @property
    def word(self) -> str:
        """'PULL'  -- the label we lead with in the UI."""
        return {
            1: "WATCH",
            2: "LOOK",
            3: "PULL",
            4: "CODE",
        }[self.value]

    @property
    def meaning(self) -> str:
        return {
            1: "Stable, monitored waiting, timed re-check",
            2: "Needs a glance, timely assessment",
            3: "Very urgent, nurse within minutes",
            4: "Resuscitation, immediate action",
        }[self.value]

    def __str__(self) -> str:
        return f"{self.code} {self.word}"


# ---------------------------------------------------------------------------
# 3. AGE BANDS
# ---------------------------------------------------------------------------

class AgeBand(Enum):
    """
    Age groups used to select threshold tables (Phase 4).

    A heart rate of 130 is an emergency in a 40-year-old and unremarkable in a
    9-month-old. A single adult-calibrated engine is therefore unsafe, which is
    why Round 2 makes age-awareness mandatory.

    Boundaries below are SIMULATED DEMONSTRATION VALUES.
    """

    INFANT = "infant"            # under 1 year
    CHILD = "child"              # 1 - 11
    ADOLESCENT = "adolescent"    # 12 - 17
    ADULT = "adult"              # 18 - 64
    GERIATRIC = "geriatric"      # 65 and over

    @classmethod
    def from_age(cls, age_years: float) -> "AgeBand":
        if age_years < 1:
            return cls.INFANT
        if age_years < 12:
            return cls.CHILD
        if age_years < 18:
            return cls.ADOLESCENT
        if age_years < 65:
            return cls.ADULT
        return cls.GERIATRIC

    @property
    def is_pediatric(self) -> bool:
        return self in (AgeBand.INFANT, AgeBand.CHILD, AgeBand.ADOLESCENT)

    def __str__(self) -> str:
        return self.value


# ---------------------------------------------------------------------------
# 4. HISTORY TIERS
# ---------------------------------------------------------------------------

class HistoryTier(Enum):
    """
    How much prior record we have for this patient.

    CORE INVARIANT (enforced by tests in Phase 15):
        ZERO history must RAISE uncertainty and must NEVER LOWER risk.
        A first-time patient is not a healthy patient.
    """

    RICH = "rich"        # conditions, medications, prior visits, baseline
    PARTIAL = "partial"  # some fields only
    ZERO = "zero"        # first-time patient, nothing on file

    @property
    def is_available(self) -> bool:
        return self is not HistoryTier.ZERO

    def __str__(self) -> str:
        return self.value


# ---------------------------------------------------------------------------
# 5. SENSOR CAPTURE STATUS  --  fail-safe behaviour
# ---------------------------------------------------------------------------

class CaptureStatus(Enum):
    """
    Did a given sensor modality actually produce data?

    Round 2 requires graceful degradation: if the facial scan fails, the system
    keeps working on vitals + symptoms + history and RAISES uncertainty.
    Technology failure must never become clinical failure.
    """

    OK = "ok"
    FAILED = "failed"                # sensor error
    REFUSED = "refused"              # patient declined
    NOT_ATTEMPTED = "not_attempted"  # never tried (e.g. no camera at this ED)

    @property
    def has_data(self) -> bool:
        return self is CaptureStatus.OK

    def __str__(self) -> str:
        return self.value


# ---------------------------------------------------------------------------
# 6. PERMANENT FACIAL CONDITIONS  --  the fairness core
# ---------------------------------------------------------------------------

class FacialBaselineCondition(Enum):
    """
    A documented, PERMANENT reason a patient's face may look asymmetric.

    These patients must NOT be auto-flagged as emergencies just because their
    face differs from a typical pattern. The engine's job is to detect ACUTE
    CHANGE, not to judge whether a face is 'normal'.
    """

    NONE = "none"
    CONGENITAL = "congenital"                 # born with asymmetry
    POST_STROKE = "post_stroke"               # chronic weakness, old CVA
    BURN_OR_ACID_INJURY = "burn_or_acid"      # permanent scarring
    SURGICAL = "surgical"                     # post-operative change
    TRAUMA = "trauma"                         # old fracture / injury
    CHRONIC_PALSY = "chronic_palsy"           # e.g. long-standing Bell's palsy
    UNKNOWN = "unknown"                       # no record either way

    @property
    def is_documented(self) -> bool:
        return self not in (
            FacialBaselineCondition.NONE,
            FacialBaselineCondition.UNKNOWN,
        )

    def __str__(self) -> str:
        return self.value


# ---------------------------------------------------------------------------
# 7. CONSCIOUSNESS  (AVPU + UNKNOWN)
# ---------------------------------------------------------------------------

class Consciousness(Enum):
    ALERT = "alert"
    VOICE = "responds_to_voice"
    PAIN = "responds_to_pain"
    UNRESPONSIVE = "unresponsive"
    UNKNOWN = "unknown"

    def __str__(self) -> str:
        return self.value


# ---------------------------------------------------------------------------
# 8. WHO CHANGED THE ACUITY  --  the Ratchet's accountability field
# ---------------------------------------------------------------------------

class ChangedBy(Enum):
    """
    Every acuity transition records its author.

    RATCHET RULE (Phase 8): AI_ESCALATION may only RAISE a band.
    NURSE_OVERRIDE is the ONLY value permitted on a lowering transition.
    """

    SYSTEM_INITIAL = "system_initial"    # first assessment on arrival
    AI_ESCALATION = "ai_escalation"      # engine raised the band
    NURSE_OVERRIDE = "nurse_override"    # human decision, reason mandatory

    def __str__(self) -> str:
        return self.value
