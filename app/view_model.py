"""
app/view_model.py
=================
The bridge between the engine and anything that draws pixels.

WHY THIS FILE EXISTS
--------------------
"The UI contains no logic" is a claim every project makes and almost none can
demonstrate, because the claim is usually enforced by good intentions and dies
the first time a renderer needs one number nobody thought to compute. Three
lines of arithmetic in a template, then five, and eventually the band shown on
screen is not the band the engine produced.

So there is a hard line here. This file ASSEMBLES: it collects assessments,
timelines and question values that `core/` and `simulation/` already produced,
sorts them, groups them and labels them. It does not compute a single clinical
quantity. There is no arithmetic in this file that a nurse could disagree with.

The renderer downstream (app/dashboard.py) touches nothing but these objects.
Swap it for Streamlit, a web API, a printed handover sheet -- the boundary does
not move.

THE TWO LISTS THAT ARE NOT THE QUEUE
------------------------------------
A triage dashboard that only ranks by acuity answers one question: who is
sickest? That is the question the engine is for, and it is not the only one a
charge nurse needs answered.

  * WHO WE MIGHT BE WRONG ABOUT. Ranked by confidence, lowest first. Phase 5
    built this and nothing has ever displayed it. It is a different list from
    the queue and it is where misses come from.

  * WHO IS WAITING PAST THEIR TARGET. Ranked by minutes overdue. This is the
    problem Phase 11 handed forward: P014 reaches PULL and is then re-scored
    eighteen times with nobody coming to see her. The clock was doing exactly
    what it was asked and the answer was useless, because re-scoring a patient
    is not treating them.

Both are deliberately SEPARATE panels rather than a blended priority number. A
single ranking that mixed acuity, uncertainty and waiting time would be making
a clinical trade-off silently, on weights nobody agreed, and would be
impossible to argue with. Three lists a nurse can read against each other beat
one number they have to trust.

SAFETY NOTE: waiting time is displayed and never scored. Nothing in core/ reads
it. See core/config.overdue_by().
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from core.config import HospitalConfig
from core.enums import TriageBand
from core.schema import Assessment, Patient


# ---------------------------------------------------------------------------
# One patient, as the screen needs them
# ---------------------------------------------------------------------------

@dataclass
class PatientCard:
    """
    Everything the dashboard shows about one patient.

    Every field is copied from an object core/ produced. If a value is not on
    this card, the renderer cannot show it -- which is the point.
    """

    patient: Patient
    assessment: Assessment
    waited_minutes: int
    care_target_minutes: int
    overdue_minutes: int
    next_question: Optional[object] = None      # QuestionValue, or None
    acuity_history: List[object] = field(default_factory=list)

    @property
    def patient_id(self) -> str:
        return self.patient.patient_id

    @property
    def band(self) -> TriageBand:
        return self.assessment.band

    @property
    def is_overdue(self) -> bool:
        return self.overdue_minutes > 0

    @property
    def uncertain(self) -> bool:
        return not self.assessment.band_is_certain

    @property
    def top_driver(self) -> str:
        drivers = [c for c in self.assessment.contributions if c.points > 0]
        return drivers[0].label if drivers else "nothing abnormal detected"

    @property
    def dominant_gap(self) -> str:
        q = self.assessment.quality
        if q is None:
            return ""
        driver = q.dominant_driver()
        return driver.name if driver else ""

    @property
    def could_reach(self) -> Optional[TriageBand]:
        """The most urgent band our uncertainty cannot rule out, if higher."""
        worst = self.assessment.worst_plausible_band
        return worst if worst and worst > self.band else None


# ---------------------------------------------------------------------------
# The whole board
# ---------------------------------------------------------------------------

@dataclass
class BoardView:
    """The dashboard's complete input. A renderer needs nothing else."""

    hospital: HospitalConfig
    at_minute: int
    cards: List[PatientCard]
    question_cap: int = 3

    # -- the queue -----------------------------------------------------------

    def by_acuity(self) -> List[PatientCard]:
        """Who is sickest. Band first, then score inside the band."""
        return sorted(self.cards,
                      key=lambda c: (-int(c.band), -c.assessment.risk_score))

    # -- the second list -----------------------------------------------------

    def by_uncertainty(self) -> List[PatientCard]:
        """
        Who we are most likely to be wrong about.

        Deliberately not filtered to the sick ones. A confident CODE and an
        uncertain WATCH are different kinds of problem and the second one is
        the kind that gets missed.
        """
        return sorted(self.cards, key=lambda c: c.assessment.confidence)

    # -- the third list ------------------------------------------------------

    def overdue(self) -> List[PatientCard]:
        """Who has been waiting longer than their band promised."""
        return sorted([c for c in self.cards if c.is_overdue],
                      key=lambda c: (-int(c.band), -c.overdue_minutes))

    # -- the question queue --------------------------------------------------

    def questions(self) -> List[PatientCard]:
        """
        The questions worth asking, most valuable first, CAPPED.

        The cap is the answer to the second problem Phase 11 handed forward. An
        adaptive questioner with a screen in front of a nurse turns into an
        interrogation script by default: it always has one more thing it would
        like to know, every item looks reasonable in isolation, and the list
        grows until it is ignored wholesale.

        Showing three means the nurse reads three. It also forces the ranking
        to be worth something, because a cap is only defensible if the ordering
        underneath it is.
        """
        with_questions = [c for c in self.cards if c.next_question is not None]
        with_questions.sort(key=lambda c: -c.next_question.value)
        return with_questions[:self.question_cap]

    def questions_withheld(self) -> int:
        return max(0, sum(1 for c in self.cards if c.next_question is not None)
                   - self.question_cap)

    # -- summary numbers -----------------------------------------------------

    def band_counts(self) -> Dict[TriageBand, int]:
        counts: Dict[TriageBand, int] = {}
        for card in self.cards:
            counts[card.band] = counts.get(card.band, 0) + 1
        return counts

    def capacity_pressure(self) -> Optional[str]:
        """
        CODE patients against resus bays.

        Reported, never acted on. Phase 7 settled this: a rule that fired less
        often when the department was full would be a rule that triages by bed
        count. Reconciling need against capacity is a nurse's decision under an
        explicit surge policy, made visibly.
        """
        code = self.band_counts().get(TriageBand.L4_CODE, 0)
        if code <= self.hospital.resus_bays:
            return None
        return (f"{code} patients at CODE against "
                f"{self.hospital.resus_bays} resus bays")

    def floored(self) -> List[PatientCard]:
        return [c for c in self.cards if c.assessment.band_was_floored]

    def held(self) -> List[PatientCard]:
        """
        Patients the ratchet is holding above what the engine now proposes.

        Phase 8 named the ratchet's cost in prose -- the queue carries acuity
        that reality has moved past. This is that cost, counted. A department
        adopting the mechanism should be able to see its price on the screen
        rather than read about it in a design document.
        """
        return [c for c in self.cards if c.assessment.band_was_held]


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def build_board(engine, patients: List[Patient], at_minute: int,
                ratchet=None, question_engine=None,
                question_cap: int = 3) -> BoardView:
    """
    Run the pipeline over a roster and arrange the results for display.

    The only place in app/ that calls into core/, and it calls the same public
    entry points scripts/run_triage.py does. There is no dashboard-specific
    code path through the engine, which means the screen cannot disagree with
    the command line.
    """
    hospital = engine.hospital
    cards: List[PatientCard] = []

    for patient in patients:
        assessment = engine.assess(patient, now_minute=at_minute)
        if ratchet is not None:
            assessment = ratchet.record(assessment)

        band = assessment.band
        waited = patient.wait_minutes(at_minute)
        card = PatientCard(
            patient=patient,
            assessment=assessment,
            waited_minutes=waited,
            care_target_minutes=hospital.care_target_for(band),
            overdue_minutes=hospital.overdue_by(band, waited),
            acuity_history=list(ratchet.history(patient.patient_id))
            if ratchet is not None else [],
        )
        if question_engine is not None:
            card.next_question = question_engine.next_question(
                patient, assessment, now_minute=at_minute)
        cards.append(card)

    return BoardView(hospital=hospital, at_minute=at_minute, cards=cards,
                     question_cap=question_cap)


def build_board_from_clock(clock, timeline, question_engine=None,
                           at_minute: Optional[int] = None,
                           question_cap: int = 3) -> BoardView:
    """
    Build the board from a shift that has already been simulated.

    This is the version that matters, and the difference from `build_board` is
    not cosmetic. Assessing the roster fresh at minute 240 scores every patient
    on the state they arrived in -- P014 shows as WATCH, because the two
    deteriorations that happened while she waited are simply not in the
    calculation. That is precisely the snapshot behaviour this project exists
    to argue against, and it would have been very easy to ship on a dashboard
    that looked completely correct.

    So nothing is re-assessed here. Each card carries the LAST assessment the
    clock actually produced for that patient, which is the one that went
    through the ratchet, fired the safety rules and was written to the audit
    log. The screen shows what happened rather than a fresh opinion about it.
    """
    hospital = clock.hospital
    now = at_minute if at_minute is not None else clock.now

    latest: Dict[str, object] = {}
    for record in timeline.records:
        latest[record.patient_id] = record

    cards: List[PatientCard] = []
    for pid, record in latest.items():
        patient = clock.state[pid]
        assessment = record.assessment
        band = record.final_band
        waited = patient.wait_minutes(now)
        card = PatientCard(
            patient=patient,
            assessment=assessment,
            waited_minutes=waited,
            care_target_minutes=hospital.care_target_for(band),
            overdue_minutes=hospital.overdue_by(band, waited),
            acuity_history=list(clock.ratchet.history(pid)),
        )
        if question_engine is not None:
            card.next_question = question_engine.next_question(
                patient, assessment, now_minute=now)
        cards.append(card)

    return BoardView(hospital=hospital, at_minute=now, cards=cards,
                     question_cap=question_cap)
