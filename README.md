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
when no baseline exists.

**3. Visible uncertainty.** Every output carries a confidence figure, a named
reason for it, and a set of plausible bands. Missing history raises uncertainty;
it never lowers risk.

## Status

Built in phases. Currently at **Phase 1 — foundations and schema**.

| Phase | | Status |
|---|---|---|
| 1 | Foundations & schema | done |
| 2 | Synthetic patients (24 authored scenarios) | done |
| 3 | Risk engine | next |
| 4 | Age-aware layer | |
| 5 | Data quality & uncertainty | |
| 6 | Facial signal module | |
| 7 | Safety guard | |
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

Requires Python 3.10 or newer. Phase 1 has no third-party dependencies.

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

## Layout

```
core/         engine — framework-free, fully testable
simulation/   clock, arrivals, deterioration, surge
app/          Streamlit UI — rendering only, zero logic
data/         synthetic patients and hospital profiles
tests/        the safety argument, expressed as code
docs/         architecture · safety · assumptions · privacy · limitations
```

`core/` deliberately imports nothing from `app/`. The engine can be tested,
reasoned about and eventually served over an API without touching the UI.

## What this prototype does not claim

It does not claim a miss rate. It does not claim clinical accuracy. It has not
been validated against real patients or real outcomes, and any performance
figure it prints is a property of our own synthetic data generator, nothing
more. `docs/limitations.md` sets out what real validation would require.
