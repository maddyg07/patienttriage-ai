# Assumptions

Every number in this system that somebody could question, in one place, with
where it lives and why it has the value it has.

> **All values below are simulated demonstration values.** None is derived from
> a real hospital, a clinical guideline, or a validated instrument. Nothing in
> the engine is hard-coded — every figure here lives in `data/` behind a
> disclaimer, so tuning during a demo is editing a file rather than editing
> logic.

## The band ladder, and the ESI cross-walk

`core/enums.py` promises this table. It matters because **our numbering runs
the opposite way to the standard scale**, and a number read the wrong way round
in a clinical setting is a serious problem.

| Ours | Word | Meaning | ESI equivalent |
|---|---|---|---|
| L4 | CODE | Resuscitation, immediate action | ESI 1 |
| L3 | PULL | Very urgent, nurse within minutes | ESI 2 |
| L2 | LOOK | Needs a glance, timely assessment | ESI 3 |
| L1 | WATCH | Stable, monitored waiting, timed re-check | ESI 4–5 |

In ESI, **Level 1 is the most critical** — the reverse of ours. `TriageBand` is
an `IntEnum` so that `L3_PULL > L2_LOOK` is true and the ratchet is one
comparison, which is why the ordering runs the way it does.

Because of the collision risk, every user-facing surface leads with the **word**
(CODE / PULL / LOOK / WATCH) and shows the number second. This is the one place
in the project where a display convention is a safety decision.

The four-level ladder itself is a simplification. ESI has five levels and
distinguishes 4 from 5 on expected resource use, which we do not model.

## Band thresholds

`data/hospitals/*.json`

| | Score |
|---|---|
| L2 LOOK | ≥ 25 |
| L3 PULL | ≥ 50 |
| L4 CODE | ≥ 75 |

**Identical across all three hospital profiles, by design.** Capacity changes
how often we look at a patient; it never changes how sick we judge them to be.
That line has been in `large_ed.json` since the first commit and Phase 14 turned
it into an asserted invariant.

## Reassessment intervals and care targets

`data/hospitals/*.json`. Two different things that are easy to confuse:

- **Reassessment interval** — how long before we look again.
- **Time-to-clinician target** — how long before a person should have seen them.

| | Small (3 nurses) | Medium (8) | Large (16) |
|---|---|---|---|
| CODE reassess / target | 0 / 0 | 0 / 0 | 0 / 0 |
| PULL | 10 / 15 | 8 / 10 | 5 / 10 |
| LOOK | 30 / 90 | 20 / 60 | 15 / 45 |
| WATCH | 45 / 180 | 30 / 120 | 25 / 100 |

A CODE interval of 0 means "this patient must not be waiting", not "re-check
infinitely often" — they leave the waiting-room schedule entirely. Taking the
zero literally is an infinite loop that looks like thoroughness.

Care targets are **displayed and never scored**. `core/config.overdue_by()`
returns minutes and its only caller is `app/view_model.py`; a test asserts no
engine reads it.

## Risk weights

`data/risk_weights.json`. Selected so that the resulting ranking matched
clinical intuition on the authored roster — which is exactly as circular as it
sounds, and is why no accuracy claim is made anywhere.

Domain caps exist because naive summation double-counts: a breathless patient
trips six respiratory signals for one clinical problem, and on the first run of
the engine a moderate asthma attack outscored a cardiac arrest. Each clinical
domain is capped and a cap that bites appears as its own line in the panel, so
the explanation still sums to the score.

## Age thresholds

`data/clinical_thresholds.json`, five tables (infant / child / adolescent /
adult / geriatric). Ranges are simplified from the general shape of paediatric
and adult vital-sign norms. Age band boundaries (1, 12, 18, 65) are convention,
not physiology.

`core/age_rules.py` also carries named context rules — masked tachycardia on
rate-limiting drugs, anticoagulated head injury, infant lethargy. **The drug
lists are string-matched** and would break immediately in production on brand
names and combination products.

## Confidence

`data/uncertainty_config.json`. Four drivers, weights summing to 1.0:

| Driver | Weight |
|---|---|
| completeness | 0.35 |
| agreement | 0.25 |
| baseline | 0.25 |
| staleness | 0.15 |

The **upward span is 100 points and the downward fraction 0.25** — the interval
reaches four times further up than down, because information we do not have can
conceal danger but cannot manufacture safety. The upward span is the full range
because at zero confidence no band can honestly be excluded; it was chosen from
that principle, not from what it does to any particular patient.

Staleness flattens after 90 minutes and caps at 15 of 100 confidence points.
Old data is weaker, not absent.

## Safety rules

`data/safety_rules.json`. Eight named clinical patterns, each setting a floor on
the band independently of the score. Simplified demonstration patterns; no
clinician has reviewed them.

R7 fires at a 75% confidence cutoff while P016 sits at 72%, which looks like a
threshold picked to catch her. Move the cutoff anywhere from 75% to 100% and she
is still the only patient who fires it; below 75%, nobody does. The requirement
for an *unresolved concerning finding* is what selects her.

## Questions

`data/questions.json`. Ten illustrative prompts, not a validated screening
instrument. Ranking weights: band movement 0.75, confidence 0.25, de-escalation-
only movement 0.35, cost penalty capped at 0.35. Cap of three questions shown.

Costs are rough effort estimates, not measured times.

## Surge capacity

`data/surge_config.json`.

- **20 reassessments per nurse per hour** (3 minutes each) — an assumption doing
  real work in every capacity figure. It is fast because the system takes part
  of the observation. Manual would be nearer ten minutes and a third of the
  capacity. Unvalidated.
- **50% of a nurse's time** available for reassessment. The rest is arrivals,
  treatment, handover, and the patients already in beds.
- **Anti-starvation at 10 minutes.** A compromise for this roster, explicitly
  not a recommendation — the sweep shows no safe value.

## Privacy

`data/privacy_config.json`. Retention: identity map 30 days, assessment detail
90 days, audit log 7 years. **None enforced by code.** Lawful basis: NOT
ESTABLISHED, deliberately.

## Patients

`data/patients.json`. 24 patients, hand-authored to exercise specific
behaviours, each carrying `scenario_label`, `expected_behaviour` and
`demonstrates` tags. Those three fields are prototype-only and would not exist
in production; the Phase 15 suite reads the tags so tests express intent rather
than hard-coded IDs.

Not a case mix. See `docs/limitations.md`.
