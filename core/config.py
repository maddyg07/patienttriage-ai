"""
core/config.py
==============
Hospital configuration: capacity, staffing, band thresholds, reassessment
intervals, surge settings.

WHY THIS FILE EXISTS
--------------------
Not a single threshold is allowed to be hard-coded inside the engine. Every
number a judge might question lives in a JSON file with a visible
"simulated demonstration values" disclaimer.

That gives us three things at once:
  1. The same engine runs a 3-nurse rural ED and a 15-nurse trauma centre
     (Round 2 scalability requirement) with zero code changes.
  2. Nobody can mistake our numbers for clinical guidelines.
  3. Tuning during the demo is editing a file, not editing logic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from core.enums import TriageBand

# Repo root, resolved from this file's location so it works from any cwd.
REPO_ROOT = Path(__file__).resolve().parent.parent
HOSPITAL_DIR = REPO_ROOT / "data" / "hospitals"


@dataclass
class BandThresholds:
    """
    Score cut-points that map a 0-100 risk score onto the L1-L4 ladder.

    SIMULATED DEMONSTRATION THRESHOLDS -- NOT CLINICAL GUIDELINES.
    """

    l2_min: float
    l3_min: float
    l4_min: float

    def band_for_score(self, score: float) -> TriageBand:
        if score >= self.l4_min:
            return TriageBand.L4_CODE
        if score >= self.l3_min:
            return TriageBand.L3_PULL
        if score >= self.l2_min:
            return TriageBand.L2_LOOK
        return TriageBand.L1_WATCH

    def distance_to_next_band(self, score: float) -> float:
        """
        How many points below the next band boundary this score sits.

        Used by the uncertainty engine (Phase 5): a score of 49 with a boundary
        at 50 is far more fragile than a score of 20, and the plausible band set
        should say so.
        """
        for boundary in (self.l2_min, self.l3_min, self.l4_min):
            if score < boundary:
                return boundary - score
        return 0.0


@dataclass
class HospitalConfig:
    """One ED profile. Loaded from data/hospitals/<name>.json."""

    name: str
    profile_id: str
    daily_volume: int
    nurses_on_shift: int
    treatment_beds: int
    resus_bays: int

    thresholds: BandThresholds

    # Minutes a patient may wait in each band before reassessment is DUE.
    # SIMULATED VALUES -- deliberately configurable, not medical truth.
    reassessment_interval_minutes: Dict[TriageBand, int] = None

    # Minutes a patient in each band should wait before a CLINICIAN sees them.
    # A completely separate axis from acuity, added in Phase 12. Exceeding it
    # is an unmet need, and it is never an input to any score -- the dashboard
    # flags it for a human. A queue that escalated people for waiting would
    # reorder itself by patience and be indistinguishable from one that had
    # detected something.
    time_to_clinician_target_minutes: Dict[TriageBand, int] = None

    surge_multiplier: float = 3.0
    notes: str = ""

    @classmethod
    def from_dict(cls, raw: dict) -> "HospitalConfig":
        intervals = {
            TriageBand.L1_WATCH: raw["reassessment_interval_minutes"]["L1"],
            TriageBand.L2_LOOK: raw["reassessment_interval_minutes"]["L2"],
            TriageBand.L3_PULL: raw["reassessment_interval_minutes"]["L3"],
            TriageBand.L4_CODE: raw["reassessment_interval_minutes"]["L4"],
        }
        targets = {
            TriageBand.L1_WATCH: raw["time_to_clinician_target_minutes"]["L1"],
            TriageBand.L2_LOOK: raw["time_to_clinician_target_minutes"]["L2"],
            TriageBand.L3_PULL: raw["time_to_clinician_target_minutes"]["L3"],
            TriageBand.L4_CODE: raw["time_to_clinician_target_minutes"]["L4"],
        }
        return cls(
            name=raw["name"],
            profile_id=raw["profile_id"],
            daily_volume=raw["daily_volume"],
            nurses_on_shift=raw["nurses_on_shift"],
            treatment_beds=raw["treatment_beds"],
            resus_bays=raw["resus_bays"],
            thresholds=BandThresholds(**raw["band_thresholds"]),
            reassessment_interval_minutes=intervals,
            time_to_clinician_target_minutes=targets,
            surge_multiplier=raw.get("surge_multiplier", 3.0),
            notes=raw.get("notes", ""),
        )

    @classmethod
    def load(cls, profile_id: str) -> "HospitalConfig":
        path = HOSPITAL_DIR / f"{profile_id}.json"
        if not path.exists():
            available = ", ".join(p.stem for p in HOSPITAL_DIR.glob("*.json"))
            raise FileNotFoundError(
                f"No hospital profile '{profile_id}'. Available: {available}"
            )
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

    @classmethod
    def list_profiles(cls) -> list[str]:
        return sorted(p.stem for p in HOSPITAL_DIR.glob("*.json"))

    def reassess_due_after(self, band: TriageBand) -> int:
        return self.reassessment_interval_minutes[band]

    def care_target_for(self, band: TriageBand) -> int:
        """Minutes this band should wait before a clinician sees them."""
        return self.time_to_clinician_target_minutes[band]

    def overdue_by(self, band: TriageBand, waited_minutes: int) -> int:
        """
        Minutes past the care target. Zero when inside it.

        Returns a NUMBER OF MINUTES, not a risk adjustment, and no caller is
        able to turn it into one -- nothing in core/ reads it. It exists to be
        displayed to a person who can act on it.
        """
        target = self.care_target_for(band)
        if target <= 0:
            return 0        # CODE has no waiting target; they go now
        return max(0, waited_minutes - target)

    def describe(self) -> str:
        lines = [
            f"{self.name}  [{self.profile_id}]",
            f"  volume        : {self.daily_volume} patients/day",
            f"  staffing      : {self.nurses_on_shift} nurses on shift",
            f"  capacity      : {self.treatment_beds} beds, {self.resus_bays} resus bays",
            f"  band cutoffs  : L2>={self.thresholds.l2_min}  "
            f"L3>={self.thresholds.l3_min}  L4>={self.thresholds.l4_min}",
            "  reassess every:",
        ]
        for band in (
            TriageBand.L4_CODE,
            TriageBand.L3_PULL,
            TriageBand.L2_LOOK,
            TriageBand.L1_WATCH,
        ):
            lines.append(
                f"      {band.code} {band.word:<6} {self.reassess_due_after(band):>3} min"
                f"   (seen within {self.care_target_for(band):>3} min)"
            )
        lines.append(f"  surge         : {self.surge_multiplier}x arrivals")
        return "\n".join(lines)
