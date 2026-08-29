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

Built in phases. Currently at **Phase 6 — the facial signal module**.

| Phase | | Status |
|---|---|---|
| 1 | Foundations & schema | done |
| 2 | Synthetic patients (24 authored scenarios) | done |
| 3 | Risk engine | done |
| 4 | Age-aware layer | done |
| 5 | Data quality & uncertainty | done |
| 6 | Facial signal module | done |
| 7 | Safety guard | next |
| 8 | Ratchet engine | |
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
`data/uncertainty_config.json`, and baseline reliability in
`data/facial_config.json`. Nothing a judge might question is hard-coded in
the engine.

## Known gaps, left visible on purpose

- **P011**, an acute stroke, scores L3 rather than L4. The domain cap that
  prevents double-counting also prevents a real emergency from reaching CODE on
  score alone. The fix is not to inflate weights until it happens to work; it is
  a hard clinical rule in Phase 7 that floors a stroke cluster at L4 regardless
  of score. The scoring model is not meant to be the final authority.
- **P016**, facial asymmetry with no baseline anywhere, is now labelled honestly
  — 18% baseline knowledge, plausible bands WATCH/LOOK, and a decision path
  whose third step reads *refusing to guess in either direction*. Nothing yet
  *acts* on it. Phase 7 turns low confidence plus a concerning finding into
  escalation.

## What this prototype does not claim

It does not claim a miss rate. It does not claim clinical accuracy. It has not
been validated against real patients or real outcomes, and any performance
figure it prints is a property of our own synthetic data generator, nothing
more. The confidence percentage is a claim about the quality of our input data
and is not a probability of any clinical outcome. The facial module does not
diagnose anything: it reports that findings appeared together and that a
baseline did or did not explain them, and it never uses a diagnosis as a
finding. `docs/limitations.md` sets
out what real validation would require.
