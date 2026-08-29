"""
simulation/clock.py
===================
The simulation clock. Where "triage is not a snapshot" stops being a claim in
the README and becomes the thing the system does.

WHAT THE CLOCK IS FOR
---------------------
Phases 3 to 9 built a pipeline that scores a patient at a moment. Every demo so
far has had to hand it a moment. `--ratchet` and `--audit` used a stand-in that
walked a single patient's authored trajectory by hand, which was honest about
being a stand-in and hid the thing that actually matters: in a real department,
nobody re-scores a patient because the data changed. The data changing is
invisible. Somebody re-scores a patient because TIME PASSED and a policy said
it was due.

So the clock is not a convenience for driving the demo. It is the component
that turns a scoring function into a triage system, because it is the only part
of the design that ever asks a question nobody prompted it to ask.

THREE KINDS OF EVENT, AND ONLY THREE
------------------------------------
    arrival        a patient enters the department and is scored for the
                   first time
    trajectory     the world changes: new vitals, a new symptom, an answered
                   question
    reassessment   the patient's band says they were due a fresh look

The third one is the phase. The first two are the world happening to us; the
third is the system deciding, on its own schedule, to look again.

A CHANGE IS NOT AN OBSERVATION
------------------------------
The first working version of this file assessed the patient the instant a
trajectory event fired, and it was wrong in a way worth writing down, because
it produced a better-looking demo than the correct version does.

A patient's SpO2 falling is not an event the department receives. Nobody is
notified. The number exists in the patient and nowhere else until somebody
takes a set of observations, and in a waiting room that happens when a
reassessment comes due. A clock that scores the moment the world changes has
quietly given the system a sensor it does not have, and -- worse -- makes its
own reassessment schedule decorative, because it can never be the thing that
discovers anything.

So a trajectory event changes the patient's STATE and records that a change is
now pending. The next reassessment is what OBSERVES it. The gap between those
two minutes is detection latency, it is reported per patient, and it is a
property of the hospital's reassessment policy rather than of anything clever
in core/. Running the same roster against `small_ed`, whose intervals are
longer because it has three nurses instead of eight, makes the same
deterioration take longer to find. That number is the most useful thing this
file produces and we could not have printed it at all a phase ago.

The one exception is a patient at CODE, who is off the waiting-room timer and
under continuous human observation; for them a change is seen when it happens.

THE LOOP CLOSES ON ITSELF
-------------------------
A reassessment interval is a property of the CURRENT band, and the band is an
output of the assessment the reassessment produces. So the schedule is not a
fixed timetable laid down on arrival -- it tightens as a patient gets sicker:

    WATCH  -> re-check in 30 minutes
    LOOK   -> re-check in 20
    PULL   -> re-check in 8

and because the ratchet means an automated path can only ever RAISE a band, an
automated path can only ever SHORTEN the loop. The two mechanisms compose into
a property neither has alone: a deteriorating patient is looked at more often,
and nothing the machine can do on its own makes it look less often. That is
worth stating precisely because it is the kind of thing that is usually an
accident of implementation rather than a consequence of the design.

WAITING DOES NOT MAKE A PATIENT SICKER
--------------------------------------
The clock adds no points for time. There is no impatience term, no queue-age
weighting, no drift. A patient re-scored at minute 200 on the same observations
gets exactly the score they got at minute 20.

What DOES change is confidence: the Phase 5 staleness driver decays as the
observations age, so the queue shows a waiting patient becoming less certain
rather than more settled. And when a reassessment is overdue, the clock says so
as a FLAG, never as a score adjustment. A system that quietly escalated people
for waiting would produce a queue that reorders itself by patience, and would
be indistinguishable from one that had detected something.

WHAT THIS CLOCK DOES NOT MODEL, STATED PLAINLY
----------------------------------------------
  * It fires every reassessment exactly when due. No real department achieves
    that, and the gap between the policy and the practice is most of what goes
    wrong in a waiting room. Phase 14 (surge) is where that assumption is
    supposed to get stressed; until then, our timeline is the optimistic case.
  * It models the WAITING ROOM, not the department. A patient at CODE has an
    interval of zero minutes in every hospital profile, which is a way of
    writing "this person should not be waiting at all". They leave the
    reassessment schedule rather than being re-scored infinitely often -- see
    `_schedule_reassessment`.
  * Nothing arrives that was not authored. There is no arrival generator and no
    random deterioration; the roster is 24 hand-written patients and the clock
    replays them. That keeps every run reproducible and every escalation
    traceable to a scenario somebody wrote on purpose, and it means this file
    cannot tell you anything about throughput.
  * It is deterministic. No RNG anywhere. Same roster, same timeline, every
    time -- which is what makes the Phase 15 tests able to assert on it.

SAFETY NOTE: horizon and event-cap values load from data/simulation_config.json
and are SIMULATED DEMONSTRATION SETTINGS.
"""

from __future__ import annotations

import copy
import heapq
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from core.config import HospitalConfig
from core.enums import ChangedBy, TriageBand
from core.ratchet import Ratchet
from core.schema import Assessment, Patient, TimedUpdate

REPO_ROOT = Path(__file__).resolve().parent.parent
SIMULATION_CONFIG_FILE = REPO_ROOT / "data" / "simulation_config.json"


def _load(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return {k: v for k, v in json.load(fh).items() if not k.startswith("_")}


# Event kinds. Strings for the same reason the audit event names are strings:
# they end up in output somebody reads without our source in front of them.
ARRIVAL = "arrival"
TRAJECTORY = "trajectory"
REASSESSMENT = "reassessment"

# Ordering rank when two events land on the same minute. Trajectory before
# reassessment is not cosmetic: if a patient's vitals change at minute 82 and
# they are also due a look at minute 82, the look must see the new numbers.
# Getting this backwards produces a system that reassesses patients on data it
# is about to be told is out of date, and the bug would be nearly invisible.
_KIND_RANK = {ARRIVAL: 0, TRAJECTORY: 1, REASSESSMENT: 2}


class SimulationError(RuntimeError):
    """Raised when the clock detects a condition that should be impossible."""


# ---------------------------------------------------------------------------
# Applying an authored change to a patient
# ---------------------------------------------------------------------------

def apply_update(patient: Patient, update: TimedUpdate) -> Patient:
    """
    Fold one trajectory step into a patient state and return the new state.

    This is the single implementation. Phases 8 and 9 had a copy of it inside
    scripts/run_triage.py labelled as a stand-in; that copy is gone and the
    demos import this one.

    The copy carried a latent bug worth recording, because it is the kind that
    survives a demo and dies in production. It folded every update onto the
    ARRIVAL state rather than onto the running state, so a symptom added by one
    update was silently dropped by the next. It produced correct output for our
    roster only because every authored update happens to carry a complete set of
    vitals. The clock folds cumulatively, which is what "the patient's current
    state" has to mean.

    A new state is returned rather than mutating in place. The risk engine is
    stateless by design and the ratchet is the only component allowed to
    remember anything; sharing a mutable patient between assessments would put
    memory somewhere neither of them can see.
    """
    p = copy.deepcopy(patient)
    if update.vitals:
        p.vitals = update.vitals
    if update.observed:
        p.observed = update.observed
    if update.facial:
        p.facial = update.facial
    for symptom in update.new_symptoms:
        if symptom not in p.self_report.symptoms:
            p.self_report.symptoms.append(symptom)
    return p


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

@dataclass(order=False)
class ClockEvent:
    """One scheduled thing. Immutable once queued."""

    at_minute: int
    kind: str
    patient_id: str
    seq: int                                  # insertion order, breaks ties
    epoch: int = 0                            # see _pop, below
    update: Optional[TimedUpdate] = None
    note: str = ""

    def sort_key(self):
        return (self.at_minute, _KIND_RANK[self.kind], self.seq)

    def __lt__(self, other: "ClockEvent") -> bool:
        return self.sort_key() < other.sort_key()


@dataclass
class TimelineRecord:
    """
    One assessment that actually happened, and why it happened.

    `trigger` is the field that makes the timeline readable as a story rather
    than as a log of numbers: it says whether the system looked because the
    world changed or because its own policy told it to.
    """

    at_minute: int
    patient_id: str
    trigger: str
    risk_score: float
    confidence: float
    proposed_band: TriageBand
    final_band: TriageBand
    previous_band: Optional[TriageBand]
    changed_by: ChangedBy
    reason: str = ""
    note: str = ""
    overdue_by: int = 0
    # Set when this assessment is the one that finally saw a change that had
    # been sitting in the patient, unobserved, since an earlier minute.
    change_occurred_at: Optional[int] = None
    assessment: Optional[Assessment] = None

    @property
    def detection_latency(self) -> int:
        """Minutes between the world changing and this system finding out."""
        if self.change_occurred_at is None:
            return 0
        return max(0, self.at_minute - self.change_occurred_at)

    @property
    def escalated(self) -> bool:
        return (self.previous_band is not None
                and self.final_band > self.previous_band)

    @property
    def held(self) -> bool:
        return (self.previous_band is not None
                and self.proposed_band < self.final_band)

    @property
    def confidence_pct(self) -> int:
        return int(round(self.confidence * 100))


@dataclass
class WorldChange:
    """
    Something that happened to a patient, whether or not anyone noticed.

    Kept separately from the timeline of assessments on purpose. The timeline
    is what the SYSTEM knows; this is what was TRUE. A design that merged them
    would make the gap between the two impossible to measure, and that gap is
    the thing this phase exists to expose.
    """

    at_minute: int
    patient_id: str
    note: str = ""
    observed_at: Optional[int] = None

    @property
    def latency(self) -> Optional[int]:
        if self.observed_at is None:
            return None
        return max(0, self.observed_at - self.at_minute)


@dataclass
class Timeline:
    """Everything the clock did, in order."""

    records: List[TimelineRecord] = field(default_factory=list)
    changes: List[WorldChange] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    # (minute, patient_id, band, due_at) for every reassessment that was due
    # and could not be performed. Empty whenever capacity is unlimited.
    deferrals: List[tuple] = field(default_factory=list)

    def undetected(self) -> List[WorldChange]:
        """Changes still unobserved when the clock stopped."""
        return [c for c in self.changes if c.observed_at is None]

    def detection_latencies(self) -> List[WorldChange]:
        return [c for c in self.changes if c.observed_at is not None]

    def __iter__(self) -> Iterator[TimelineRecord]:
        return iter(self.records)

    def __len__(self) -> int:
        return len(self.records)

    def for_patient(self, patient_id: str) -> List[TimelineRecord]:
        return [r for r in self.records if r.patient_id == patient_id]

    def escalations(self) -> List[TimelineRecord]:
        return [r for r in self.records if r.escalated]

    def by_trigger(self, trigger: str) -> List[TimelineRecord]:
        return [r for r in self.records if r.trigger == trigger]

    def final_bands(self) -> Dict[str, TriageBand]:
        out: Dict[str, TriageBand] = {}
        for r in self.records:
            out[r.patient_id] = r.final_band
        return out

    def late_reassessments(self) -> List[TimelineRecord]:
        """Reassessments that happened, but after they were due."""
        return [r for r in self.records if r.overdue_by > 0]

    def unprompted_escalations(self) -> List[TimelineRecord]:
        """
        Escalations that happened at a reassessment nobody asked for.

        The single most useful query in this file. An escalation on a
        trajectory event means the system reacted to being told something. An
        escalation on a REASSESSMENT event means it went and looked on its own
        schedule and found something -- which is the entire difference between
        a triage tool and a triage system, and it is worth being able to count
        rather than assert.
        """
        return [r for r in self.records
                if r.escalated and r.trigger == REASSESSMENT]


# ---------------------------------------------------------------------------
# The clock
# ---------------------------------------------------------------------------

class SimulationClock:
    """
    Event-driven. Advances to the next scheduled event, never tick by tick.

    A tick loop would spend most of its time doing nothing at one-minute
    resolution and would quietly couple the resolution of the simulation to its
    cost. Jumping between events means a 4-hour shift costs exactly as much as
    there are things that happened in it.
    """

    def __init__(
        self,
        engine,
        patients: List[Patient],
        ratchet: Optional[Ratchet] = None,
        config: Optional[dict] = None,
        capacity=None,
    ):
        cfg = config or _load(SIMULATION_CONFIG_FILE)
        self.engine = engine
        self.hospital: HospitalConfig = engine.hospital
        self.ratchet = ratchet or Ratchet()
        self.horizon = int(cfg["clock"]["default_horizon_minutes"])
        self.max_events = int(cfg["clock"]["max_events_per_run"])

        # Phase 14. None means unlimited nurse time -- every reassessment
        # fires exactly when due, which is what Phases 10-13 assumed and no
        # real department achieves. See simulation/surge.py.
        self.capacity = capacity

        self.roster: Dict[str, Patient] = {p.patient_id: p for p in patients}
        self.state: Dict[str, Patient] = {}          # current state, post-updates
        self.now: int = 0

        self._queue: List[ClockEvent] = []
        self._seq = 0
        # Epoch per patient. Every assessment bumps it, which invalidates any
        # reassessment scheduled by an earlier one. See _pop.
        self._epoch: Dict[str, int] = {}
        self._due_at: Dict[str, Optional[int]] = {}
        self._observed: Dict[str, bool] = {}         # left the waiting room
        # Changes that have happened to a patient but that nobody has looked at
        # yet. The waiting room, in one data structure.
        self._pending: Dict[str, List["WorldChange"]] = {}
        self._deferred: Dict[str, int] = {}

    # -----------------------------------------------------------------------
    # Queue plumbing
    # -----------------------------------------------------------------------

    def _push(self, event: ClockEvent) -> None:
        heapq.heappush(self._queue, event)

    def _schedule(self, at_minute: int, kind: str, patient_id: str,
                  update: Optional[TimedUpdate] = None, note: str = "",
                  epoch: int = 0) -> None:
        self._seq += 1
        self._push(ClockEvent(at_minute=at_minute, kind=kind,
                              patient_id=patient_id, seq=self._seq,
                              epoch=epoch, update=update, note=note))

    def _pop(self) -> Optional[ClockEvent]:
        """
        Next live event, discarding stale ones.

        CANCELLATION, and why it is done this way. When a patient escalates
        from WATCH to PULL, the reassessment their old band scheduled 30 minutes
        out is wrong -- they are now due in 8. A heap has no remove operation,
        so the alternatives are to rebuild the queue on every band change or to
        mark the obsolete event and skip it when it surfaces. We mark: each
        assessment bumps the patient's epoch, and a reassessment carrying an
        older epoch is dropped on arrival.

        This is a standard lazy-deletion queue. It is written out rather than
        left implicit because a silently-skipped event in a system whose whole
        claim is "we look again" would be a very bad bug to have and a very
        hard one to see.
        """
        while self._queue:
            event = heapq.heappop(self._queue)
            if event.kind == REASSESSMENT:
                if event.epoch != self._epoch.get(event.patient_id, 0):
                    continue                        # superseded, drop it
                if self._observed.get(event.patient_id):
                    continue                        # no longer in the waiting room
            return event
        return None

    # -----------------------------------------------------------------------
    # Running
    # -----------------------------------------------------------------------

    def run(self, until_minute: Optional[int] = None) -> Timeline:
        """
        Replay the roster. Returns everything that happened.

        Safe to call once per clock; the queue is built here.
        """
        horizon = self.horizon if until_minute is None else int(until_minute)
        timeline = Timeline()

        for patient in self.roster.values():
            self._schedule(patient.arrival_minute, ARRIVAL, patient.patient_id)
            for update in patient.trajectory:
                self._schedule(update.at_minute, TRAJECTORY,
                               patient.patient_id, update=update,
                               note=update.note)

        processed = 0
        while True:
            event = self._pop()
            if event is None:
                break
            if event.at_minute > horizon:
                break

            processed += 1
            if processed > self.max_events:
                # The epoch scheme above is the thing that keeps this loop
                # finite. This is the independent check on that claim: if it
                # ever stops working we want a loud failure, not a hung
                # process and a demo that dies in front of a judge.
                raise SimulationError(
                    f"event cap of {self.max_events} exceeded at minute "
                    f"{event.at_minute} -- reassessment scheduling is not "
                    f"terminating")

            self.now = event.at_minute
            record = self._handle(event, timeline)
            if record is not None:
                timeline.records.append(record)

        timeline.notes.append(
            f"{processed} events processed to minute {min(self.now, horizon)}")
        return timeline

    def _handle(self, event: ClockEvent,
                timeline: Timeline) -> Optional[TimelineRecord]:
        pid = event.patient_id

        if event.kind == ARRIVAL:
            self.state[pid] = copy.deepcopy(self.roster[pid])
            return self._assess(pid, event.at_minute, ARRIVAL, timeline)

        if event.kind == TRAJECTORY:
            if pid not in self.state:
                # An authored update that fires before its patient arrives is a
                # data error, not something to paper over at runtime.
                raise SimulationError(
                    f"{pid}: trajectory update at minute {event.at_minute} "
                    f"fires before the patient arrives")

            self.state[pid] = apply_update(self.state[pid], event.update)
            change = WorldChange(at_minute=event.at_minute, patient_id=pid,
                                 note=event.note)
            timeline.changes.append(change)
            self._pending.setdefault(pid, []).append(change)

            # A patient off the waiting-room timer is under continuous human
            # observation, so a change reaches us as it happens. Everyone else
            # waits for the next scheduled look, which is the honest model of a
            # waiting room and the whole reason detection latency is a number
            # this file can report.
            if self._observed.get(pid):
                return self._assess(pid, event.at_minute, TRAJECTORY, timeline)
            return None

        due = self._due_at.get(pid)
        overdue = max(0, event.at_minute - due) if due is not None else 0

        # Phase 14. The reassessment is DUE. Whether it can actually happen
        # depends on whether a nurse is free, and a refusal defers the event
        # rather than cancelling it -- the patient stays in the queue and keeps
        # asking. A dropped reassessment would be a patient nobody looks at
        # again, which is a different and much worse thing.
        if self.capacity is not None:
            band = self.ratchet.band(pid)
            if band is not None and not self.capacity.request(
                    event.at_minute, band, due if due is not None else event.at_minute):
                self._deferred[pid] = self._deferred.get(pid, 0) + 1
                self._schedule(event.at_minute + self.capacity.recheck_after,
                               REASSESSMENT, pid, epoch=event.epoch)
                timeline.deferrals.append(
                    (event.at_minute, pid, band,
                     due if due is not None else event.at_minute))
                return None

        return self._assess(pid, event.at_minute, REASSESSMENT, timeline,
                            overdue=overdue)

    def _assess(self, pid: str, minute: int, trigger: str, timeline: Timeline,
                overdue: int = 0) -> TimelineRecord:
        """
        Score the current state, ratchet it, schedule the next look.

        Note the order and note what is absent. Nothing here inspects how long
        the patient has waited, and nothing here passes the wait time to the
        engine. The engine could not use it if we did -- there is no wait-time
        term in core/risk_engine.py -- and this is the file where such a term
        would be convenient to add and wrong to add.
        """
        # This assessment is the moment anything pending becomes known.
        pending = self._pending.pop(pid, [])
        for change in pending:
            change.observed_at = minute
        occurred_at = pending[0].at_minute if pending else None
        note = pending[-1].note if pending else ""

        patient = self.state[pid]
        assessment = self.ratchet.record(
            self.engine.assess(patient, now_minute=minute))

        self._epoch[pid] = self._epoch.get(pid, 0) + 1
        self._schedule_reassessment(pid, minute, assessment.band)

        return TimelineRecord(
            at_minute=minute,
            patient_id=pid,
            trigger=trigger,
            risk_score=assessment.risk_score,
            confidence=assessment.confidence,
            proposed_band=assessment.proposed_band,
            final_band=assessment.band,
            previous_band=assessment.previous_band,
            changed_by=assessment.changed_by,
            reason=assessment.change_reason,
            note=note,
            overdue_by=overdue,
            change_occurred_at=occurred_at,
            assessment=assessment,
        )

    def _schedule_reassessment(self, pid: str, minute: int,
                               band: TriageBand) -> None:
        """
        Book the next look, based on the band the patient is at NOW.

        THE ZERO-INTERVAL CASE. Every hospital profile sets the L4 CODE
        interval to 0 minutes. Read literally that means "re-check immediately",
        and a loop that took it literally would schedule an event at the current
        minute, assess, schedule another at the current minute, and never
        advance the clock again -- an infinite loop that looks exactly like
        thoroughness right up until the process stops responding.

        The interval is not really zero. It is a way of writing "this patient
        must not be sitting in a waiting room", and a patient in a resus bay is
        under continuous human observation rather than on a timer. So they
        leave the reassessment schedule entirely and the clock records that it
        has stopped modelling them, which is honest about the boundary of what
        this file simulates.
        """
        interval = self.hospital.reassess_due_after(band)
        if interval <= 0:
            self._observed[pid] = True
            self._due_at[pid] = None
            return

        self._observed[pid] = False
        due = minute + interval
        self._due_at[pid] = due
        self._schedule(due, REASSESSMENT, pid, epoch=self._epoch[pid])

    # -----------------------------------------------------------------------
    # Reading back
    # -----------------------------------------------------------------------

    def waiting_room(self) -> List[str]:
        """Patients still on the reassessment schedule."""
        return sorted(pid for pid in self.state
                      if not self._observed.get(pid))

    def under_observation(self) -> List[str]:
        """Patients whose band took them off the waiting-room timer."""
        return sorted(pid for pid in self.state if self._observed.get(pid))


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_TRIGGER_LABEL = {
    ARRIVAL: "arrived",
    TRAJECTORY: "changed",
    REASSESSMENT: "due",
}


def render_record(record: TimelineRecord, width: int = 30) -> str:
    """One timeline row."""
    if record.previous_band is None:
        movement = record.final_band.word
    elif record.escalated:
        movement = f"{record.previous_band.word} -> {record.final_band.word}"
    else:
        movement = f"{record.final_band.word}"

    marker = "  "
    if record.escalated:
        marker = "^ " if record.trigger == REASSESSMENT else "* "
    elif record.held:
        marker = "= "

    why = record.reason or record.note or ""
    if len(why) > width:
        why = why[:width - 3] + "..."

    return (f" {marker}{record.at_minute:>4}  {record.patient_id:<6}"
            f"{_TRIGGER_LABEL[record.trigger]:<9}{movement:<17}"
            f"{record.risk_score:>4.0f}{record.confidence_pct:>5}%  {why}")
