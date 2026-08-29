# Architecture

## The pipeline

One patient state in, one `Assessment` out. Every stage appends to the same
object and each can only do one thing to the band.

```
Patient state
     │
     ├─ score vitals · symptoms · pain · observed · voice · facial
     ├─ age context rules            core/age_rules.py
     ├─ conflict detection           core/risk_engine.py
     │
     ├─ domain caps ─────────────────► risk_score = sum(contributions)
     │
     ├─ uncertainty                  core/uncertainty.py   reads score, cannot write
     ├─ safety guard                 core/safety_rules.py  can raise a band only
     ├─ ratchet                      core/ratchet.py       max(proposed, previous)
     │
     └─► Assessment ──► app/view_model.py ──► app/dashboard.py
```

The order is load-bearing. Uncertainty runs after the score is final and
asserts it did not change; the guard runs after uncertainty because one rule
needs the confidence figure; the ratchet runs last because it is the only stage
allowed to disagree with everything before it.

## What each stage may do to the band

This table is the safety argument in one place.

| Stage | Raise | Lower |
|---|---|---|
| Risk engine | via score | via score |
| Uncertainty | no — widens the band *set* only | **no mechanism** |
| Safety guard | yes, sets a floor | **no code path** |
| Ratchet (automated) | yes | **no code path** |
| Nurse override | yes | yes — with ID, reason, acknowledgement |
| Surge / capacity | no | **no code path** |
| Workflow (`mark_seen`) | no | no |

Everything in bold is enforced by the *absence* of a branch rather than by a
convention, and each has a test that plants a violation to prove the check
works.

## Module boundaries

```
core/         the engine — framework-free, no I/O beyond data/, fully testable
simulation/   clock.py    event queue, re-triage schedule, detection latency
              surge.py    nurse-time budget, deferral policy, load testing
app/          view_model.py  arrangement only — no clinical computation
              dashboard.py   string formatting only
data/         every tunable number, behind a disclaimer
tests/        the safety argument, organised by claim
docs/         this
scripts/      entry points
```

**`core/` imports nothing from `app/`.** The engine can be tested, reasoned
about and served over an API without touching the UI, and a bug in the
dashboard cannot become a bug in a patient's acuity.

`app/dashboard.py` is handed its four `explain_*` functions rather than
importing them, so the renderer has no route to `core/` at all — it cannot
accidentally start reasoning about a patient because it cannot reach anything
that reasons.

## Statefulness

Almost everything is stateless. Three exceptions, each deliberate:

- **`Ratchet`** remembers each patient's current band. A stateless ratchet is a
  contradiction in terms — remembering is its entire job.
- **`SimulationClock`** holds the event queue and each patient's current state.
- **`Workflow`** remembers who has been seen.

The risk engine is stateless by contract, and a test asserts it does not mutate
the patient it is given. Anything that changes over time arrives as a *new*
patient state from the simulation, never as hidden memory inside the engine.
That is what makes it testable.

## The explanation is the calculation

The score is not computed and then explained. It is computed **by** building the
explanation: every scorer appends a labelled `Contribution`, and the total is
exactly their sum.

There is no post-hoc attribution layer, so the explanation cannot drift from
what happened — and a test asserts the panel sums to the score for every patient
on the board. This is the main reason a transparent weighted engine was chosen
over a trained model.

## Data flow over a shift

```
arrival ──► assess ──► ratchet ──► audit log
                │
                └──► schedule reassessment at band's interval
                          │
      world changes ──────┤ (pending, unobserved)
                          ▼
                     reassessment ──► capacity? ──► defer ──┐
                          │                                  │
                          └──► observe pending ──► assess ───┘
```

A **change is not an observation**: a trajectory event alters the patient and
the next scheduled look is what finds it. The gap between them is detection
latency, and it is a property of the department's reassessment policy rather
than of the model.

## Configuration

Not a single threshold is hard-coded in the engine. Every number lives in
`data/` behind a visible disclaimer:

| File | Holds |
|---|---|
| `hospitals/*.json` | band cutoffs, intervals, care targets, capacity |
| `risk_weights.json` | point values, domain caps |
| `clinical_thresholds.json` | age-banded vital ranges |
| `uncertainty_config.json` | driver weights, interval shape |
| `facial_config.json` | baseline provenance reliability |
| `safety_rules.json` | the hard rules |
| `ratchet_config.json` | override policy |
| `audit_config.json` | log path, hashing, which events |
| `simulation_config.json` | clock horizon, event cap |
| `questions.json` | question bank, ranking weights |
| `workflow_config.json` | clinician action policy |
| `surge_config.json` | capacity, deferral policy |
| `privacy_config.json` | retention, export, lawful basis |

The same engine runs a 3-nurse rural ED and a 16-nurse trauma centre with zero
code changes.

## Entry points

```bash
python -m scripts.check_setup        # foundations
python -m scripts.show_patients      # the roster
python -m scripts.run_triage         # everything, phase by phase
python -m scripts.run_tests          # the safety argument
python -m scripts.build_dashboard    # board.html
```
