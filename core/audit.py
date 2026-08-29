"""
core/audit.py
=============
The append-only audit log. Where "who changed this band, when, and why" stops
living in a dictionary that dies with the process.

TWO READERS, ONE FILE
---------------------
A nurse asks: why is this patient at CODE, and who put them there? They need
one patient's decision trail, in order, in English.

A governance team asks: has this system EVER lowered a band without a human
behind it? They need to run one query across every entry ever written --
including entries written by a version of this code they are not reading, and
possibly by a version nobody at the hospital has seen.

The second reader is the reason the log is a plain file with a verifiable
structure rather than an object with methods. `Ratchet.audit_violations()` in
Phase 8 could only answer that question about the objects currently in memory,
which means it could only answer it for people who trust the running code. The
same query over a JSONL file needs no such trust.

APPEND-ONLY IS A STRUCTURAL CLAIM
---------------------------------
There is no update method here and no delete method. Not disabled, not private:
the operations do not exist. A log that can be corrected is a log that can be
quietly corrected, and the entire value of the artefact is that it records what
happened rather than what someone later wished had happened.

TAMPER-EVIDENT, NOT TAMPER-PROOF
--------------------------------
Each entry carries the hash of the entry before it. Edit a historical line or
remove one, and every hash after it breaks; `verify()` reports the first
sequence number where the chain fails.

That is a real property and it is worth being precise about its limit, because
the temptation to oversell it in a pitch is considerable. Anyone who can write
this file can also recompute the entire chain and produce a perfectly valid log
that says whatever they like. Hash chaining detects casual and accidental
alteration -- a corrected reason, a deleted embarrassing line, a partial write
after a crash. It does not defend against a determined administrator. Real
tamper resistance requires anchoring the digest somewhere the writer does not
control: an append-only store, a signing service, a periodic digest sent
off-site. That is a deployment decision, and claiming we have solved it would
be a lie a competent security reviewer would find in about a minute.

WHAT GETS LOGGED, AND WHAT DELIBERATELY DOES NOT
------------------------------------------------
Band transitions, accepted overrides, and REJECTED overrides. That last one
matters: a system that records only successful actions hides the pattern most
worth seeing. Three refused de-escalation attempts before an accepted one is
clinically meaningful and completely invisible in a log of outcomes.

Routine assessments are off by default. Phase 10 re-triages continuously, and
logging every re-score of every waiting patient produces a file in which the
decisions that matter are buried in noise.

SAFETY AND PRIVACY NOTE: this log holds patient identifiers and acuity history,
which is health information. Retention, access control and lawful basis are
Phase 16 and are NOT settled by anything in this file.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
AUDIT_CONFIG_FILE = REPO_ROOT / "data" / "audit_config.json"


def _load(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return {k: v for k, v in json.load(fh).items() if not k.startswith("_")}


# Event names. Strings rather than an enum because they are written to a file
# that outlives this code, and a consumer three years from now should not need
# our enum definition to read it.
BAND_TRANSITION = "band_transition"
OVERRIDE_ACCEPTED = "override_accepted"
OVERRIDE_REJECTED = "override_rejected"
ASSESSMENT = "assessment"


@dataclass
class AuditEntry:
    """
    One line of the log.

    Field names are deliberately boring and self-describing. Somebody will
    eventually read this file with `grep` and no documentation, and that person
    is a legitimate user.
    """

    seq: int
    event: str
    patient_id: str
    at_minute: int
    recorded_at: str
    payload: Dict = field(default_factory=dict)
    actor_id: str = ""
    prev_hash: str = ""
    entry_hash: str = ""

    def content_for_hash(self) -> str:
        """
        Everything except the entry's own hash, canonically ordered.

        `sort_keys` is not cosmetic: the hash must not depend on dictionary
        insertion order, or a log written by a different Python version fails
        to verify for no reason anybody could diagnose.
        """
        body = {k: v for k, v in asdict(self).items() if k != "entry_hash"}
        return json.dumps(body, sort_keys=True, separators=(",", ":"))

    def compute_hash(self, algorithm: str = "sha256") -> str:
        digest = hashlib.new(algorithm)
        digest.update(self.content_for_hash().encode("utf-8"))
        return digest.hexdigest()


class AuditLog:
    """
    Append-only, hash-chained, file-backed.

    Note the absence. There is no `update`, no `delete`, no `amend`, no
    `correct`. A correction is a new entry that says a correction was made,
    which is how a clinical record works and for the same reason.
    """

    def __init__(self, config: Optional[dict] = None, path: Optional[Path] = None):
        cfg = config or _load(AUDIT_CONFIG_FILE)
        self.cfg = cfg
        self.algorithm = cfg["integrity"]["algorithm"]
        self.genesis = cfg["integrity"]["genesis_hash"]
        self.enabled_events = cfg["events"]
        self.path = Path(path) if path else REPO_ROOT / cfg["log"]["path"]
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # -----------------------------------------------------------------------
    # Writing
    # -----------------------------------------------------------------------

    def append(self, event: str, patient_id: str, at_minute: int,
               payload: Optional[Dict] = None, actor_id: str = "") -> Optional[AuditEntry]:
        """
        Write one entry. Returns None if this event type is switched off.

        The only write operation in the class.
        """
        if not self.enabled_events.get(event, True):
            return None

        seq, prev_hash = self._tail()
        entry = AuditEntry(
            seq=seq + 1,
            event=event,
            patient_id=patient_id,
            at_minute=at_minute,
            recorded_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            payload=payload or {},
            actor_id=actor_id,
            prev_hash=prev_hash,
        )
        entry.entry_hash = entry.compute_hash(self.algorithm)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(entry), sort_keys=True) + "\n")
        return entry

    def _tail(self):
        last = None
        for entry in self.read():
            last = entry
        return (last.seq, last.entry_hash) if last else (0, self.genesis)

    # -----------------------------------------------------------------------
    # Reading
    # -----------------------------------------------------------------------

    def read(self) -> Iterator[AuditEntry]:
        if not self.path.exists():
            return
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                yield AuditEntry(**json.loads(line))

    def entries(self) -> List[AuditEntry]:
        return list(self.read())

    def for_patient(self, patient_id: str) -> List[AuditEntry]:
        return [e for e in self.read() if e.patient_id == patient_id]

    # -----------------------------------------------------------------------
    # Integrity
    # -----------------------------------------------------------------------

    def verify(self):
        """
        Walk the chain. Returns (ok, problems).

        Detects an edited entry, a removed entry, a reordered entry and a
        truncated write. Does not detect a wholesale rewrite by someone who
        recomputed the chain -- see the module docstring, and please do not let
        that limitation get lost in a slide.
        """
        problems: List[str] = []
        expected_prev = self.genesis
        expected_seq = 1

        for entry in self.read():
            if entry.seq != expected_seq:
                problems.append(
                    f"seq {entry.seq}: expected {expected_seq} "
                    f"(an entry was removed or reordered)")
            if entry.prev_hash != expected_prev:
                problems.append(
                    f"seq {entry.seq}: chain broken, this entry was written "
                    f"against a different predecessor")
            recomputed = entry.compute_hash(self.algorithm)
            if recomputed != entry.entry_hash:
                problems.append(
                    f"seq {entry.seq}: content has been altered since it "
                    f"was written")
            expected_prev = entry.entry_hash
            expected_seq = entry.seq + 1

        return (not problems), problems

    # -----------------------------------------------------------------------
    # Queries a governance team would actually run
    # -----------------------------------------------------------------------

    def ratchet_violations(self) -> List[AuditEntry]:
        """
        Every band that went DOWN without a nurse behind it.

        The Phase 8 promise, asked of a file instead of a running process. This
        is the version that means something to somebody who does not trust our
        code, because it does not require them to run our code.
        """
        out = []
        for e in self.read():
            if e.event != BAND_TRANSITION:
                continue
            p = e.payload
            if p.get("direction") == "down" and p.get("changed_by") != "nurse_override":
                out.append(e)
        return out

    def unattributed_overrides(self) -> List[AuditEntry]:
        """Accepted overrides with nobody's name on them."""
        return [e for e in self.read()
                if e.event == OVERRIDE_ACCEPTED and not e.actor_id.strip()]

    def rejected_before_accepted(self) -> Dict[str, int]:
        """
        How many refusals each patient's eventual override took.

        Not a compliance check. A high count is a signal about the interface or
        about a clinician under pressure, and it only exists because rejections
        are logged at all.
        """
        counts: Dict[str, int] = {}
        for e in self.read():
            if e.event == OVERRIDE_REJECTED:
                counts[e.patient_id] = counts.get(e.patient_id, 0) + 1
        return counts

    def replay_bands(self) -> Dict[str, str]:
        """
        Reconstruct every patient's current band from the log alone.

        This is the completeness test. If replaying the log reproduces the
        live system's state, then nothing that determines a patient's acuity
        exists only in memory -- which is the property that makes the log an
        actual record rather than a diary of selected highlights.
        """
        bands: Dict[str, str] = {}
        for e in self.read():
            if e.event in (BAND_TRANSITION, OVERRIDE_ACCEPTED):
                to_band = e.payload.get("to_band")
                if to_band:
                    bands[e.patient_id] = to_band
        return bands


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_entry(entry: AuditEntry) -> str:
    p = entry.payload
    if entry.event == BAND_TRANSITION:
        arrow = (f"{p.get('from_band')} -> {p.get('to_band')}"
                 if p.get("from_band") else f"{p.get('to_band')}")
        # A hold is not authored by anyone; showing the inherited attribution
        # would misdescribe what happened.
        author = ("ratchet_held" if p.get("direction") == "held"
                  and p.get("flags") else p.get("changed_by", ""))
        head = f"{arrow:<16} {author}"
    elif entry.event == OVERRIDE_ACCEPTED:
        head = (f"{p.get('from_band')} -> {p.get('to_band'):<8} "
                f"OVERRIDE by {entry.actor_id}")
    elif entry.event == OVERRIDE_REJECTED:
        head = f"override REFUSED: {p.get('rejection', '')}"
    elif entry.event == "patient_seen":
        head = (f"SEEN by {entry.actor_id} at {p.get('band_when_seen')} "
                f"after {p.get('waited_minutes')} min")
    elif entry.event == "question_answered":
        changed = "" if p.get("changed_record") else "  (record unchanged)"
        head = (f"{entry.actor_id} answered \"{p.get('answer')}\""
                f"{changed}")
    elif entry.event == "question_unanswered":
        head = f"question asked, NO ANSWER obtained ({p.get('question_id')})"
    else:
        head = entry.event

    line = (f"  #{entry.seq:<4}t={entry.at_minute:<5}{entry.patient_id:<7}"
            f"{head}")
    if p.get("reason"):
        line += f"\n{'':<20}\"{p['reason']}\""
    for flag in p.get("flags", []):
        line += f"\n{'':<20}! {flag}"
    return line
