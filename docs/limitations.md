# Limitations

> The README has pointed here since Phase 5 for the question "what would real
> validation require?". This is that answer, and it is longer than the list of
> things the system does well.

## The one that subsumes the rest

**Nothing in this repository has been validated against a real patient, a real
outcome, or a real clinician.**

Every threshold, weight, rule, question and capacity figure is a simulated
demonstration value that we chose. The 24 patients were authored by us, to
demonstrate behaviours we had already decided the system should have. A system
evaluated on data written to exercise it will always look good, and any
performance figure produced from this repository is a property of our own
scenario file rather than of the world.

That is not false modesty. It is the single most important thing a judge or a
clinician should take from this document, and everything below is a
consequence of it.

## What we do not claim

- **No miss rate.** We have never measured one and could not: there are no
  outcomes to be wrong about.
- **No clinical accuracy.** No clinician has reviewed the weights, the
  thresholds, the safety rules or the question bank.
- **No sensitivity or specificity.** Same reason.
- **No coverage guarantee.** The plausible band set is deliberately *not*
  called conformal prediction (Phase 5). A real conformal predictor calibrates
  against labelled outcomes; we have none, so we ship a monotone widening rule
  and say so.
- **No diagnosis.** The facial module reports that findings appeared together
  and that a baseline did or did not explain them. It never names a condition
  as a finding.
- **No throughput claim.** The simulation has no arrival generator; surge is
  our own roster replicated.

## Specific known gaps

### Pinned in the test suite

Two properties this project implies and does not hold. They are marked
`expectedFailure` so they appear in every run of `python -m scripts.run_tests`
and the suite reports it if either is ever fixed.

**Removing information can raise confidence.** Found by the Phase 15 suite. The
agreement driver computes its split over the modalities that *spoke*, so
silencing a dissenting one removes a disagreement — 13 of 210 single-item
deletions across the roster increase confidence. Not measuring the thing that
disagrees should never be a way to look more certain. The fix is a pessimistic
split; it is not shipped because it re-calibrates every confidence figure in
the repository.

**No patient exercises the acute-on-chronic facial case.** Open since Phase 6.
A patient with documented asymmetry whose family says it got worse *today* is
arguably the hardest facial case in practice. The module handles it correctly
and the demo never shows it.

### In the data

- **The roster is too well-informed.** Patients arrive volunteering everything —
  P006 states unprompted that she struck her head. Real patients answer the
  question they were asked and no more. Two of ten questions in the Phase 11
  bank never fire because of this, and the roster therefore *understates* what
  an adaptive questioner is worth.
- **Nobody in the roster improves.** The ratchet's hold has to be demonstrated
  with a constructed recovery (`--ratchet`), because no authored patient gets
  better. That is a real gap in the Phase 2 data.
- **24 patients is not a case mix.** It is a set of edge cases chosen to
  exercise specific behaviours.

### In the model

- **Drug matching is string-based.** `RATE_LIMITING_DRUGS` and `ANTICOAGULANTS`
  in `core/age_rules.py` are name-matched lists. That works for 24 patients and
  would break immediately on brand names, generics, spellings and combination
  products. A production system resolves these from a coded drug dictionary.
- **The question engine is not expected value of information.** We compute the
  *range* of outcomes rather than their expectation, because we have no
  calibrated prior over how a patient will answer. Deliberately biased toward
  asking.
- **The agreement driver is crude by design.** It reduces each modality to one
  word using adult-agnostic thresholds. It is a coarse consistency check, not a
  second scoring engine, and it is the source of the confidence bug above.
- **Domain caps are hand-set.** They stop double-counting, and the specific cap
  values are ours.

### In the simulation

- **Every reassessment fires exactly when due** (unless capacity binds). No
  real department achieves that, and the gap between policy and practice is
  most of what goes wrong in a waiting room.
- **Three minutes per reassessment** is an assumption doing real work in every
  capacity figure in Phase 14. It is fast because the system takes part of the
  observation; a department doing this manually would have a third of the
  capacity. We cannot validate it.
- **No arrival model, no random deterioration.** Deterministic replay of
  authored scenarios.
- **Surge has no safe setting.** The anti-starvation sweep trades missed
  deteriorations against neglected known-sick patients monotonically. Our
  default is a compromise for this roster and is not a recommendation.

### In deployment

- **The audit log is tamper-evident, not tamper-proof.** Anyone who can write
  the file can recompute the chain. Real resistance needs the digest anchored
  somewhere the writer does not control.
- **No access control, no encryption at rest, no read auditing.** See
  `docs/privacy.md`; the read-auditing gap is the largest.
- **Retention is documented and unenforced.**
- **Lawful basis is not established.**

## What real validation would require

Roughly in order of what would have to happen first.

1. **Clinical review of every threshold, weight and rule** by emergency
   clinicians, against a recognised scale (ESI, Manchester, ATS) rather than
   against our own ladder.
2. **Retrospective evaluation** on real historical presentations with known
   outcomes, at multiple sites, reporting under-triage rate as the primary
   measure — because under-triage is the failure that kills people and
   over-triage is the failure that wastes time, and a single accuracy number
   hides which one you are getting.
3. **Subgroup analysis as a gate, not a supplement.** Performance by age, sex,
   ethnicity, language, disability, housing status and documentation status.
   The facial module's fairness counterfactual tests our *design intent*; it
   says nothing about outcomes in a population. A system that performs worse on
   people without records is exactly the failure mode this project claims to
   avoid and cannot currently demonstrate avoiding.
4. **Calibration of the uncertainty layer** against real outcomes, which is
   also what would let the band set be called conformal honestly.
5. **Prospective silent running** — the system scoring alongside triage nurses
   without showing them anything — long enough to compare, before any output is
   ever displayed.
6. **A DPIA, a lawful basis determination, and a controller/processor
   decision** before any real data is touched.
7. **Regulatory classification.** Triage support is very likely a medical
   device in most jurisdictions. Nothing here has been built to a medical
   device software standard.
8. **Human factors evaluation.** The Phase 12 board and the Phase 13 workflow
   have never been in front of a nurse under time pressure, and the Phase 11
   question cap exists because we *reasoned* that an uncapped list would be
   ignored — we did not observe it.

## Two things we would say in our own favour

Not to soften the list, but because they are the parts we would defend.

The system is built so that these gaps are **findable**. The score is the sum
of its explanation, so it cannot drift from what happened. The safety
properties are tests that can go red, several with companion tests that break
the invariant deliberately. The known gaps are pinned in the suite rather than
deleted.

And the failure direction is chosen and enforced rather than hoped for. The
ratchet has no code path that lowers a band; the safety rules have no path that
lowers one; the uncertainty engine cannot write the score; nothing can mark a
patient as seen. Those are properties of what is absent from the code, which is
the only kind of safety claim that survives somebody editing it later.

---

## Why there is no facial-expression dataset in this project

The question comes up every time somebody sees the doorway scan, and the
answer is not "we ran out of time".

**FER2013, AffectNet and CK+ classify the wrong thing.** They label seven basic
emotions: happy, sad, angry, surprised, fearful, disgusted, neutral. A model
trained on them can tell you a face looks sad. It cannot tell you a face is
drooping on one side, and it has never seen a clinical presentation of
anything. Wiring one in would let this console print "AI detected distress"
backed by a classifier trained on actors posing to camera. That is worse than
a crude honest measurement, because it looks like evidence.

**They are posed, not clinical.** CK+ and much of AffectNet are posed or
web-scraped expressions of healthy people. Pain, respiratory distress and
neurological deficit are not in the label space at any point, so accuracy on
the benchmark says nothing at all about accuracy on a patient.

**Licensing.** AffectNet is research-only and non-commercial. CK+ requires a
signed agreement. FER2013 carries Kaggle competition terms. None of those
survive contact with a product.

**And the deepest reason.** Even a perfect distress classifier cannot say
whether a facial difference arrived this morning or at birth, and that single
question decides stroke versus a person's ordinary appearance. It is answered
by asking. Better detection does not move that question one inch closer to
being solved, which is the argument this whole project is built on.

### What we did instead

`app/landmarks.py`, enabled with `python -m scripts.run_clinic --landmarks`.

MediaPipe Face Landmarker: Apache 2.0, runs in the browser as WASM, **no
training data on our part and no dataset licence**. It returns 478 facial
landmarks, which are geometry rather than a judgement, so a measurement derived
from them is a measurement instead of a classifier's opinion dressed as one.

It gives mouth-corner height difference, eye-aperture difference and
eyebrow-height difference, each mirrored about the face's own midline and
normalised to the patient's interocular distance so the reading does not change
with how far they sit from the camera. Illumination stops mattering entirely,
because geometry is not luminance, and the side-lighting failure that produced
a false asymmetry in Phase 17 simply cannot occur.

Head roll replaces the lighting gradient as the rejection gate: a tilted head
produces every one of those differences with no facial asymmetry at all, which
is the geometric version of the same problem.

Off by default. It needs a CDN, and a live demo that depends on conference wifi
is a live demo that fails. On any failure — blocked CDN, unsupported browser,
no face in frame — the console falls back to the luminance measurement and
records which method produced the reading, so a nurse is never shown a
geometric figure that was actually a brightness comparison.

### Audio

There is no dataset here either, and for a simpler reason: the audio channel
measures a speech-and-pause envelope, which is a measurement and not a
classification. It needs no labels. Voice-quality analysis — hoarseness,
stridor, prosodic distress — would need a clinical corpus that we do not have
and could not licence, and it is not implemented rather than approximated.
