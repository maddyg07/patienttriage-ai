# Privacy

> **Read this first.** This is a prototype running on synthetic data. It holds
> no real patient information, no lawful basis has been established, and
> nothing below is legal advice. It is a statement of what the system does and,
> more importantly, what it does not do.

## The conflict at the centre of this design

Phase 9 built an append-only, hash-chained audit log with **no update method
and no delete method** — not disabled, not private, absent — and argued that
the absence of those operations is the entire value of the artefact. A log that
can be corrected is a log that can be quietly corrected.

Data protection law argues the opposite. The **DPDP Act 2023** (India) and the
**GDPR** (EU) both give a person the right to have their data erased, and both
require that data is not kept longer than necessary.

**These cannot both be satisfied by deleting log lines.** Something has to
give, and pretending otherwise is how a prototype becomes an unlawful product.

## How we resolve it

The log never holds a direct identifier. It holds a pseudonym — `P014` — and
the mapping from that pseudonym to a real person lives in exactly one place:
`IdentityVault` in `core/privacy.py`, which is the only class in this
repository with a `forget()` method.

Erasing a person destroys that mapping. `python -m scripts.run_triage --privacy`
demonstrates it against a live log:

```
P014 has 20 entries in the log. The vault can name her.
chain verifies: True

vault.forget('P014')
vault can name her : False
entries remaining  : 20
chain verifies     : True
replay still works : True
```

Nothing was deleted and nothing needed to be. The entries describe the same
events; they now describe a subject nobody can name.

This is the standard resolution and it is **not a loophole**. It is why the log
was designed to carry pseudonyms from Phase 9 onward, rather than a fix
retrofitted once the problem became inconvenient.

The property it depends on — that no direct identifier exists anywhere — is
asserted in `tests/test_privacy.py` rather than trusted, including a planted
identifier that confirms the scanner works. "We do not store names" is exactly
the kind of claim that stays in a README for two years after somebody adds a
`patient_name` field for debugging.

## Pseudonymisation is not anonymisation

This is the most over-claimed thing in health data engineering, so it gets its
own heading rather than a footnote.

What remains after erasure is an age, an arrival time, a set of vital signs,
conditions, medications, and a sequence of acuity changes at **minute-level
timestamps**. For an unusual presentation in a small department on a known
date, that is re-identifiable by anybody who was on shift, and by anybody
holding the department's own patient list for that afternoon.

Under both the GDPR and the DPDP Act, that residue is **very likely still
personal data**.

`IdentityVault.reidentification_risk()` is a method rather than a comment
precisely so it can be printed by anything that makes the erasure claim, and so
the two cannot drift apart.

## Retention

| Artefact | Limit | Rationale |
|---|---|---|
| Identity map | 30 days | Operational only; the shortest useful life |
| Assessment detail | 90 days | Largest and least useful long-term |
| Audit log | 7 years | Clinical-safety record — a **placeholder**, not a legal opinion |

Three clocks on purpose, because the artefacts serve different purposes and the
shortest defensible period differs for each.

**None of these is enforced by code.** There is no scheduler, no purge job and
no expiry check anywhere in this repository. `RetentionPolicy` reports what a
policy would require and what is overdue under it; nothing acts on that.
`RetentionPolicy.enforced()` returns `False` and is a method rather than a
constant so nothing can quietly assume otherwise.

A retention period that nothing enforces is a document, not a control. Saying
so is more useful than a config value implying an automation we do not have.

## Export

`Exporter` drops prototype-only fields and coarsens ages above 89 to `90+`. A
single 97-year-old in a district hospital on a given afternoon is identifiable
by age alone — this is the oldest and cheapest disclosure control there is, and
it is nowhere near sufficient by itself.

The exporter **refuses to run** if a direct identifier field is present rather
than stripping it, because a record containing one is a bug upstream and
silently removing it would hide the bug.

## Lawful basis: NOT ESTABLISHED

Recorded deliberately as unestablished rather than filled with a
plausible-sounding value. A prototype on synthetic data has no lawful basis
question to answer, and writing "legitimate interests" would create the
appearance of an assessment nobody has done.

A test asserts the status string, so filling it in requires deleting a test —
which is a conversation rather than an edit.

Questions a real deployment must answer, none of which we can:

- Who is the controller: the hospital, or whoever operates the model?
- Is automated triage support direct care, or a secondary use requiring a
  separate basis?
- Does any output constitute a decision with legal or similarly significant
  effect? (Relevant to GDPR Art. 22. Our answer is that a nurse decides and the
  system recommends — but that is our characterisation, not a determination.)
- What retention period does the clinical governance lead agree?
- How is a subject access request served against a hash-chained log?
- Where is the log digest anchored, given the log is tamper-evident and not
  tamper-proof?

## What this phase did not build

Listed as **open**, not as future work with a tick next to it. A gap named
honestly is something a deployment can plan around; a gap described as
"planned" is one somebody assumes is handled.

- **No access control.** Any process that can open the file can read the log.
- **No encryption at rest.** The log is plain JSONL on disk.
- **No read auditing.** We log every write and not one read — and unauthorised
  *reading* of records is the thing that actually gets abused in hospital
  systems. This is the largest gap on the page.
- **No DPIA.**
- **No digest anchoring.** Flagged in Phase 9, unfixed. The log remains
  tamper-evident and not tamper-proof: anyone who can write the file can
  recompute the chain.
- **No enforcement of any retention period.**

## Where the code is

| | |
|---|---|
| `core/privacy.py` | vault, retention reporting, export, identifier scanning |
| `data/privacy_config.json` | every value above, with its reasoning |
| `tests/test_privacy.py` | 16 checks, including a planted-identifier teeth test |
| `python -m scripts.run_triage --privacy` | the demonstration |


---

## Live intake capture (Phase 17)

`scripts/run_intake.py` adds a camera and a microphone. That is the largest
privacy surface in this project, so what it does and does not do is set out
here rather than left to be inferred.

### What is captured

A video frame, at the moment the operator presses **Capture frame**. An audio
stream, while the operator holds the microphone open. A speech transcript, if
the browser supports recognition.

### Where it goes

Nowhere. Specifically:

* **Frames** are drawn to an off-screen canvas, reduced to a luminance grid,
  and discarded when the next frame replaces them. No frame is encoded, posted
  or written.
* **Audio** never leaves the browser tab. The analyser reads amplitude in
  memory; there is no recorder, no blob and no upload.
* **The transcript** stays in the page. It is posted only because the operator
  left it in the box and it becomes the chief complaint, which is ordinary
  clinical text, not a recording.
* **The server** writes no file. It holds one in-memory session and forgets it
  when the process stops.

`data/intake_config.json` declares this as `capture_retention`, all zeros, and
`tests/test_intake.py::TestIntakeWritesNothing` fails if a session creates a
file. A policy nothing checks is a sentence, not a control.

### What crosses the wire

Confirmed clinical flags. `asymmetry_observed: yes`, `baseline_known: unknown`,
`spo2: 91`. The payload is the same shape as one record in
`data/patients.json`, which is why the same loader validates it.

### Why loopback only

The server binds `127.0.0.1` and is never exposed on a network interface.
Browsers grant camera and microphone access on a loopback origin without a
certificate, which is what makes this work with no install and no HTTPS setup.
It also means the console cannot be reached from another machine.

### What this is not

It is not a compliance claim. A production deployment would need consent
capture before the camera starts, a retention schedule for anything that is
kept, a DPIA covering biometric processing, and a decision on whether facial
analysis constitutes sensitive personal data under the DPDP Act — which, for a
system inferring health status from appearance, it very likely does. None of
that is implemented here and none of it should be assumed.
