"""
core/privacy.py
===============
The one component in this system with a delete operation, and the reason the
audit log does not need one.

THE CONFLICT
------------
Phase 9 built an append-only, hash-chained log with no update method and no
delete method, and argued that the absence of those operations is the entire
value of the artefact. A log that can be corrected is a log that can be quietly
corrected.

Data protection law argues the opposite. The DPDP Act 2023 in India and the
GDPR in the EU both give a person the right to have their data erased, and both
require that data is not kept longer than it is needed. Those two positions
cannot both be satisfied by deleting lines from the log.

Something has to give, and pretending otherwise is how a prototype becomes an
unlawful product.

THE RESOLUTION
--------------
The log never holds a direct identifier. It holds a pseudonym -- P014 -- and
the mapping from that pseudonym to a real person lives in exactly one place:
`IdentityVault`, below, which is the only class in this repository with a
`forget()` method.

Erasing a person destroys that mapping. The hash chain stays intact and still
verifies, `replay_bands()` still reconstructs the department's state, and the
entries that remain describe a subject nobody can name.

This is the standard resolution and it is not a loophole. It is why the log was
designed to carry pseudonyms from Phase 9 onward rather than a fix retrofitted
once the problem became inconvenient.

WHAT THIS DOES NOT ACHIEVE
--------------------------
PSEUDONYMISATION IS NOT ANONYMISATION. This is the most over-claimed thing in
health data engineering and it is worth being blunt about.

What remains after erasure is a timeline: an age, an arrival time, a set of
vital signs, conditions, medications, and a sequence of acuity changes with
minute-level timestamps. For an unusual presentation in a small department on a
known date, that is re-identifiable by anybody who was on shift, and by anybody
holding the department's own patient list for that afternoon.

Under both the GDPR and the DPDP Act, that residue is very likely still
personal data. So this module describes itself as REDUCING re-identification
risk. It never claims to eliminate it, and `reidentification_risk()` exists to
force the honest sentence into any output that uses it.

WHAT IS NOT BUILT HERE, AND SHOULD NOT BE PRETENDED
---------------------------------------------------
There is no scheduler, no purge job and no expiry enforcement anywhere in this
repository. `RetentionPolicy` reports what a policy WOULD require and what is
overdue under it; nothing acts on that. A retention period that nothing enforces
is a document, not a control, and a config value implying an automation we do
not have would be worse than the honest gap.

Nor is there access control, encryption at rest, or a lawful basis. The last of
those is deliberately recorded as NOT ESTABLISHED rather than filled with a
plausible-sounding value, because a prototype on synthetic data has no lawful
basis question to answer and inventing one creates the appearance of an
assessment nobody has done.

SAFETY NOTE: values load from data/privacy_config.json and are SIMULATED
DEMONSTRATION SETTINGS. No lawyer has reviewed any of this.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
PRIVACY_CONFIG_FILE = REPO_ROOT / "data" / "privacy_config.json"


def _load(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return {k: v for k, v in json.load(fh).items() if not k.startswith("_")}


class SubjectNotFound(KeyError):
    """Raised when a pseudonym has no identity behind it -- often correctly."""


# ---------------------------------------------------------------------------
# Direct identifiers
# ---------------------------------------------------------------------------

# Field names that would carry a direct identifier if anybody ever added one.
# Checked against the clinical record rather than trusted, because "we do not
# store names" is the kind of claim that stays in a README for two years after
# somebody adds a `patient_name` field for debugging.
DIRECT_IDENTIFIER_FIELDS = (
    "name", "first_name", "last_name", "full_name", "patient_name",
    "date_of_birth", "dob", "address", "postcode", "phone", "email",
    "nhs_number", "hospital_number", "mrn", "aadhaar", "ssn", "nric",
)

_PSEUDONYM = re.compile(r"^P\d+(-[a-z])?$")


def looks_like_a_pseudonym(value: str) -> bool:
    return bool(_PSEUDONYM.match(str(value)))


def scan_for_direct_identifiers(record: dict, where: str = "") -> List[str]:
    """
    Walk a nested record and report any field that looks like a direct
    identifier. Returns paths, not values -- a function that returned the
    identifiers it found would be a convenient way to extract them.
    """
    found: List[str] = []

    def walk(node, path):
        if isinstance(node, dict):
            for key, value in node.items():
                here = f"{path}.{key}" if path else key
                if key.lower() in DIRECT_IDENTIFIER_FIELDS:
                    found.append(here)
                walk(value, here)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{path}[{i}]")

    walk(record, where)
    return found


# ---------------------------------------------------------------------------
# The vault
# ---------------------------------------------------------------------------

@dataclass
class Identity:
    """
    What a real deployment would hold and this prototype does not.

    Present so the boundary is explicit. There is not a single name in this
    repository, and tests/test_privacy.py asserts that rather than trusting it.
    """

    pseudonym: str
    name: str = ""
    date_of_birth: str = ""
    hospital_number: str = ""
    contact: str = ""


class IdentityVault:
    """
    The only place a pseudonym maps to a person, and the only class here with
    a delete operation.

    Deliberately tiny. Every additional thing this holds is another thing that
    has to be erased, another thing that leaks, and another reason the erasure
    claim needs qualifying.
    """

    def __init__(self, config: Optional[dict] = None):
        cfg = config or _load(PRIVACY_CONFIG_FILE)
        self.cfg = cfg
        self.irreversible = bool(cfg["identity"]["erasure_is_irreversible"])
        self._identities: Dict[str, Identity] = {}
        self.erased: List[str] = []

    def enrol(self, identity: Identity) -> str:
        self._identities[identity.pseudonym] = identity
        return identity.pseudonym

    def resolve(self, pseudonym: str) -> Identity:
        if pseudonym not in self._identities:
            raise SubjectNotFound(
                f"{pseudonym} has no identity on file. If this subject was "
                f"erased, that is the correct and intended outcome.")
        return self._identities[pseudonym]

    def knows(self, pseudonym: str) -> bool:
        return pseudonym in self._identities

    def forget(self, pseudonym: str) -> None:
        """
        Erase a person.

        The mapping is destroyed. The clinical record and the audit log are
        untouched, still verify, and still describe the same events -- they
        simply describe a subject nobody can name any more.

        This does not make the remaining data anonymous. See
        `reidentification_risk()`, and see the module docstring before writing
        anything about this in a slide.
        """
        self._identities.pop(pseudonym, None)
        if pseudonym not in self.erased:
            self.erased.append(pseudonym)

    def was_erased(self, pseudonym: str) -> bool:
        return pseudonym in self.erased

    @staticmethod
    def reidentification_risk() -> str:
        """
        The sentence that has to accompany any claim about erasure.

        A method rather than a comment so that it can be printed by the things
        that make the claim, and so it cannot drift out of sync with them.
        """
        return (
            "Pseudonymisation is not anonymisation. What remains is an age, an "
            "arrival time, vital signs, conditions and an acuity timeline at "
            "minute resolution; for an unusual presentation in a small "
            "department on a known date that is re-identifiable by anyone who "
            "was on shift. This residue is very likely still personal data.")


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------

@dataclass
class RetentionFinding:
    artefact: str
    age_days: float
    limit_days: int

    @property
    def overdue(self) -> bool:
        return self.age_days > self.limit_days

    @property
    def overdue_by_days(self) -> float:
        return max(0.0, self.age_days - self.limit_days)


class RetentionPolicy:
    """
    Reports what a policy would require. Enforces nothing.

    That gap is stated rather than hidden. There is no scheduler and no purge
    job in this repository, so this class can tell a governance team what is
    overdue and cannot do anything about it. A retention period nothing
    enforces is a document, not a control, and the useful thing a prototype can
    do is say which one it has.
    """

    def __init__(self, config: Optional[dict] = None):
        cfg = config or _load(PRIVACY_CONFIG_FILE)
        self.limits = cfg["retention"]

    def limit_for(self, artefact: str) -> int:
        key = f"{artefact}_days"
        if key not in self.limits:
            raise KeyError(
                f"no retention limit defined for '{artefact}'. Adding an "
                f"artefact without a limit is how data becomes permanent by "
                f"accident.")
        return int(self.limits[key])

    def review(self, ages_in_days: Dict[str, float]) -> List[RetentionFinding]:
        return [RetentionFinding(name, age, self.limit_for(name))
                for name, age in sorted(ages_in_days.items())]

    @staticmethod
    def enforced() -> bool:
        """Always False, and it is a method so nothing can quietly assume True."""
        return False


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

class Exporter:
    """
    Produces a record safe(r) to hand to someone outside direct care.

    "Safer", not "safe". Every disclosure control here is weak on its own and
    they do not compose into anonymity.
    """

    def __init__(self, config: Optional[dict] = None):
        cfg = config or _load(PRIVACY_CONFIG_FILE)
        self.drop = list(cfg["export"]["drop_fields"])
        self.coarsen_above = int(cfg["export"]["coarsen_age_above"])

    def export_patient(self, record: dict) -> dict:
        """
        Drop prototype-only fields and coarsen the oldest ages.

        The age rule is the oldest and cheapest disclosure control there is: a
        single 97-year-old in a district hospital on a given afternoon is
        identifiable by age alone. It is also nowhere near sufficient by
        itself, which is why `reidentification_risk()` travels with any output
        built from this.
        """
        out = copy.deepcopy(record)
        for field_name in self.drop:
            out.pop(field_name, None)

        age = out.get("age_years")
        if isinstance(age, (int, float)) and age > self.coarsen_above:
            out["age_years"] = f"{self.coarsen_above + 1}+"

        leaks = scan_for_direct_identifiers(out)
        if leaks:
            raise ValueError(
                f"refusing to export: direct identifier fields present at "
                f"{leaks}. Fix the record, not this check.")
        return out

    def export_roster(self, records: List[dict]) -> List[dict]:
        return [self.export_patient(r) for r in records]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def explain_lawful_basis(config: Optional[dict] = None) -> str:
    cfg = config or _load(PRIVACY_CONFIG_FILE)
    basis = cfg["lawful_basis"]
    lines = [f"    status: {basis['status']}", "",
             "    Questions a deployment must answer, none of which we can:"]
    for question in basis["questions_a_deployment_must_answer"]:
        lines.append(f"      - {question}")
    return "\n".join(lines)
