"""
simulation/surge.py
===================
What happens when the department cannot keep up.

THE ASSUMPTION THIS FILE EXISTS TO BREAK
----------------------------------------
Phase 10 shipped a clock that fires every reassessment exactly when it falls
due, and said so in its own docstring: no real department achieves that, and
the gap between the policy and the practice is most of what actually goes wrong
in a waiting room. Every number this project has produced since -- detection
latency, the overdue panel, the escalations found on schedule -- has been the
optimistic case.

This file removes the assumption. Reassessments now cost a nurse's time, there
is a finite amount of it, and when demand exceeds supply something has to give.

WHAT GIVES, AND WHAT DOES NOT
-----------------------------
The tempting design is to relax the band thresholds under load: score a little
harder, so fewer patients come out as PULL when there are no PULL beds. It
would make the board look calmer immediately. It is also the single worst thing
this system could do, because it makes the department look better while making
the patients no safer, and the distortion is invisible -- the arithmetic still
looks principled.

So capacity constrains OBSERVATION and never ACUITY. What degrades under surge
is how often we can look at people. How sick we judge them to be does not move
by a single point, and `assert_invariants()` fails loudly if it ever does.

This was settled in Phase 1 without anybody noticing: large_ed.json has carried
the line "Band cutoffs are IDENTICAL across all three profiles by design.
Capacity changes how often we look at a patient; it never changes how sick we
judge them to be" since the first commit. This file is that sentence turned
into a mechanism.

DEFERRED, NEVER DROPPED
-----------------------
A reassessment that cannot happen now goes back in the queue and asks again in
a few minutes. It is never cancelled. The distinction carries the whole safety
argument: a deferred patient is one somebody will get to, and a dropped one is
a patient nobody will ever look at again. Under heavy load the deferral counts
get ugly, which is the correct thing for them to do -- the number is supposed
to be a measure of how far behind the department is, and a policy that quietly
discarded the backlog would report zero and mean nothing.

RATIONING BY ACUITY, NOT BY ARRIVAL ORDER
-----------------------------------------
When the budget is short, someone waits. Left alone, that would be whoever the
event queue happened to reach first, which is a lottery. Instead each band is
barred from consuming the last portion of the budget, reserved for anyone
sicker -- including sicker patients we have not met yet. A WATCH patient can
only spend while most of the budget remains; a PULL patient can spend the last
token.

The consequence is uncomfortable and worth stating rather than burying: WATCH
patients absorb almost all of the deferral. That is defensible triage, and it
is also precisely where P014 was when she started deteriorating. The patient
this entire project was built around is the one whose observation gets starved
first under load. We think the policy is still right, and we would rather print
that finding than let it sit undiscovered in a config file.

THE SURGE ROSTER IS A LOAD TEST
-------------------------------
There is no arrival generator here and there never has been. To create load we
replicate the authored roster, and the copies are labelled as copies. That
makes this a legitimate test of what happens when demand triples, and it makes
it useless as a statement about case mix -- a real surge is not three of every
patient. Nothing here should be read as a claim about what a department
actually receives.

SAFETY NOTE: every value loads from data/surge_config.json and is a SIMULATED
DEMONSTRATION SETTING. This is not a surge escalation policy.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from core.config import HospitalConfig
from core.enums import TriageBand
from core.schema import Patient

REPO_ROOT = Path(__file__).resolve().parent.parent
SURGE_CONFIG_FILE = REPO_ROOT / "data" / "surge_config.json"


def _load(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return {k: v for k, v in json.load(fh).items() if not k.startswith("_")}


class SurgeInvariantBroken(RuntimeError):
    """
    Raised when surge handling has changed something it must never change.

    Should be unreachable. It exists because "we never relax thresholds under
    load" is a claim, and a safety claim is worth more when the code crashes
    rather than quietly complying if it turns out to be false.
    """


# ---------------------------------------------------------------------------
# Building a surge roster
# ---------------------------------------------------------------------------

def build_surge_roster(patients: List[Patient], multiplier: int,
                       stagger_minutes: int = 0) -> List[Patient]:
    """
    Replicate the authored roster to create load.

    Each copy keeps its patient's own internal timing -- the offsets between
    arrival, observations and trajectory updates are preserved exactly, so a
    copy of P014 deteriorates at P014's rate and not three times faster.
    Compressing a patient's physiology to simulate a busy department would be
    modelling a different disease, not a different workload.

    Copies are suffixed (P014-b, P014-c) and are obviously copies. This is a
    LOAD TEST. It says nothing about case mix, because a real surge is not
    three of every patient.
    """
    if multiplier <= 1:
        return list(patients)

    roster: List[Patient] = list(patients)
    suffixes = "bcdefghijklmnop"

    for copy_index in range(1, multiplier):
        offset = stagger_minutes * copy_index
        suffix = suffixes[copy_index - 1]
        for original in patients:
            clone = copy.deepcopy(original)
            clone.patient_id = f"{original.patient_id}-{suffix}"
            clone.arrival_minute = original.arrival_minute + offset
            if clone.vitals.measured_at_minute is not None:
                clone.vitals.measured_at_minute += offset
            for update in clone.trajectory:
                update.at_minute += offset
                if update.vitals and update.vitals.measured_at_minute is not None:
                    update.vitals.measured_at_minute += offset
            clone.scenario_label = f"[surge copy] {original.scenario_label}"
            roster.append(clone)

    return roster


# ---------------------------------------------------------------------------
# Capacity
# ---------------------------------------------------------------------------

@dataclass
class Deferral:
    """One reassessment that could not happen when it was due."""

    at_minute: int
    patient_id: str
    band: TriageBand
    due_at: int

    @property
    def late_by(self) -> int:
        return max(0, self.at_minute - self.due_at)


class SurgeController:
    """
    A budget of nurse-minutes, and a policy for who gets them when short.

    The clock asks `request()` before performing any reassessment. A refusal
    defers the event; it never cancels one.
    """

    def __init__(self, hospital: HospitalConfig, config: Optional[dict] = None):
        cfg = config or _load(SURGE_CONFIG_FILE)
        self.hospital = hospital
        self.cfg = cfg
        cap = cfg["capacity"]

        per_hour = (float(cap["reassessments_per_nurse_per_hour"])
                    * hospital.nurses_on_shift
                    * float(cap["nurse_fraction_available_for_reassessment"]))
        self.per_minute = per_hour / 60.0
        self.burst = self.per_minute * float(cap["burst_minutes"])

        self.reserved = {
            TriageBand.L1_WATCH: float(cfg["deferral"]["reserved_capacity_fraction"]["L1"]),
            TriageBand.L2_LOOK: float(cfg["deferral"]["reserved_capacity_fraction"]["L2"]),
            TriageBand.L3_PULL: float(cfg["deferral"]["reserved_capacity_fraction"]["L3"]),
            TriageBand.L4_CODE: float(cfg["deferral"]["reserved_capacity_fraction"]["L4"]),
        }
        self.recheck_after = int(cfg["deferral"]["recheck_after_minutes"])
        self.starvation_minutes = float(cfg["deferral"]["starvation_minutes"])
        self._max_logged = int(cfg["deferral"]["max_deferrals_logged"])

        self.tokens = self.burst
        self._last_minute = 0
        self.performed = 0
        self.deferred = 0
        self.deferrals: List[Deferral] = []
        self.deferrals_by_band: Dict[TriageBand, int] = {b: 0 for b in TriageBand}
        self.performed_by_band: Dict[TriageBand, int] = {b: 0 for b in TriageBand}

        # A snapshot of everything surge is forbidden to touch, taken before
        # any load is applied and checked afterwards.
        self._baseline = self._snapshot()

    # -----------------------------------------------------------------------
    # Budget
    # -----------------------------------------------------------------------

    def _replenish(self, minute: int) -> None:
        elapsed = max(0, minute - self._last_minute)
        self.tokens = min(self.burst, self.tokens + elapsed * self.per_minute)
        self._last_minute = minute

    def request(self, minute: int, band: TriageBand, due_at: int) -> bool:
        """
        May this reassessment happen now?

        Two rules, and the second exists because the first on its own is
        dangerous.

        ACUITY GOES FIRST. A band may spend only while the budget is above its
        reserved floor, so the last of the capacity is kept for whoever is
        sickest -- including sicker patients we have not met yet.

        NOBODY IS FORGOTTEN. That floor decays the longer a patient has been
        waiting past due, reaching zero at `starvation_minutes`. After that
        they can spend the last token like anybody else.

        The second rule is not a refinement, it is a correction, and the
        measurement that forced it is in the phase notes. Reserving capacity
        purely by current acuity looks obviously right and starves the WATCH
        patients completely: at 3x load the reserve-only policy re-checked the
        already-PULL patients constantly, returned the same band every time,
        and detected NOT ONE of the fifteen deteriorations in the roster. P014
        -- the patient this whole project was built around -- was never looked
        at again.

        The reason generalises well beyond this file. Rationing observation by
        current acuity is rationing by what we already know, and observation
        exists to find out what we do not. It is the same mistake Phase 11
        refused to make when it declined to rank questions by confidence
        gained: value lives in what might change, not in what is settled.
        """
        self._replenish(minute)

        late_by = max(0, minute - due_at)
        aging = (max(0.0, 1.0 - (late_by / self.starvation_minutes))
                 if self.starvation_minutes > 0 else 0.0)
        floor = self.reserved[band] * self.burst * aging

        if self.tokens >= 1.0 and self.tokens - 1.0 >= floor - 1e-9:
            self.tokens -= 1.0
            self.performed += 1
            self.performed_by_band[band] += 1
            return True

        self.deferred += 1
        self.deferrals_by_band[band] += 1
        if len(self.deferrals) < self._max_logged:
            self.deferrals.append(
                Deferral(at_minute=minute, patient_id="", band=band, due_at=due_at))
        return False

    # -----------------------------------------------------------------------
    # Reporting
    # -----------------------------------------------------------------------

    @property
    def total_requests(self) -> int:
        return self.performed + self.deferred

    @property
    def deferral_rate(self) -> float:
        return self.deferred / self.total_requests if self.total_requests else 0.0

    def capacity_per_hour(self) -> float:
        return self.per_minute * 60.0

    def deferral_share(self) -> Dict[TriageBand, float]:
        """What fraction of each band's due reassessments were deferred."""
        out = {}
        for band in TriageBand:
            asked = self.performed_by_band[band] + self.deferrals_by_band[band]
            out[band] = (self.deferrals_by_band[band] / asked) if asked else 0.0
        return out

    # -----------------------------------------------------------------------
    # The invariants
    # -----------------------------------------------------------------------

    def _snapshot(self) -> dict:
        t = self.hospital.thresholds
        return {
            "l2_min": t.l2_min, "l3_min": t.l3_min, "l4_min": t.l4_min,
            "targets": {b.code: self.hospital.care_target_for(b)
                        for b in TriageBand},
            "intervals": {b.code: self.hospital.reassess_due_after(b)
                          for b in TriageBand},
        }

    def assert_invariants(self) -> None:
        """
        Confirm surge changed nothing it was forbidden to change.

        The list in data/surge_config.json is documentation; this is the check.
        Band cutoffs, care targets and reassessment intervals are compared
        against a snapshot taken before any load was applied.

        The care targets are the uncomfortable one and they are checked
        deliberately. Under surge the overdue panel goes red and stays red, and
        that is correct: moving the target because the department is busy would
        mean the number reports how busy we are willing to admit we are, rather
        than how long patients actually waited. A target that relaxes under
        load measures nothing.
        """
        now = self._snapshot()
        if now != self._baseline:
            raise SurgeInvariantBroken(
                f"surge handling altered something it must not: "
                f"{self._baseline} -> {now}")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def explain_capacity(controller: SurgeController) -> str:
    h = controller.hospital
    cap = controller.cfg["capacity"]
    lines = [
        f"    {h.nurses_on_shift} nurses x "
        f"{cap['reassessments_per_nurse_per_hour']}/hour x "
        f"{cap['nurse_fraction_available_for_reassessment']:.0%} available",
        f"    = {controller.capacity_per_hour():.0f} reassessments/hour "
        f"({controller.per_minute:.2f}/min)",
    ]
    return "\n".join(lines)
