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
asymmetry is a mechanism here, not a slogan.

**2. Baseline-aware facial reasoning.** A patient with congenital asymmetry,
acid-attack scarring or chronic post-stroke weakness must not be flagged as an
emergency because their face is unusual. We separate *what a patient looks like*
from *what has changed acutely*, and we return `UNKNOWN` rather than guessing
when no baseline exists. The claim is enforced by a counterfactual test that can
fail, not by a comment.

**3. Visible uncertainty.** Every output carries a confidence figure, a named
reason for it, and a set of plausible bands. Missing history raises uncertainty;
it never lowers risk.

## Status

Built in phases. Currently at **Phase 7 — the safety guard**.

| Phase | | Status |
|---|---|---|
| 1 | Foundations & schema | done |
| 2 | Synthetic patients (24 authored scenarios) | done |
| 3 | Risk engine | done |
| 4 | Age-aware layer | done |
| 5 | Data quality & uncertainty | done |
| 6 | Facial signal module | done |
| 7 | Safety guard | done |
| 8 | Ratchet engine | next |
| 9 | Audit log | |
| 10 | Simulation clock & re-triage | |
| 11 | VOI adaptive questions | |
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
simulation/   clock, arrivals, deterioration, surge
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
`data/facial_config.json`, and the hard rules in `data/safety_rules.json`. Nothing a judge might question is hard-coded in
the engine.

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
protocols, and no clinician has reviewed them. `docs/limitations.md` sets
out what real validation would require.
