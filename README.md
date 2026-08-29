# PatientTriage.ai

**Continuous, explainable, safety-biased triage support for emergency departments.**

Accenture Innovation Challenge 2026 — Round 2 prototype
Team **Slayers** — Madhav Goel, Mohammad Ansar, Pratyaksh Khanna (IIT Delhi)

---

> ### Disclaimer, up front
> This is a **prototype running entirely on synthetic data**. Every threshold,
> weight and patient in this repository is a **simulated demonstration value**.
> Nothing here is clinically validated, and nothing here should be used for
> any real clinical decision.

---

## The problem

Triage is usually a snapshot: a patient is scored once, at the door, and then
waits. But risk is not static. Patients deteriorate in waiting rooms, and the
system that assessed them has already stopped looking.

Four failure modes we are targeting:

| | |
|---|---|
| **Waiting queues** | Visible delays in emergency care |
| **Cognitive bottleneck** | Manual intake under time pressure causes errors |
| **Silent deterioration** | Critical change missed in *waiting* patients |
| **Asymmetric risk** | Algorithms optimise average accuracy, not safety |

## The approach

Signals in → quality-checked → age-adjusted → scored → uncertainty attached →
safety rules applied → ratchet applied → **human decides** → back into the loop,
with everything logged.

Three things make it different from a severity classifier:

**1. The Ratchet Engine.** The AI can raise a patient's acuity. It can never
lower it. Only a nurse can de-escalate, and only with a logged reason. Safety
asymmetry is a mechanism here, not a slogan — enforced by the absence of a code
path, and checkable after the fact with `audit_violations()`.

**2. Baseline-aware facial reasoning.** A patient with congenital asymmetry,
acid-attack scarring or chronic post-stroke weakness must not be flagged as an
emergency because their face is unusual. We separate *what a patient looks like*
from *what has changed acutely*, and we return `UNKNOWN` rather than guessing
when no baseline exists. The claim is enforced by a counterfactual test that can
fail, not by a comment.

**3. Visible uncertainty.** Every output carries a confidence figure, a named
reason for it, and a set of plausible bands. Missing history raises uncertainty;
it never lowers risk.

**4. It looks again on its own.** A simulation clock re-assesses every waiting
patient on their band's schedule, so deterioration is found by the system going
to look rather than by somebody handing it new data. All three escalations in a
simulated shift are found this way — and the gap between a patient changing and
the system noticing is reported as a number, because it is a consequence of the
department's staffing, not of the model.

## Status

Built in phases. Currently at **Phase 10 — the simulation clock**.

| Phase | | Status |
|---|---|---|
| 1 | Foundations & schema | done |
| 2 | Synthetic patients (24 authored scenarios) | done |
| 3 | Risk engine | done |
| 4 | Age-aware layer | done |
| 5 | Data quality & uncertainty | done |
| 6 | Facial signal module | done |
| 7 | Safety guard | done |
| 8 | Ratchet engine | done |
| 9 | Audit log | done |
| 10 | Simulation clock & re-triage | done |
| 11 | VOI adaptive questions | next |
| 12 | Dashboard | |
| 13 | Nurse workflow | |
| 14 | Surge mode | |
| 15 | Test suite | |
| 16 | Docs & privacy | |
| 17 | Demo mode | |

## Running it

Requires Python 3.10 or newer. There are still no third-party dependencies.

```bash
git clone <this-repo>
cd patienttriage-ai
python -m scripts.check_setup
```

### Explore the synthetic patients

```bash
python -m scripts.show_patients              # the full 24-patient roster
python -m scripts.show_patients P014         # the patient who deteriorates while waiting
python -m scripts.show_patients P013         # acid-attack survivor: asymmetric face, not an emergency
python -m scripts.show_patients --coverage   # which Round 2 requirement each patient covers
```

Five patients present with an abnormal-looking face. Only one is an acute
change. That distinction is the point of the project.

### Score them

```bash
python -m scripts.run_triage                 # the ranked queue
python -m scripts.run_triage P016            # one patient: score panel + confidence panel
python -m scripts.run_triage --facial        # the five facial patients side by side
python -m scripts.run_triage --age           # what age-awareness changed, in both directions
python -m scripts.run_triage --confidence    # who we understand least
python -m scripts.run_triage --fairness      # the counterfactual fairness test
python -m scripts.run_triage --ladder P016   # the facial decision path, step by step
python -m scripts.run_triage --provenance    # what a weaker baseline costs
python -m scripts.run_triage --rules         # every safety rule firing on the board
python -m scripts.run_triage --ratchet       # the one-way acuity mechanism
python -m scripts.run_triage --override      # what a nurse de-escalation requires
python -m scripts.run_triage --audit         # the append-only log, and tampering with it
python -m scripts.run_triage --clock         # a whole simulated shift
python -m scripts.run_triage --latency       # how long deterioration goes unseen
python -m scripts.run_triage --timeline P014 # one patient through the clock
python -m scripts.run_triage --stale P002    # confidence decaying while a patient waits
python -m scripts.run_triage --hospital small_ed
```

## How the score is built

The score is not computed and then explained. The score is computed **by**
building the explanation — every scorer appends a labelled contribution, and
the total is exactly their sum:

```
+20  anticoagulated (apixaban) and struck head            [neurological]
+16  reports: confusion                                   [neurological]
+10  heart rate 68 may be blunted by bisoprolol           [circulatory]
 +9  systolic_bp 104 (low, mild)                          [circulatory]
 +8  reports: head strike                                 [general]
────
 70  →  L3 PULL
```

There is no post-hoc attribution layer, so the explanation cannot drift from
what actually happened. Correlated findings are grouped into clinical
**domains** and each domain is capped, because a breathless patient tripping
six respiratory signals has one clinical problem, not six.

## Confidence, and what it does not mean

Every assessment carries a confidence figure built from four named, separately
testable drivers: **completeness**, **agreement** between modalities,
**baseline knowledge**, and **staleness**.

Confidence is a statement about *our information*, not about the patient.

> 40% confidence does not mean "probably fine". It means we are reasoning from
> a thin, stale or contradictory picture, and a human should look sooner.

Two rules are enforced structurally rather than promised:

- The uncertainty engine reads `risk_score` and **cannot write it**. Low
  confidence never moves anyone down the queue.
- The uncertainty interval is **asymmetric** — it reaches four times further up
  than down, because information we do not have can conceal danger but cannot
  manufacture safety.

**On the "Conformal Risk Guard" from Round 1.** Conformal prediction gives a
set with a coverage guarantee, and that guarantee comes from calibration
against real labelled outcomes. We have synthetic patients and no outcomes, so
we do not claim coverage and we do not use the name. What `core/uncertainty.py`
implements is a monotone widening rule: worse input data produces a wider
plausible set, always in the safe direction. The band-set *interface* is
deliberately conformal-shaped, so a calibrated predictor can replace it later
without changing a single consumer.

## The facial module, and the fairness argument

`core/facial.py` asks exactly one question — **has this face changed?** — and
never asks whether a face is normal. It has no concept of a correct face. A
system that scores appearance will, with total consistency, penalise people for
congenital differences, burn scarring, old strokes and surgery, and those
patients arrive in emergency departments more often than average, not less.

Three things make the claim checkable rather than rhetorical:

**Baseline provenance.** Not just *whether* a baseline exists but *where it came
from* — a documented record, a prior encounter, a relative, the patient's own
recollection, or nothing. Each carries a reliability figure.

**A recorded decision path.** Every verdict reports the ladder it climbed:

```
1. droop and asymmetry observed -> is it NEW?
2. baseline available: documented in prior clinical record (reliability 100%)
3. baseline already shows asymmetry (post_stroke)
4. and it is reported unchanged -> chronic, not acute
```

A nurse who disagrees can point at one step. That is not possible with a
probability.

**An executable fairness test.** `fairness_counterfactual()` re-scores a patient
once per possible *cause* of a documented facial difference — congenital, burn,
surgical, old stroke, trauma — changing nothing else, and asserts the facial
points never move. `--fairness` prints the table; the Phase 15 suite runs it
across the roster. It is a test that can go red.

### The direction that matters

**A weak baseline lowers confidence. It never raises the score.**

The obvious alternative — treat an unverifiable baseline as possibly acute and
escalate — sounds cautious and is quietly discriminatory. It escalates hardest
on undocumented patients, who are disproportionately people without regular
care, without records, and without a relative to speak for them. Run
`--provenance` to see the property directly: strip P015's record away one tier
at a time and her score stays at 66 while confidence falls from 94% to 74%.

The correct response to a missing baseline is to say so loudly and go and find
out (Phase 11), not to convert our ignorance into the patient's points.

## The Ratchet Engine

    The engine may RAISE a patient's acuity.
    The engine may NEVER lower it.
    Only a nurse can de-escalate, and only with a reason on the record.

Most triage systems that claim safety bias implement it as a weighting: score a
little higher, set thresholds a little lower, and let the model move a patient
in either direction as its inputs change. That is a thumb on the scale, not
asymmetry, and a confident enough model will still walk a deteriorating patient
back down.

Here the asymmetry is structural. Every automated path through `core/ratchet.py`
computes `final = max(proposed, previous)`. There is no branch, flag or config
value that produces a lower band, and if one ever appears the code raises
`RatchetViolation` rather than quietly complying. `--ratchet` shows the row that
is the whole product claim:

```
      t  proposed  FINAL     author          why
     48  WATCH     WATCH     system_initial  -
     66  LOOK      LOOK      ai_escalation   risk 32: reports: breathlessness
     82  PULL      PULL      ai_escalation   risk 73: reports: chest pain
    100  LOOK      PULL      ratchet_held    engine proposed LOOK; held at PULL
```

`Ratchet.audit_violations()` returns every transition that lowered a band with
no nurse behind it. It returns nothing, and it is the same query a governance
team could run over a log this code did not produce — which is the form the
property is actually useful in.

### De-escalation

One function can lower a band, and it will not run without a nurse identifier,
a reason that survives validation, and acknowledgement of any safety rule
currently holding the floor. `--override` walks the rejections:

```
rejected  (empty)              lowering a band requires a reason on the record
rejected  "ok"                 'ok' is not a reason...
rejected  "clinical judgement" 'clinical judgement' is not a reason...
rejected  "looks better"       reason is 12 characters; policy requires 15
rejected  (a real reason)      band is held by R1_acute_neuro_cluster; an
                               override must acknowledge the rule it is removing
```

Reasons are required in one direction only, and that is not an oversight. An
escalation already carries its justification — the contribution trace, the
confidence panel, the rule firing are all on the record — and making a nurse
type an explanation for the machine's own decision would be theatre. A
de-escalation overrides evidence the system has recorded, so the person
overriding it supplies the missing piece.

The nurse is never blocked from disagreeing with a safety rule. A Phase 7 floor
is a floor for the machine, not for a clinician. What they cannot do is remove
one without being shown what put it there.

### What the ratchet costs

A patient who genuinely improves keeps their old band until a human agrees they
have. In a busy department that means the queue sometimes carries acuity that
reality has moved past.

We think that is the right side to be wrong on: the alternative failure mode is
a machine quietly walking a deteriorating patient back down, which kills people
while this one wastes a nurse's time. But it is a real cost, not a free win, and
a department adopting this should adopt it knowing that.

## The audit log

`Ratchet.audit_violations()` answers "has this system ever lowered a band
without a human" about objects in memory — which answers it only for people
willing to run our code and trust it while they do. That is nobody in a
governance role.

`core/audit.py` writes the same events to JSONL: one JSON object per line,
openable in any text editor, readable by someone who has never seen this
repository. Band transitions, accepted overrides, and **refused** overrides all
go in — a log of outcomes alone would show one clean de-escalation and hide that
it took three attempts, which is a signal about the interface or about a
clinician under pressure.

There is no `update` method and no `delete` method. Not disabled, not private —
the operations do not exist. A correction is a new entry saying a correction was
made, which is how a clinical record works and for the same reason.

### Tamper-evident, not tamper-proof

Each entry hashes the one before it, so editing a line, deleting one, or
reordering two breaks every hash after it and `verify()` names the first
sequence number where the chain fails:

```
someone edits a reason after the fact
  verifies: False
  seq 7: content has been altered since it was written

someone deletes an inconvenient line
  verifies: False
  seq 6: expected 5 (an entry was removed or reordered)
```

The limit matters as much as the property. Anyone who can write this file can
recompute the whole chain and produce a valid log saying whatever they like.
Hash chaining catches casual alteration, a quietly corrected reason, a crash
mid-write. It does not defend against a determined administrator — that needs
the digest anchored somewhere the writer does not control, which is a deployment
decision we are not in a position to make.

### Completeness

`replay_bands()` reconstructs every patient's current acuity from the log alone
and matches the running system exactly. If that ever stopped matching, something
determining a patient's band would be living only in memory — which is the
difference between a record and a diary of selected highlights.

## The simulation clock

Every phase before this one scored a patient at a *moment*, and every demo had
to hand it the moment. `simulation/clock.py` is the component that decides on
its own when to look again — which is the difference between a scoring function
and a triage system.

Three kinds of event, and only three:

| | |
|---|---|
| **arrival** | a patient enters the department and is scored for the first time |
| **trajectory** | the world changes: new vitals, a new symptom, an answered question |
| **reassessment** | the patient's band says they were due a fresh look |

The third one is the phase. The first two are the world happening to us; the
third is the system deciding, on its own schedule, to go and look.

Over one simulated shift the roster produces **242 assessments and 3 band
changes**, and all three escalations are found at a scheduled reassessment —
nobody handed the system new data and asked it to think again.

### A change is not an observation

The first working version of the clock scored the instant a trajectory event
fired. It produced a better-looking demo than the correct version does, and it
was wrong.

A patient's SpO2 falling is not an event the department receives. Nobody is
notified. The number exists in the patient and nowhere else until somebody takes
observations, which in a waiting room happens when a reassessment comes due. A
clock that scores the moment the world changes has quietly given the system a
sensor it does not have — and, worse, made its own reassessment schedule
decorative, because a schedule that can never discover anything is not a
schedule.

So a trajectory event changes the patient's **state** and records that a change
is pending. The next reassessment is what **observes** it. The gap between those
two minutes is detection latency, and it is a property of the hospital's
reassessment policy rather than of anything clever in `core/`:

```
Medium District Hospital  (8 nurses; WATCH every 30 min, LOOK 20, PULL 8)
  P014  changed t=66   seen t=78   (12 min later)
  P014  changed t=82   seen t=98   (16 min later)

Small Rural ED            (3 nurses; WATCH every 45 min, LOOK 30, PULL 10)
  P014  changed t=66   seen t=93   (27 min later)
```

Read P014 carefully, because the rural ED does not get a gentler version of the
same picture. In the district hospital she escalates twice, WATCH → LOOK → PULL,
because a 30-minute interval catches her halfway down. In the rural ED she gets
**no intermediate warning at all**: the first look after arrival is the one that
finds her already at PULL, a two-band jump 27 minutes after the fact.

Nothing about the engine changed between those runs — same weights, same rules,
same patient, same trajectory. The entire difference is a staffing-driven number
in a JSON file, which makes the reassessment interval a **safety parameter**
rather than a scheduling convenience, and makes it visible. That is the argument
for having a clock at all.

### The loop closes on itself

A reassessment interval is a property of the *current* band, and the band is the
output of the assessment the reassessment produces. Because the ratchet means an
automated path can only ever **raise** a band, an automated path can only ever
**shorten** the loop.

A deteriorating patient is looked at more often, and nothing the machine can do
on its own makes it look less often. Neither mechanism has that property alone.

### Waiting does not make a patient sicker

There is no wait-time term anywhere in `core/`, and the clock never passes a wait
duration to the engine. A patient re-scored at minute 200 on the same
observations gets exactly the score they got at minute 20.

What changes is **confidence**: the Phase 5 staleness driver decays as the
observations age, so the queue shows a waiting patient becoming *less certain*
rather than more settled. An overdue reassessment is a flag, never a score
adjustment. A system that quietly escalated people for waiting would produce a
queue that reordered itself by patience and would be indistinguishable from one
that had detected something.

P017 exists in the roster to make that checkable: same waiting room, same clock,
same triggers, no deterioration. She is re-scored seven times and moves nowhere.
**22 of 24 patients do not move at all.** Without her, P014's escalation could be
dismissed as a system that simply escalates everyone who waits long enough.

### What the clock does not model

- It fires every reassessment **exactly when due**, and no real department
  achieves that. The gap between the policy and the practice is most of what
  actually goes wrong in a waiting room; our timeline is the optimistic case, and
  Phase 14 is where that assumption is supposed to get stressed.
- It models the **waiting room**, not the department. Every hospital profile sets
  the CODE interval to 0 minutes, which is a way of writing "this person must not
  be waiting". Those patients leave the reassessment schedule rather than being
  re-scored infinitely often — a loop that took the zero literally would never
  advance the clock again, and would look exactly like thoroughness until the
  process stopped responding.
- **Nothing arrives that was not authored.** There is no arrival generator and no
  random deterioration, so this file cannot tell you anything about throughput.
  It is deterministic: same roster, same timeline, every time, which is what lets
  the Phase 15 tests assert on it.

### One thing this exposes that we have not solved

`--timeline P014` shows her reaching PULL and then being re-scored eighteen more
times with nothing changing, because nobody has come to see her. The clock is
doing exactly what it was asked to and the answer is useless: re-scoring a
patient does not treat them, and a reassessment that keeps returning the same
band is evidence of an **unmet need**, not evidence that things are fine.

The queue has no way to say "this patient has been at PULL for two hours and no
one has arrived". That is a Phase 12 dashboard concern and a Phase 13 workflow
concern, and it is named here rather than left to read as reassurance.

## The safety guard

A weighted score is a good instrument for ranking and a bad one for absolutes.
`core/safety_rules.py` holds eight named clinical patterns, each of which sets a
**floor** on the band independently of the score.

**Rules can only raise a band.** This is not a convention the rules agree to
follow — there is no code path in the file capable of lowering one. The whole
mechanism is `max(score_band, highest_floor)`. A rule that wanted to say "this
patient is less sick than the score suggests" has no way to express it, which is
the correct expressive limit for an automated system. De-escalation belongs to a
nurse with a logged reason (Phase 8).

### Why rules instead of better weights

P011 has an acute facial change, slurred speech and one-sided weakness on a
documented symmetric baseline. He scores 64. CODE starts at 75.

The tempting fix is to raise the facial and speech weights until he crosses.
That fix is wrong, and precisely why is worth stating: those weights are shared
by all 24 patients. Tuning them to force one patient over one line silently
re-ranks the entire board to fix a single case, and the distortion is invisible
because the arithmetic still looks principled. It is overfitting with extra
steps.

So the weights did not move. The score is still 64, and a named rule floors the
pattern at CODE with its own evidence trail.

**The cost, stated plainly:** after Phase 7 the score and the band can disagree.
P011's panel reads `64/100` and `L4 CODE` together. That looks like a bug until
you read the rule underneath it, and we would rather explain it than hide it.

### Restraint is part of the design

Every rule added fires on patients nobody has thought about. `--rules` reports
**8 firings across 8 of 24 patients, 3 of them binding** — five agreed with a
score that had already got there on its own. If the guard fired on most of the
board it would have replaced the ranking engine with a lookup table; if nothing
ever bound, the rules would be decoration. Both failure modes are visible in
that one line.

### Capacity is not the guard's business

The board now shows 5 patients at CODE against 3 resus bays. The guard does not
know that, and should not. A rule that fired less often when the department was
full would be a rule that triages by bed count. Reconciling clinical need
against capacity is a nurse's decision (Phase 13) under explicit surge policy
(Phase 14), made visibly and with a logged reason.

## Layout

```
core/         engine — framework-free, fully testable
simulation/   clock.py — event queue, re-triage schedule, detection latency
app/          Streamlit UI — rendering only, zero logic
data/         synthetic patients, hospital profiles, weights, thresholds
tests/        the safety argument, expressed as code
docs/         architecture · safety · assumptions · privacy · limitations
```

`core/` deliberately imports nothing from `app/`. The engine can be tested,
reasoned about and eventually served over an API without touching the UI.

Every tunable number lives in `data/` behind a visible disclaimer — band
cutoffs in `data/hospitals/`, point values in `data/risk_weights.json`,
vital-sign ranges in `data/clinical_thresholds.json`, confidence weights in
`data/uncertainty_config.json`, baseline reliability in
`data/facial_config.json`, the hard rules in `data/safety_rules.json`, and
override policy in `data/ratchet_config.json`, log settings in
`data/audit_config.json`, and clock horizon and event caps in
`data/simulation_config.json`. Nothing a judge might question is hard-coded in
the engine.

Reassessment intervals are the exception worth naming: they live in
`data/hospitals/`, alongside bed counts and staffing, because that is what they
are a consequence of — and `--latency` shows what changing them costs.

## Both long-standing gaps, now closed

- **P011** is at L4 CODE, floored by `R1_acute_neuro_cluster`. His score is
  unchanged at 64 and no weight was touched.
- **P016** is at L2 LOOK, floored by `R7_unresolved_finding_low_confidence`,
  because a concerning finding we cannot resolve on thin information is not the
  same thing as a patient with nothing wrong. Her score is still 4 and nobody
  pretended otherwise.

R7 fires at a 75% confidence cutoff while P016 sits at 72%, which looks like a
threshold picked to catch her. It is not, and that is checkable: move the cutoff
anywhere from 75% to 100% and she is still the only patient who fires it, and
below 75% nobody does. Confidence is not what selects her — the requirement for
an *unresolved concerning finding* is, and she is the only patient on the board
who has one.

## What this prototype does not claim

It does not claim a miss rate. It does not claim clinical accuracy. It has not
been validated against real patients or real outcomes, and any performance
figure it prints is a property of our own synthetic data generator, nothing
more. The confidence percentage is a claim about the quality of our input data
and is not a probability of any clinical outcome. The facial module does not
diagnose anything: it reports that findings appeared together and that a
baseline did or did not explain them, and it never uses a diagnosis as a
finding. The safety rules are simplified demonstration patterns, not clinical
protocols, and no clinician has reviewed them. The ratchet's override policy is
a demonstration setting; a real department would set it with its clinical
governance lead rather than inherit ours. The audit log holds patient
identifiers and acuity history, which is health information; retention, access
control and lawful basis are Phase 16 and are not settled by anything built so
far. The simulation clock replays 24 authored patients on a punctual schedule;
it has no arrival model, no random deterioration and no missed reassessments, so
its detection-latency figures describe our own scenario file and say nothing
about throughput or about how a real department behaves.
`docs/limitations.md` sets out what real validation would require.
