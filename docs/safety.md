# Safety

The argument, and where each part of it is checked.

> This document describes what the system is built to do and how that is
> enforced. It is **not** clinical validation — see `docs/limitations.md`, which
> is longer than this page and should be read with it.

## The shape of the argument

A triage system fails in two directions and they are not symmetric.

**Over-triage** wastes a nurse's time. **Under-triage** kills people.

Most systems acknowledge this and implement it as a weighting: score a little
higher, set thresholds a little lower, and let the model move a patient in
either direction as its inputs change. That is a thumb on the scale, not
asymmetry, and a sufficiently confident model will still walk a deteriorating
patient back down.

Here the asymmetry is **structural**: it is a property of what is absent from
the code. Four independent stages can raise a patient's acuity and none of them
has a code path capable of lowering it.

## The five properties

### 1. Only a human can lower acuity

`final = max(proposed, previous)` on every automated path through
`core/ratchet.py`. No branch, no flag, no config value produces a lower band,
and `RatchetViolation` is raised rather than quietly complying if one ever
appears.

De-escalation exists in exactly one function, gated on three things: an
identifier, a reason that survives validation, and acknowledgement of any safety
rule currently holding the floor.

*Checked by:* `tests/test_ratchet.py` — including a planted forged
de-escalation, because `audit_violations() == []` proves nothing if it would
also return empty for a broken system.

**The cost, stated:** a patient who genuinely improves keeps their old band
until a human agrees. The queue sometimes carries acuity reality has moved past,
and `--board` counts it. We think that is the right side to be wrong on. It is
not free.

### 2. Missing data never becomes reassurance

`Tri.UNKNOWN` exists so that "we do not know" cannot collapse into "no". The
loader fails loudly on unrecognised values and falls back to UNKNOWN only for
absent fields.

The precise claim — and writing the test suite is what forced the precision —
is **not** that removing data can never lower a score. It can, and it should:
points are earned by evidence, and a patient cannot be charged for a fever
nobody measured. What must hold is that an absent value contributes *nothing*
and is never scored as though it had been measured and found normal.

*Checked by:* `tests/test_uncertainty.py`.

**Known gap:** removing a dissenting signal can *raise* confidence, in 13 of 210
deletions. Pinned in the suite. See `docs/limitations.md`.

### 3. Appearance is never scored — only change

`core/facial.py` asks one question: has this face changed? It has no concept of
a correct face. A patient with congenital asymmetry, acid-attack scarring or
chronic post-stroke weakness scores zero from the face.

The claim is checkable rather than rhetorical: `fairness_counterfactual()`
re-scores a patient once per possible *cause* of their difference, changing
nothing else, and the points must not move. A system that flagged the
acid-attack survivor but not the congenital case would pass a naive fairness
check and fail this one.

**The direction that matters:** a weak baseline lowers confidence and never
raises the score. The obvious alternative — treat an unverifiable baseline as
possibly acute and escalate — sounds cautious and escalates hardest on patients
without regular care, without records, and without a relative to speak for
them.

*Checked by:* `tests/test_fairness.py`.

### 4. The system cannot improve its own numbers

Two separate mechanisms, same reasoning.

**Nothing can mark a patient as seen.** No automated path, no default, no batch
operation. "Waiting past target" is the panel that says the department is not
keeping up, and a system able to clear its own overdue list could make that
panel look healthy without anybody being treated.

**Capacity never relaxes acuity.** Under surge, what degrades is how often we
can look — not how sick we judge someone to be. Band cutoffs, safety rules, the
ratchet, care targets and audit logging are compared against a pre-load snapshot
and `SurgeInvariantBroken` is raised if any moved.

An engine that can improve its own reported metrics will eventually be tuned to
do so, whether or not anybody sets out to cheat.

*Checked by:* `tests/test_accountability.py`, both with teeth tests.

### 5. The record survives the process

Band transitions, accepted overrides and **refused** overrides go to an
append-only hash-chained log with no update and no delete method. A log of
outcomes alone would show one clean de-escalation and hide that it took three
attempts.

`replay_bands()` reconstructs the department's state from the log alone and
matches the running system — which is what separates a record from a diary of
selected highlights.

**Tamper-evident, not tamper-proof.** Anyone who can write the file can
recompute the chain. Hash chaining catches casual alteration, a quietly
corrected reason, a crash mid-write. Real resistance needs the digest anchored
somewhere the writer does not control, and we have not done that.

*Checked by:* `tests/test_accountability.py`.

## Restraint as a safety property

Three places where doing less was the safer choice.

**The safety guard fires on 8 of 24 patients, 3 binding.** If it fired on most
of the board it would have replaced the ranking engine with a lookup table; if
nothing ever bound, the rules would be decoration. Both failure modes are
visible in that one line.

**The question queue is capped at three.** An adaptive questioner with a screen
in front of a nurse becomes an interrogation script by default — it always has
one more reasonable-looking thing it would like to know.

**The board shows three lists, not one number.** A single ranking blending
acuity, uncertainty and waiting time would hide a clinical trade-off inside
weights nobody agreed and would be impossible to argue with.

## Where the system deliberately stops

- It does not decide who to see next.
- It does not allocate staff or beds.
- It does not diagnose.
- It does not know how full the department is when applying rules.
- It does not mark anybody as seen.

## Running the argument

```bash
python -m scripts.run_tests
```

52 checks grouped by claim, plus pinned known gaps. Sabotaging the ratchet with
a one-line change turns three tests red across two claim groups and flips the
verdict.

A green run means the system behaves the way this repository says it does. It
does not mean the behaviour is clinically correct.
