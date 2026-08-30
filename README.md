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

**5. It shows unmet need as its own axis.** The board ranks who is sickest, who
has waited past their target, and who we are least sure about as three separate
lists rather than one blended score. Waiting time is displayed and never scored.

**6. Only a person can close a need.** Nothing in the system can mark a patient
as seen — there is no automated path to it anywhere. The panel that says the
department is not keeping up cannot be cleared by the thing being measured.

**7. It degrades honestly.** Under load the department cannot re-check everyone,
so reassessments are deferred — never dropped, and never by relaxing anyone's
band. Capacity constrains how often we look, not how sick we judge someone to be,
and the invariant is asserted in code rather than promised.

**8. Its claims can go red.** Every safety property above is a test organised by
claim, and the central ones carry a companion test that breaks the invariant
deliberately to prove the check works. Two known gaps are pinned rather than
deleted so they cannot be quietly forgotten.

**9. A person can be erased from it.** The audit log carries pseudonyms only,
and the mapping to a real person lives in the one component with a delete
operation. Erasure destroys that mapping while the hash chain stays intact — and
we say plainly that what remains is pseudonymous, not anonymous.

**10. It knows what to ask.** Rather than reporting a gap, the system prices
every question it could ask by re-running the whole pipeline on each possible
answer, and ranks them by whether the answer could change the band — not by how
much they would raise confidence. Asking cannot lower anyone's acuity, and a
patient who cannot answer is routed to collateral sources rather than skipped.

## Status

Built in phases. Currently at **Phase 16 — docs and privacy**.

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
| 11 | VOI adaptive questions | done |
| 12 | Dashboard | done |
| 13 | Nurse workflow | done |
| 14 | Surge mode | done |
| 15 | Test suite | done |
| 16 | Docs & privacy | done |
| 17 | Demo mode | next |

## Live intake — camera, microphone, voice

```bash
python -m scripts.run_intake
```

Opens a console at `http://127.0.0.1:8770/`. Point a camera at a face, speak
the symptoms, and the real engine scores them: the same risk engine, the same
uncertainty model, the same safety rules and the same ratchet that
`scripts/run_triage.py` uses. The page holds no clinical logic and no
thresholds — it captures signals, an operator confirms them, and the flags are
posted to the engine.

Chrome or Edge for speech recognition; every other channel works anywhere, and
typing is always available. Nothing captured is written to disk.

The point of the live demo is not the detector. Capture the same asymmetric
face three times and answer the baseline question differently each time:

| Baseline answer | Outcome |
| --- | --- |
| new | **L4 CODE** — a hard rule floors it, above what the score alone gives |
| normal for them | **L1 WATCH** — triaged for what they actually came in with |
| cannot say | **L2 LOOK**, confidence ~60%, uncertainty drivers named |

Same pixels, three answers. A better detector changes none of it. See
`docs/demo.md` for the four-minute runbook.

## Running it

Requires Python 3.10 or newer. There are still no third-party dependencies.

```bash
git clone <this-repo>
cd patienttriage-ai
python -m scripts.check_setup
python -m scripts.run_tests                  # the safety argument, run as code
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
python -m scripts.run_triage --questions     # the next question, for everyone
python -m scripts.run_triage --ask P019      # what turns on a single answer
python -m scripts.run_triage --board         # the department board, in the terminal
python -m scripts.run_triage --workflow      # a nurse working the board
python -m scripts.run_triage --surge         # what breaks under 3x load
python -m scripts.run_triage --privacy       # erasure against an append-only log
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

## Asking the right question

Since Phase 5 every assessment has carried a confidence figure with a named
dominant driver. P016 sat at 18% baseline knowledge for six phases while the
system said, very honestly and completely uselessly, that it could not tell
whether her face had changed. **Naming a gap is not closing one.**

`core/questions.py` closes them by answering one question about the questions:
of everything we could ask, which one is worth asking?

### Value is measured in decisions, not in information

The obvious ranking is by information gained — ask whatever raises confidence
most. It is the wrong objective. A question that takes a patient from 72% to 95%
and leaves them in the same band has tidied our records; it has not changed
anything about their care.

So candidates are ranked on **band movement** first, confidence second, and cost
only as a tie-breaker that can demote a question but never rescue one that
changes no decision.

`--questions` shows the check on that:

```
P004  WATCH     86%    0.75  LOOK        Any pain, pressure or tight...
P013  WATCH     95%    0.94  PULL        Did the headache come on su...
P016  LOOK      72%    0.28  review only Is this facial asymmetry ne...
P019  WATCH     88%    0.94  PULL        Did the headache come on su...

6 of 24 patients have a question worth asking.
```

P024 holds the **lowest confidence on the board at 64%** and the questioner has
nothing to ask him, because he is already at CODE and no answer can move him
higher. An information-maximising questioner would have gone straight to the
sickest patient in the department to improve its records.

### How the value is computed

By counterfactual, not by a model. Each possible answer is applied to a copy of
the patient and the **entire pipeline is re-run** — score, age rules, facial
module, uncertainty, safety rules. The value of the question is the spread of
outcomes that come back. Slow, and completely transparent: the answer to "why
did it ask that?" is a table showing what each answer would have done.

```
Did the headache come on suddenly, and is this the worst you have ever had?
  addresses completeness; ask patient; about 15s
    "sudden, and the worst ever"  risk  55  conf  88%  PULL   ESCALATES to PULL
    "gradual, and not the worst"  risk   7  conf  88%  WATCH  (record unchanged)
```

### Direction is priced

An answer that could reveal a patient is sicker changes what happens to them
today. An answer that could only propose a **lower** band changes nothing on its
own — the ratchet holds a waiting patient where they are — so its worth is in
handing a nurse documented grounds for a review they would otherwise have no
basis for. Real, and priced at a fraction.

That is also the honest answer to what the questioner does for P016: it cannot
move her, it can only let somebody else move her. Ranking the two directions
equally would let the questioner spend its one cheap question confirming that
people are fine, which is precisely the instinct a safety-biased system should
not have.

Asking can never lower a band, and that falls out of the design rather than
being enforced here: every answer produces a fresh assessment that goes through
the ratchet like any other. **A non-answer is not a no** — "cannot say" is a real
answer with an empty effect set that keeps the uncertainty and never reduces
risk. A questioner that treated silence as reassurance would be strictly worse
than not asking.

### Who can answer

A question aimed at a patient who cannot communicate is worth nothing however
high it scores, so `answerable_by` is checked *before* value is computed and
those patients are routed to collateral and record sources instead. This is not
politeness: an unconscious patient, an infant and a person who does not share a
language with the nurse are three of the highest-risk groups in any emergency
department, and a questioner that quietly returned an empty list for all of them
would fail exactly where it is needed most.

### This is not expected value of information

Textbook VOI weights each outcome by the probability of that answer. We do not
have those probabilities. Nothing here is calibrated against real patients, so
any prior over how a patient is likely to answer would be invented — and it
would be the invented number driving the entire ranking, which is the worst
possible place to hide one.

So we compute the **range** of outcomes rather than their expectation: a question
is valuable if *some* answer changes the band. That is a possibility measure,
deliberately biased toward asking, and it will sometimes rank a question highly
because of an answer the patient was never likely to give. We would rather
over-ask than silently weight a life-changing answer down to nothing on a prior
we made up. The interface is expectation-shaped, so a calibrated answer model
drops in later without changing a consumer.

### What a question is worth, in minutes

P019 asked at intake reaches PULL at t=68. Left to the clock, the same fact
arrives on its own at t=74 and is found at the next scheduled look:

```
asked at intake       t=68    ->  PULL at t=68
discovered by clock   t=74    ->  PULL at t=98
```

**Thirty minutes, for a fifteen-second question** — and only measurable because
Phase 10 built the thing that measures the alternative.

### One earlier claim retracted

Phase 5 said in as many words that the largest confidence penalty would be the
best question to ask next. P019 is the counterexample: her dominant driver is
`agreement`, and the only question that can move her band addresses
`completeness`. The docstring in `core/schema.py` has been corrected rather than
left to age quietly.

Uncertainty tells you where our picture is thin. It does not tell you where a
*decision* is fragile, and those are different places.

### A problem with our own data

Two of the ten questions in the bank never fire on this roster. They are not
broken — they never fire because our synthetic patients **arrive volunteering
everything**. P006 states unprompted that she struck her head; P005's parent has
already reported poor feeding. Real patients answer the question they were asked
and no more.

This roster therefore systematically *understates* what a questioner is worth,
and the honest version of the Phase 2 data would author patients with things
they have not mentioned yet.

## The board

```bash
python -m scripts.build_dashboard          # writes board.html
python -m scripts.build_dashboard --worked # after a nurse has worked it
python -m scripts.run_triage --board       # the same thing, in the terminal
```

One self-contained HTML file. No CDN, no fonts, no scripts, no server — it
opens by double-clicking, on any machine, offline.

### Three lists, deliberately not one number

A triage dashboard that only ranks by acuity answers one question: who is
sickest? That is the question the engine is for, and it is not the only one a
charge nurse needs answered. The board shows three lists side by side:

| | |
|---|---|
| **The queue** | Who is sickest. Band first, score within the band. |
| **Waiting past target** | Who has been waiting longer than their band promised. |
| **Who we might be wrong about** | Ranked by confidence, lowest first. |

They are separate panels rather than a blended priority score on purpose. A
single ranking that mixed acuity, uncertainty and waiting time would be making a
clinical trade-off silently, on weights nobody agreed, and would be impossible to
argue with. **Three lists a nurse can read against each other beat one number
they have to trust.**

### The unmet-need axis

Phase 11 handed this phase a problem: `--timeline P014` showed her reaching PULL
and then being re-scored eighteen times with nobody coming to see her. The clock
was doing exactly what it was asked and the answer was useless.

```
P006  PULL   waited  222 min   target  10   OVER BY 212
P014  PULL   waited  192 min   target  10   OVER BY 182
P016  LOOK   waited  184 min   target  60   OVER BY 124
```

Re-scoring a patient is not treating them. A reassessment that keeps returning
the same band is evidence of an **unmet need**, not evidence that things are
fine.

Time-to-clinician targets live in `data/hospitals/` next to staffing, and
**waiting time is displayed and never scored**. `core/config.overdue_by()`
returns minutes; the only caller is `app/view_model.py`. No engine reads it,
which is consistent with the Phase 10 rule — a queue that escalated people for
waiting would reorder itself by patience and be indistinguishable from one that
had detected something.

### The question queue is capped

The board shows **three** questions and says how many it withheld. An adaptive
questioner with a screen in front of a nurse becomes an interrogation script by
default: it always has one more reasonable-looking thing it would like to know,
and the list grows until it is ignored wholesale. Showing three means three get
read — and the cap is only defensible because the ranking underneath it is.

### The ratchet's price, counted

Phase 8 described the cost of the one-way mechanism in prose: the queue
sometimes carries acuity that reality has moved past. The board counts it, and
names the patients. A department adopting the ratchet should be able to see its
price on a screen rather than read about it in a design document.

### Zero logic in the UI, and it is checkable

`app/view_model.py` **assembles**: it collects assessments, timelines and
question values that `core/` and `simulation/` already produced, then sorts,
groups and labels them. It computes no clinical quantity. `app/dashboard.py`
formats strings — the four `explain_*` functions are passed *in* rather than
imported, so the renderer has no route to `core/` at all and cannot accidentally
start reasoning about a patient.

Nothing is re-assessed to draw the board. Each card carries the last assessment
the clock actually produced — the one that went through the ratchet, fired the
safety rules and was written to the audit log. That distinction is not cosmetic:
assessing the roster fresh at minute 240 scores everyone on the state they
*arrived* in, and P014 renders as WATCH. That is exactly the snapshot behaviour
this project argues against, and it would have shipped on a dashboard that looked
completely correct. `--intake` renders that view deliberately, for comparison.

### Why not Streamlit

`streamlit` and `pandas` have been commented out in `requirements.txt` since
Phase 1, waiting for this phase. They stay commented out.

- The repository still has **zero third-party dependencies**, which is a real
  property of a prototype somebody may have to run on an unfamiliar machine.
- A demo needing a server and a reachable package index is a demo that can fail
  in the room. This produces a file you can commit, email and attach.
- A generated file can be diffed, checked and regenerated deterministically. A
  Streamlit app can only be verified by running it and looking.

None of that argues against Streamlit for a product, and the architecture is
indifferent: the boundary is `app/view_model.py`, and a Streamlit front end would
consume exactly the same `BoardView`.

### Colour is never the only signal

Every band carries its word, every overdue patient carries its minutes, and the
tables read correctly in monochrome, on a printout, and to a screen reader. A
board where the difference between CODE and PULL is a hue fails the first
colour-blind nurse who uses it.

## The nurse workflow

Phase 12 built a board that reports. Everything on it was read-only — the
overdue list had no way to shrink and the questions had nowhere to send an
answer. `core/workflow.py` adds the four things a clinician can do, and nothing
else:

| | |
|---|---|
| `mark_seen` | a clinician made contact with this patient |
| `answer_question` | somebody answered one of the questions we were asking |
| `unable_to_answer` | somebody tried, and could not get an answer |
| `override` | a nurse changes the band (Phase 8, unchanged) |

Four verbs is not a small API by accident. Every additional one is a new way for
the record to disagree with what happened.

### Nothing in this system can mark a patient as seen

No automated path, no default, no batch operation, no config flag. Grep for
`PATIENT_SEEN`: it is written in exactly one place, by a person, under their own
identifier.

That restriction is the phase. "Waiting past target" is the panel that says the
department is not keeping up, and **a system able to clear its own overdue list
could make that panel look healthy without anybody being treated**. An engine
that can improve its own reported metrics will eventually be tuned to do so,
whether or not anyone sets out to cheat.

Same reasoning as the ratchet, pointed at a different failure: there the machine
must not lower acuity, here it must not close a need.

### Seen is not treated

`mark_seen` records that a clinician made contact. It says nothing about whether
anything was done, whether the patient improved, or whether they still need a
bed. **A department can reach total compliance with a time-to-clinician target by
having somebody walk past every patient in the waiting room**, and target-driven
systems reliably discover exactly that.

We record contact because it is the only thing we can honestly observe from
here. `time_to_seen()` is labelled as what it is, the board says so on the panel,
and we do not call it a quality measure. Naming the weakness is worth more than a
stronger-sounding metric we cannot support.

Being seen does not take a patient off the reassessment schedule. They stay on
the board and the clock keeps looking at them — treating "a nurse looked at them
once" as "somebody else's problem now" is the exact failure this project is named
after.

### An answer still cannot lower a band

P016 is the proof, and it is the case six phases have been building toward. A
nurse asks the question, gets an answer, the concerning finding resolves, and the
engine proposes WATCH:

```
engine proposes : WATCH
FINAL           : LOOK   (ratchet held)
```

The good news we went looking for does not get to move her either. An answer
produces a fresh assessment that goes through the ratchet like any other, so it
can raise a band and has no mechanism to lower one. What it does instead is hand
a nurse documented grounds to de-escalate her deliberately, under their own name
— which is exactly what Phase 11 priced the question at.

The workflow also does not trust Phase 11's prediction of what the answer would
do. It applies the real answer and runs the whole pipeline again; reusing the
earlier figure would let the board show a band that no assessment ever produced.

### An answer is not an observation

Phase 10 drew a line between the world changing and the system noticing. This
phase draws the matching one: an answer is evidence a **person** gave us, so it
carries their identifier, whereas a trajectory update is the world moving whether
anyone is watching. Merging them would make it impossible to tell what we were
told from what we observed, which is what a retrospective review turns on.

`unable_to_answer` is a real outcome with no effect on the record. It keeps the
uncertainty, keeps the question available, and resolves nothing. A workflow that
only accepted answers would push a clinician under time pressure toward guessing
on the patient's behalf, and a guess entered as an answer is worse than a gap —
because a gap is visible.

### The log reads causally

The nurse's answer is written **before** the band transition it caused:

```
#246  t=241  P013   RN-2210 answered "sudden, and the worst ever"
#247  t=241  P013   WATCH -> PULL    ai_escalation
                    "risk 59: reports: thunderclap onset"
```

The first version of this logged the assessment first, and the trail read as
though the engine had decided something and a human agreed afterwards — the
reverse of the truth, and invisible unless you went looking for it.

### What the workflow deliberately does not do

It never says who to see next. The board presents three lists precisely because
a single blended ranking would hide a clinical trade-off inside weights nobody
agreed, and a workflow layer answering "who next?" would collapse them straight
back into that number. The nurse chooses. We record what they chose.

## Surge

Phase 10 shipped a clock that fires every reassessment exactly when it falls
due, and said so in its own docstring: no real department achieves that. Every
number this project has produced since — detection latency, the overdue panel,
the escalations found on schedule — has been the optimistic case.

`simulation/surge.py` removes the assumption. Reassessments cost a nurse's time,
there is a finite amount of it, and when demand exceeds supply something gives.

```
           patients  re-checks  deferred   late  found  MISSED
normal           24        242         0      0      5       0
surge x3         72        353      1156     57      8       7
```

### Capacity constrains observation, never acuity

The tempting design is to relax the band thresholds under load: score a little
harder, so fewer patients come out as PULL when there are no PULL beds. It would
calm the board immediately. It would also make the department look better while
making the patients no safer, and the distortion would be invisible because the
arithmetic still looks principled.

So what degrades is **how often we can look**. How sick we judge someone does not
move by a point. Five things are off limits — band thresholds, safety rules, the
ratchet, time-to-clinician targets, audit logging — and
`SurgeController.assert_invariants()` compares them against a snapshot taken
before any load was applied and raises if any of them moved. Checked, not listed.

This was settled in Phase 1 without anyone noticing. `data/hospitals/large_ed.json`
has carried the line *"Band cutoffs are IDENTICAL across all three profiles by
design. Capacity changes how often we look at a patient; it never changes how
sick we judge them to be"* since the first commit. This phase turns that sentence
into a mechanism.

The care targets are the uncomfortable item on that list. Under surge the overdue
panel goes red and stays red — which is correct, because a target that relaxes
when the department is busy reports how busy we are willing to admit we are
rather than how long patients waited.

### Deferred, never dropped

A reassessment that cannot happen now goes back in the queue and asks again. It
is never cancelled. A deferred patient is one somebody will get to; a dropped one
is a patient nobody looks at again — and a policy that quietly discarded its
backlog would report a deferral count of zero and mean nothing by it. At 3x load
the count is 80%, and it is supposed to be ugly.

### The measurement that changed the design

Reserving capacity for the sickest patients is the obviously correct policy, and
on its own it is dangerous. Sweeping the anti-starvation threshold — the point at
which a long-deferred patient may spend reserved capacity — shows why:

```
 starve at  WATCH defer  PULL defer  found  MISSED
     never         49%         81%     14       1
    10 min         91%         50%      8       7
    25 min         97%         31%      6       9
    90 min         98%         20%      1      14
```

With a hard reserve the department protects the patients it already knows are
sick, spends the whole budget re-checking bands that never move, and misses
fourteen of fifteen deteriorations. **P014 — the patient this entire project was
built around — is never looked at again.** With no reserve it finds almost every
deterioration and neglects the patients it knows are sick.

So the reserve now decays with lateness: acuity goes first, but nobody is
forgotten.

> Rationing observation by **current acuity** is rationing by what we already
> know, and observation exists to find out what we do not.

That is the same mistake Phase 11 refused to make when it declined to rank
questions by confidence gained. Value lives in what might change, not in what is
already settled.

### There is no safe setting

The sweep is monotone: every value trades one failure for the other. That is not
a defect in the mechanism — it is what being three times oversubscribed actually
costs. Our default of 10 minutes is a compromise for this roster and **is not a
recommendation**. The system's job is to make the choice explicit, set in advance
by a clinical governance lead and logged, rather than discovered at 2am when the
waiting room is full.

### What the surge roster is, and is not

There is no arrival generator here and there never has been. Load is created by
**replicating the authored roster**, with copies labelled as copies (`P014-b`,
`P014-c`). That makes it a fair test of what happens when demand triples and
useless as a statement about case mix — a real surge is not three of every
patient.

Each copy keeps its own internal timing, so a copy of P014 deteriorates at
P014's rate rather than three times faster. Compressing a patient's physiology to
simulate a busy department would be modelling a different disease, not a
different workload.

One assumption is doing real work in every capacity figure above: **three minutes
per reassessment**. It is fast because the system takes part of it — sensors
capture, the engine re-scores, the nurse confirms. A department doing this
manually would be nearer ten minutes and would have a third of the capacity
modelled here. We cannot validate three minutes and nobody should treat it as
measured.

## The safety argument, run as code

```bash
python -m scripts.run_tests          # 52 checks, grouped by claim
python -m scripts.run_tests --gaps   # only the known gaps
```

Plain `unittest` from the standard library — **requirements.txt still lists
nothing**, so this runs on a clean machine with no network. `pytest` discovers
`tests/` unchanged if you prefer it.

### Organised by claim, not by module

A conventional suite mirrors the source tree: `test_ratchet.py` tests
`core/ratchet.py`. That is the right shape when the question is *did I break the
code?* It is the wrong shape here, because the question this project has to
answer is *is the claim true?* — and the claims do not live inside single
modules. "Missing data never lowers risk" is enforced by the loader, the
uncertainty engine, the facial module and the safety guard acting together, and
a test of any one of them would pass while the property was broken.

So each file is a claim, each test is named after the sentence in the README it
defends, and the runner prints them as claims:

```
CLAIM  Nothing in this system can mark a patient as seen. Only a person can.
  ok    a full shift marks nobody seen
  ok    marking seen requires an identifier
  ok    an answer can raise a band and cannot lower one
  ok    the same patient cannot be closed twice
```

### A test that cannot fail is not evidence

Several properties here are enforced by something **not existing** — the ratchet
has no branch that lowers a band, `AuditLog` has no `update` method, nothing can
mark a patient seen. Asserting that an impossible thing did not happen passes on
an empty function.

So the central claims carry a companion test that plants a violation and confirms
the check catches it. Those are marked `[has teeth]`. Sabotaging the ratchet with
a one-line change turns three tests red across two different claim groups and
flips the verdict.

### Writing the suite made a vague claim precise

The first draft of "missing data never lowers risk" asserted that deleting a
measured value can never lower the score. That is **false, and it should be**:
points are earned by evidence, and a patient cannot be charged for a fever nobody
measured. Demanding otherwise would require the engine to retain points for
findings it does not have.

What must hold is narrower and is the thing that actually protects people: an
absent value contributes *nothing* — it is never scored as though it had been
measured and found normal. The README claim now says that, because the test
forced us to say what we meant.

### Two known gaps, pinned rather than deleted

A suite containing only things that pass is a suite that has stopped looking. Two
properties this project implies and does not currently hold are marked
`expectedFailure`, so they appear in every run and the suite reports it if one is
ever fixed:

**Removing information can raise confidence.** Found by this suite. The agreement
driver computes its split over the modalities that *spoke*, so silencing a
dissenting one removes a disagreement — 13 of 210 single-item deletions across the
roster increase confidence. Not measuring the thing that disagrees should never be
a way to look more certain. The fix is a pessimistic split (assume a silent
modality takes whichever side makes the disagreement worst — this project's own
"unknown never becomes no" rule applied to the one driver that breaks it). It is
not shipped in the same commit as the suite that found it, because that would mean
changing every confidence figure in the repository and the thing that checks them
at the same time.

**The acute-on-chronic facial case is not in the roster.** Open since Phase 6. A
patient with documented asymmetry whose family says it got worse *today* is
arguably the hardest facial case in practice; the module handles it correctly and
no authored patient exercises it.

### What a green run does not mean

It is not clinical validation. These tests confirm the system behaves the way this
repository says it does. They say nothing about whether that behaviour is
clinically correct, and every threshold they assert against is a simulated
demonstration value.

## Privacy, and the conflict at the centre of it

Phase 9 built an append-only, hash-chained log with **no update method and no
delete method** — not disabled, not private, absent — and argued that the
absence of those operations is the entire value of the artefact.

Data protection law argues the opposite. The **DPDP Act 2023** and the **GDPR**
both give a person the right to have their data erased. These cannot both be
satisfied by deleting log lines, and pretending otherwise is how a prototype
becomes an unlawful product.

### How it resolves

The log never holds a direct identifier. It holds a pseudonym, and the mapping
to a real person lives in exactly one place — `IdentityVault` in
`core/privacy.py`, the only class in this repository with a `forget()` method.

```
P014 has 20 entries in the log. The vault can name her.
chain verifies: True

vault.forget('P014')
vault can name her : False
entries remaining  : 20
chain verifies     : True
replay still works : True
```

Nothing was deleted and nothing needed to be. This is the standard resolution
and it is **not a loophole** — it is why the log was designed to carry
pseudonyms from Phase 9 rather than a fix retrofitted once the problem became
inconvenient. That no direct identifier exists anywhere is asserted in
`tests/test_privacy.py`, including a planted identifier that proves the scanner
works.

### Pseudonymisation is not anonymisation

The most over-claimed thing in health data engineering, so it gets said plainly.
What remains after erasure is an age, an arrival time, vital signs, conditions
and an acuity timeline at **minute resolution**. For an unusual presentation in
a small department on a known date, that is re-identifiable by anybody who was
on shift — and is very likely still personal data.

`reidentification_risk()` is a method living next to `forget()` so the claim and
its qualification cannot drift apart.

### What this phase did not build

Named as **open**, not as future work with a tick beside it:

- No access control, no encryption at rest, no DPIA
- No digest anchoring — the log stays tamper-evident, not tamper-proof
- **No audit of who *read* a record.** We log every write and not one read, and
  unauthorised reading is what actually gets abused in hospital systems. This is
  the largest gap.
- Retention periods are documented and **enforced by nothing** —
  `RetentionPolicy.enforced()` returns `False` and is a method so nothing can
  quietly assume otherwise.
- **Lawful basis: NOT ESTABLISHED**, deliberately rather than filled with a
  plausible-sounding value. A test asserts the status string, so filling it in
  requires deleting a test — a conversation rather than an edit.

## Documentation

The `docs/` line in the layout block was aspirational until this phase.

| | |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | the pipeline, module boundaries, and a table of what each stage may do to a band |
| [`docs/safety.md`](docs/safety.md) | the five safety properties and where each is checked |
| [`docs/assumptions.md`](docs/assumptions.md) | every simulated number in one place, plus the **ESI cross-walk** |
| [`docs/privacy.md`](docs/privacy.md) | the erasure resolution and the open gaps |
| [`docs/limitations.md`](docs/limitations.md) | what real validation would require |

The ESI cross-walk matters more than it sounds: **our L4 CODE is ESI 1** — the
numbering runs the opposite way to the standard scale. That is why every surface
in this system leads with the word (CODE / PULL / LOOK / WATCH) and shows the
number second. It is the one place where a display convention is a safety
decision.

`docs/limitations.md` is longer than the list of things this system does well,
which is the correct ratio for a prototype nobody has validated.

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
              surge.py — nurse-time budget, deferral policy, load testing
app/          view_model.py + dashboard.py — rendering only, zero logic
data/         synthetic patients, hospital profiles, weights, thresholds
tests/        the safety argument, expressed as code — organised by claim
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
`data/audit_config.json`, clock horizon and event caps in
`data/simulation_config.json`, and the question bank and its ranking weights in
`data/questions.json`, and clinician action policy in
`data/workflow_config.json`, and capacity and deferral policy in
`data/surge_config.json`, and retention and export policy in
`data/privacy_config.json`. Nothing a judge might question is hard-coded in the
engine — `docs/assumptions.md` lists every one of them in a single table.

Reassessment intervals and time-to-clinician targets both live in
`data/hospitals/`, alongside bed counts and staffing, because that is what they
are a consequence of.

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
about throughput or about how a real department behaves. The question bank is
ten illustrative prompts that no clinician has reviewed and is not a screening
instrument; its value figures are a possibility measure over our own synthetic
answers, not a calibrated expected value of information. The time-to-clinician
targets on the board are simulated demonstration values that no department has
agreed, and the board reports need rather than allocating anything: it does not
assign staff, reserve beds or decide who is seen next. Marking a patient seen
records clinical contact and nothing more: it is not a measure of care quality,
and a department optimising it could score perfectly without treating anybody.
The surge figures come from replicating our own 24 patients, which is a load test
and not a case mix, and they rest on an unvalidated three-minute reassessment
cost; the deferral policy is a demonstration setting with no safe value, not a
surge escalation protocol. No lawful basis has been established, retention is
documented and enforced by nothing, and there is no access control, no
encryption at rest and no auditing of who reads a record.
`docs/limitations.md` sets out what real validation would require.
