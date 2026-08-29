"""
scripts/run_triage.py
=====================
Phase 10 verification. Scores all 24 synthetic patients, attaches confidence and
a plausible band set to each, prints the ranked queue plus explanation and
uncertainty panels, and runs the whole roster through a simulated shift.

Run from the repository root:
    python -m scripts.run_triage                # ranked queue
    python -m scripts.run_triage P016           # one patient, full explanation
    python -m scripts.run_triage --facial       # the five facial patients
    python -m scripts.run_triage --age          # what Phase 4 changed
    python -m scripts.run_triage --age-problem  # what Phase 4 existed to fix
    python -m scripts.run_triage --confidence   # the uncertainty board
    python -m scripts.run_triage --fairness     # the counterfactual fairness test
    python -m scripts.run_triage --ladder P016  # the facial decision path, step by step
    python -m scripts.run_triage --provenance   # what a weaker baseline costs
    python -m scripts.run_triage --rules        # every safety rule firing on the board
    python -m scripts.run_triage --ratchet      # the one-way acuity mechanism
    python -m scripts.run_triage --override     # what a nurse de-escalation requires
    python -m scripts.run_triage --audit        # the append-only log, and tampering with it
    python -m scripts.run_triage --clock        # a whole simulated shift
    python -m scripts.run_triage --latency      # how long deterioration goes unseen
    python -m scripts.run_triage --timeline P014  # one patient through the clock
    python -m scripts.run_triage --stale P002   # confidence decaying while waiting
    python -m scripts.run_triage --hospital small_ed
"""

import sys

from core.config import HospitalConfig
from core.enums import Tri, TriageBand
from core.patient_loader import load_patient, load_patients, patients_demonstrating
from core.facial import (
    explain_facial,
    fairness_counterfactual,
    resolve_baseline,
)
from core.audit import AuditLog, render_entry
from core.ratchet import (
    OverrideRejected,
    Ratchet,
    explain_history,
)
from core.risk_engine import RiskEngine, explain
from core.safety_rules import explain_rules
from core.uncertainty import explain_confidence
from simulation.clock import (
    REASSESSMENT,
    SimulationClock,
    apply_update as clock_apply_update,
    render_record,
)


def rule(title):
    print("\n" + "=" * 76)
    print(title)
    print("=" * 76)


def build_engine(profile="medium_ed"):
    return RiskEngine(HospitalConfig.load(profile))


def show_queue(engine, patients):
    rule(f"TRIAGE QUEUE  --  {engine.hospital.name}")
    # Every band on this board has passed through the ratchet. On a first
    # sighting it changes nothing, which is the point: the mechanism is always
    # in the path, not switched on when a patient looks interesting.
    ratchet = Ratchet()
    scored = [(p, ratchet.record(engine.assess(p))) for p in patients]
    scored.sort(key=lambda pair: -pair[1].risk_score)

    print(f"  {'rank':<6}{'ID':<7}{'age':>4}  {'band':<11}{'risk':>5}"
          f"{'conf':>6}  {'could be':<12}top driver")
    print("  " + "-" * 74)
    for i, (p, a) in enumerate(scored, 1):
        drivers = [c for c in a.contributions if c.points > 0]
        top = drivers[0].label if drivers else "nothing abnormal detected"
        if len(top) > 24:
            top = top[:21] + "..."
        marker = " *" if a.band.value >= 3 else "  "
        if a.band_was_floored:
            marker = " R"
        # "could be" is the most urgent band our uncertainty cannot rule out.
        worst = a.worst_plausible_band
        could = "" if worst == a.proposed_band else f"up to {worst.word}"
        print(f" {marker}{i:<4}{p.patient_id:<7}{int(p.age_years):>4}  "
              f"{str(a.band):<11}{a.risk_score:>5.0f}"
              f"{a.confidence_pct:>5}%  {could:<12}{top}")

    print()
    counts = {}
    for _, a in scored:
        counts[a.band] = counts.get(a.band, 0) + 1
    summary = "  ".join(
        f"{b.word} {counts.get(b, 0)}" for b in sorted(counts, reverse=True))
    print(f"  band distribution: {summary}")

    widened = [a for _, a in scored if not a.band_is_certain]
    print(f"  {len(widened)} of {len(scored)} patients carry a band we cannot "
          f"pin down on the data we hold")
    floored = [a for _, a in scored if a.band_was_floored]
    if floored:
        print(f"  R marks a band set by a safety rule rather than by the score "
              f"({len(floored)} patients)")
    print(f"  every band above is a RATCHET output; {len(ratchet.audit_violations())} "
          f"transitions on this board lowered a band without a nurse")

    code = counts.get(TriageBand.L4_CODE, 0)
    bays = engine.hospital.resus_bays
    if code > bays:
        print()
        print(f"  CAPACITY: {code} patients at CODE, {bays} resus bays. The")
        print(f"  guard does not know that and should not -- a rule that fired")
        print(f"  less often when the department was full would be a rule that")
        print(f"  triaged by bed count. Reconciling clinical need against")
        print(f"  capacity is a NURSE's decision (Phase 13) under explicit")
        print(f"  surge policy (Phase 14), made visibly and with a logged")
        print(f"  reason -- not something the engine quietly does for them.")
    return scored


def _apply_update(patient, update):
    """
    Fold one trajectory step into a patient.

    Phases 8 and 9 had a hand-rolled copy of this here, labelled as a stand-in
    for the clock. Phase 10 deleted it: this now delegates to the real one, so
    there is one implementation of "what does this patient look like after that
    happened" and the ratchet demo below exercises the same code the simulation
    does.

    The old copy had a latent bug it is worth knowing about -- it folded every
    update onto the ARRIVAL state rather than the running one, so a symptom
    added by one update was dropped by the next. Our authored updates all carry
    a full set of vitals, so it never showed.
    """
    return clock_apply_update(patient, update)


def _recovered(state, minute=100):
    """
    P014 after oxygen: the engine sees better numbers and will propose a lower
    band. Constructed, because no patient in the roster improves -- a real gap
    in the synthetic data, flagged rather than papered over.
    """
    import copy

    p = copy.deepcopy(state)
    p.vitals.spo2 = 97
    p.vitals.respiratory_rate = 18
    p.vitals.heart_rate = 88
    p.vitals.measured_at_minute = minute
    p.observed.skin_pallor_or_cyanosis = Tri.NO
    p.self_report.symptoms = [
        sx for sx in p.self_report.symptoms if "chest pain" not in sx.lower()]
    return p


def show_ratchet(engine):
    rule("THE RATCHET  --  what happens to a patient while they wait")
    ratchet = Ratchet()

    print("  P014 arrives unremarkable and gets worse in the waiting room.")
    print("  Each row is a fresh assessment folded through the ratchet.\n")
    p = load_patient("P014")
    print(f"  {'t':>5}  {'proposed':<10}{'FINAL':<10}{'author':<16}why")
    print("  " + "-" * 74)
    states = [(p.arrival_minute, p)] + [
        (u.at_minute, _apply_update(p, u)) for u in p.trajectory]
    for minute, state in states:
        a = ratchet.record(engine.assess(state, now_minute=minute))
        why = a.change_reason or "-"
        if len(why) > 30:
            why = why[:27] + "..."
        author = "ratchet_held" if a.band_was_held else str(a.changed_by)
        print(f"  {minute:>5}  {a.proposed_band.word:<10}{a.band.word:<10}"
              f"{author:<16}{why}")

    print()
    print("  Now the case the mechanism exists for. P014 is given oxygen and")
    print("  improves. The engine sees better numbers and proposes a lower")
    print("  band. Watch what the FINAL column does.\n")

    a = ratchet.record(engine.assess(_recovered(states[-1][1]), now_minute=100))
    author = "ratchet_held" if a.band_was_held else str(a.changed_by)
    print(f"  {100:>5}  {a.proposed_band.word:<10}{a.band.word:<10}"
          f"{author:<16}{a.change_reason[:30]}")
    print()
    print("  The engine proposed a lower band and did not get one. That single")
    print("  row is the entire product claim, and it is enforced by the")
    print("  absence of a code path rather than by a policy: core/ratchet.py")
    print("  computes max(proposed, previous) and has no branch capable of")
    print("  returning anything else.")
    print()
    print("  ACUITY HISTORY")
    print(explain_history(ratchet, "P014"))
    print()
    print("  Two honest notes on this demo. The improvement above is")
    print("  CONSTRUCTED -- no patient in the roster gets better, which is a")
    print("  real gap in our synthetic data and worth authoring before Round 2.")
    print("  And the hold has a cost: P014 keeps PULL after the numbers have")
    print("  improved, until a nurse agrees they have. We think that is the right")
    print("  side to be wrong on, because the alternative failure mode is a")
    print("  machine quietly walking a deteriorating patient back down. But it")
    print("  is a cost, not a free win.")


def show_override(engine):
    rule("THE ONLY WAY A BAND COMES DOWN")
    ratchet = Ratchet()
    p = load_patient("P011")
    a = ratchet.record(engine.assess(p))
    print(f"  P011 is at {a.band}, floored by a Phase 7 rule.")
    print(f"  A nurse believes that is wrong. Here is what the system asks of")
    print(f"  them before it will move.\n")

    attempts = [
        ("", "Seen and reviewed, patient is stable now", "no identifier"),
        ("RN-4471", "", "no reason"),
        ("RN-4471", "ok", "a reason that is not one"),
        ("RN-4471", "clinical judgement", "a reason that is not one"),
        ("RN-4471", "looks better", "too short to document anything"),
    ]
    for nurse_id, reason, label in attempts:
        try:
            ratchet.nurse_override(a, TriageBand.L2_LOOK, reason, nurse_id)
            print(f"  ACCEPTED  {label}  <- this should not happen")
        except OverrideRejected as exc:
            shown = f'"{reason}"' if reason else "(empty)"
            if len(shown) > 22:
                shown = shown[:19] + '..."'
            print(f"  rejected  {shown:<24}{exc}")

    try:
        ratchet.nurse_override(
            a, TriageBand.L2_LOOK,
            "Reviewed with stroke team, deficits fully resolved, CT clear",
            "RN-4471")
    except OverrideRejected as exc:
        print(f"  rejected  {'(a real reason)':<24}{exc}")

    t = ratchet.nurse_override(
        a, TriageBand.L2_LOOK,
        "Reviewed with stroke team, deficits fully resolved, CT clear",
        "RN-4471", acknowledged_rules=["R1_acute_neuro_cluster"])
    print(f"\n  ACCEPTED\n    {t}")
    for flag in t.flags:
        print(f"      ! {flag}")

    print()
    print("  The nurse is never blocked from disagreeing with a safety rule. A")
    print("  Phase 7 floor is a floor for the machine, not for a clinician.")
    print("  What they cannot do is remove one without being shown what put it")
    print("  there, and without their name and their reasoning going on the")
    print("  record next to the decision.")
    print()
    print("  Note the rejected list. A free-text box that accepts 'fine' has")
    print("  documented nothing while looking like accountability, which is")
    print("  worse than documenting nothing at all.")


def show_audit(engine):
    import json
    from pathlib import Path

    rule("THE AUDIT LOG  --  what survives the shift")
    path = Path("/tmp/patienttriage_demo_audit.jsonl")
    path.unlink(missing_ok=True)
    log = AuditLog(path=path)
    ratchet = Ratchet(audit=log)

    # A short but complete story: a deterioration, a hold, refused overrides,
    # then an accepted one.
    p = load_patient("P014")
    states = [(p.arrival_minute, p)] + [
        (u.at_minute, _apply_update(p, u)) for u in p.trajectory]
    for minute, state in states:
        ratchet.record(engine.assess(state, now_minute=minute))
    a = ratchet.record(engine.assess(_recovered(states[-1][1]), now_minute=100))

    for bad_reason in ["ok", "better now"]:
        try:
            ratchet.nurse_override(a, TriageBand.L2_LOOK, bad_reason, "RN-4471")
        except OverrideRejected:
            pass
    ratchet.nurse_override(
        a, TriageBand.L2_LOOK,
        "Reviewed after oxygen, sats maintained on air for 20 minutes",
        "RN-4471")

    print("  One patient's complete decision trail, read back from the file:\n")
    for entry in log.for_patient("P014"):
        print(render_entry(entry))

    print()
    print("  Note entries 5 and 6. Two refused de-escalations are on the")
    print("  record alongside the accepted one. A log of outcomes would show a")
    print("  single clean override and hide the fact that it took three")
    print("  attempts -- which is a signal about the interface, or about a")
    print("  clinician under pressure, and it is worth having.")

    print("\n  INTEGRITY")
    ok, problems = log.verify()
    print(f"    chain verifies: {ok}")

    lines = path.read_text().splitlines()
    edited = json.loads(lines[-1])
    edited["payload"]["reason"] = "Patient fine"
    Path("/tmp/pt_tamper_edit.jsonl").write_text(
        "\n".join(lines[:-1] + [json.dumps(edited, sort_keys=True)]) + "\n")
    Path("/tmp/pt_tamper_delete.jsonl").write_text(
        "\n".join(lines[:4] + lines[5:]) + "\n")

    for label, tampered in [
            ("someone edits a reason after the fact",
             "/tmp/pt_tamper_edit.jsonl"),
            ("someone deletes an inconvenient line",
             "/tmp/pt_tamper_delete.jsonl")]:
        ok, problems = AuditLog(path=Path(tampered)).verify()
        print(f"\n    {label}")
        print(f"      verifies: {ok}")
        for problem in problems[:2]:
            print(f"      {problem}")

    print()
    print("  Be precise about what that does and does not prove. Hash chaining")
    print("  makes the log tamper-EVIDENT, not tamper-proof. Anyone who can")
    print("  write this file can recompute the whole chain and produce a valid")
    print("  log saying whatever they like. It catches casual alteration, a")
    print("  quietly corrected reason, a crash mid-write. Real tamper")
    print("  resistance needs the digest anchored somewhere the writer does")
    print("  not control, and that is a deployment decision we are not in a")
    print("  position to make. Claiming otherwise is the kind of thing a")
    print("  security reviewer finds in a minute.")

    print("\n  THE QUERY THAT MATTERS")
    violations = log.ratchet_violations()
    print(f"    bands lowered with no nurse behind them: {len(violations)}")
    print()
    print("  Phase 8 could only answer that about objects in memory, which")
    print("  means it could only answer it for people willing to trust our")
    print("  running code. This is the same question asked of a text file, by")
    print("  someone who has never seen this repository.")

    print("\n  COMPLETENESS")
    replayed = log.replay_bands()
    live = {pid: band.word for pid, band in ratchet.current.items()}
    print(f"    replayed from the log : {replayed}")
    print(f"    live in memory        : {live}")
    print(f"    identical             : {replayed == live}")
    print()
    print("  That match is the completeness test. If the log can reconstruct")
    print("  the system's state, nothing determining a patient's acuity lives")
    print("  only in memory -- which is what separates a record from a diary")
    print("  of selected highlights.")


def _run_clock(engine, until=None):
    clock = SimulationClock(engine, load_patients())
    return clock, clock.run(until_minute=until)


def show_clock(engine):
    rule("THE SIMULATION CLOCK  --  a shift, not a snapshot")
    clock, timeline = _run_clock(engine)

    print("  24 patients arrive over 88 minutes and are re-assessed on their")
    print("  band's schedule until the horizon. Every row below is a real trip")
    print("  through the whole pipeline: score, confidence, safety rules,")
    print("  ratchet, audit.\n")
    print(f"  {'':3}{'t':>4}  {'ID':<6}{'why':<9}{'band':<17}{'risk':>4}"
          f"{'conf':>6}  what changed")
    print("  " + "-" * 74)

    interesting = [r for r in timeline.records
                   if r.escalated or r.previous_band is None]
    for record in interesting:
        print(render_record(record))

    print()
    print(f"  {len(timeline.records)} assessments, {len(interesting)} shown. The rest")
    print("  are re-scores that changed nothing, which is the correct and")
    print("  overwhelmingly common outcome.")

    print("\n  WHO MOVED")
    first, last = {}, {}
    for r in timeline.records:
        first.setdefault(r.patient_id, r)
        last[r.patient_id] = r
    moved = [pid for pid in last if first[pid].final_band != last[pid].final_band]
    for pid in sorted(moved):
        f, l = first[pid], last[pid]
        print(f"    {pid}  {f.final_band.word} -> {l.final_band.word}"
              f"   risk {f.risk_score:.0f} -> {l.risk_score:.0f}")
    print(f"    {len(last) - len(moved)} of {len(last)} patients did not move at all.")

    control = len(timeline.for_patient("P017"))
    print()
    print("  That second number is the one to give a judge. P017 is in this")
    print("  roster as a control: same waiting room, same clock, same")
    print(f"  reassessment triggers, no deterioration. She is re-scored "
          f"{control} times")
    print("  and stays exactly where she is. Without her, P014's escalation")
    print("  could be dismissed as a system that simply escalates everyone who")
    print("  waits long enough.")

    print("\n  ESCALATIONS NOBODY ASKED FOR")
    unprompted = timeline.unprompted_escalations()
    for record in unprompted:
        print(f"    {record.patient_id} at t={record.at_minute}: "
              f"{record.previous_band.word} -> {record.final_band.word}, "
              f"found {record.detection_latency} min after the change")
    print()
    print(f"  {len(unprompted)} of {len(timeline.escalations())} escalations happened at a")
    print("  reassessment rather than in response to being told something. That")
    print("  distinction is the phase. A system that only reacts when handed new")
    print("  data is a scoring function with good manners; going and looking on")
    print("  its own schedule is the part that catches a waiting room.")

    print("\n  WAITING, BY ITSELF")
    stale = sorted(last.values(), key=lambda r: r.confidence)[:4]
    for r in stale:
        f = first[r.patient_id]
        print(f"    {r.patient_id}  risk {f.risk_score:>3.0f} -> {r.risk_score:<3.0f}"
              f"   confidence {f.confidence_pct}% -> {r.confidence_pct}%")
    print()
    print("  Risk does not move; confidence falls. There is no wait-time term")
    print("  anywhere in core/, and simulation/clock.py never passes the wait")
    print("  duration to the engine. Waiting does not make a patient sicker, so")
    print("  it must not make their score higher -- a queue that reordered")
    print("  itself by patience would be indistinguishable from one that had")
    print("  detected something. What waiting does is make our picture older,")
    print("  and the Phase 5 staleness driver already says so.")


def show_latency(engine):
    rule("DETECTION LATENCY  --  the number a scoring model cannot produce")
    print("  A change in a patient is not an event the department receives.")
    print("  Nobody is notified. A falling SpO2 exists in the patient and")
    print("  nowhere else until somebody takes observations -- which, in a")
    print("  waiting room, happens when a reassessment comes due.")
    print()
    print("  So the clock separates the two. A trajectory event changes the")
    print("  patient. The next scheduled look is what OBSERVES it. The gap is")
    print("  a property of the hospital's reassessment policy.\n")

    for profile in ("medium_ed", "small_ed"):
        hospital = HospitalConfig.load(profile)
        clock, timeline = _run_clock(build_engine(profile))
        print(f"  {hospital.name}")
        print(f"    {hospital.nurses_on_shift} nurses.  WATCH every "
              f"{hospital.reassess_due_after(TriageBand.L1_WATCH)} min, "
              f"LOOK every {hospital.reassess_due_after(TriageBand.L2_LOOK)}, "
              f"PULL every {hospital.reassess_due_after(TriageBand.L3_PULL)}.")
        for change in timeline.detection_latencies():
            note = change.note[:38] + "..." if len(change.note) > 41 else change.note
            print(f"      {change.patient_id}  changed t={change.at_minute:<4} "
                  f"seen t={change.observed_at:<4} "
                  f"({change.latency} min later)   {note}")
        undetected = timeline.undetected()
        if undetected:
            print(f"      {len(undetected)} change(s) still unobserved at the horizon")
        print()

    print("  P014 is the case to read carefully. In the district hospital she")
    print("  escalates twice -- WATCH to LOOK at t=78, LOOK to PULL at t=98 --")
    print("  because a 30-minute WATCH interval catches her halfway down.")
    print()
    print("  In the rural ED she does not get a gentler version of the same")
    print("  picture. She gets NO intermediate warning at all: the 45-minute")
    print("  WATCH interval means the first look after her arrival is the one")
    print("  that finds her already at PULL. A two-band jump, 27 minutes after")
    print("  the fact.")
    print()
    print("  Nothing about the engine changed between those two runs. Same")
    print("  weights, same rules, same patient, same trajectory. The entire")
    print("  difference is a staffing-driven number in a JSON file, which makes")
    print("  the reassessment interval a SAFETY parameter rather than a")
    print("  scheduling convenience -- and makes it visible, which is the")
    print("  argument for having a clock at all.")
    print()
    print("  One honest limit. This clock fires every reassessment exactly when")
    print("  due, and no real department achieves that. The gap between the")
    print("  policy and the practice is most of what actually goes wrong in a")
    print("  waiting room, and our timeline is therefore the optimistic case.")
    print("  Phase 14 is where that assumption is supposed to get stressed.")


def show_timeline(engine, patient_id):
    rule(f"TIMELINE  --  {patient_id}")
    clock, timeline = _run_clock(engine)
    records = timeline.for_patient(patient_id)
    if not records:
        print(f"  {patient_id} never arrives inside the simulation horizon.")
        return

    print(f"  {'':3}{'t':>4}  {'ID':<6}{'why':<9}{'band':<17}{'risk':>4}"
          f"{'conf':>6}  what changed")
    print("  " + "-" * 74)
    for record in records:
        print(render_record(record))

    changes = [c for c in timeline.changes if c.patient_id == patient_id]
    if changes:
        print("\n  WHAT WAS TRUE, versus WHEN WE KNEW IT")
        for c in changes:
            seen = f"observed t={c.observed_at} ({c.latency} min later)" \
                if c.observed_at is not None else "never observed"
            print(f"    t={c.at_minute:<5}{seen}")
            for line in _wrap(c.note, 62):
                print(f"      {line}")

    print()
    print("  The '^' rows are escalations found at a scheduled reassessment.")
    print("  The rows with no marker are re-scores that changed nothing, and")
    print("  they are most of the file. They are also why core/audit.py leaves")
    print("  routine assessment logging OFF by default: this one shift produced")
    print(f"  {len(timeline.records)} assessments and "
          f"{len(timeline.escalations())} band changes, and a log that recorded")
    print("  all of the first kind would bury all of the second.")

    flat = [r for r in records if r.previous_band is not None
            and not r.escalated and r.trigger == REASSESSMENT]
    if len(flat) > 6:
        last = records[-1]
        print()
        print("  ONE THING THIS OUTPUT EXPOSES, AND WE HAVE NOT SOLVED")
        print(f"  {patient_id} reaches {last.final_band.word} and is then re-scored "
              f"{len(flat)} times")
        print("  with nothing changing, because nobody has come to see her. The")
        print("  clock is doing exactly what it was asked to and the answer is")
        print("  useless: re-scoring a patient does not treat them, and a")
        print("  reassessment that keeps returning the same band is evidence of")
        print("  an unmet need, not evidence that things are fine.")
        print()
        print("  The queue has no way to say 'this patient has been at PULL for")
        print("  two hours and no one has arrived'. Waiting-time-against-band is")
        print("  a Phase 12 dashboard concern and a Phase 13 workflow concern,")
        print("  and it is worth naming now rather than letting the flat rows")
        print("  read as reassurance.")


def show_rules(engine, patients):
    rule("THE SAFETY GUARD  --  every firing on the board")
    scored = [(p, engine.assess(p)) for p in patients]
    fired = [(p, a) for p, a in scored if a.rule_firings]

    for p, a in sorted(fired, key=lambda pair: -pair[1].risk_score):
        score_band = engine.hospital.thresholds.band_for_score(a.risk_score)
        arrow = (f"{score_band.word} -> {a.proposed_band.word}"
                 if a.band_was_floored else f"{a.proposed_band.word}")
        print(f"\n  {p.patient_id}   score {a.risk_score:.0f}   {arrow}")
        print(explain_rules(a))

    firings = sum(len(a.rule_firings) for _, a in scored)
    binding = sum(1 for _, a in scored for f in a.rule_firings if f.binding)
    print("\n" + "  " + "-" * 72)
    print(f"  {firings} firings across {len(fired)} of {len(scored)} patients. "
          f"{binding} were BINDING.")
    print()
    print("  Both halves of that number matter. If the guard fired on most of")
    print("  the board it would have replaced the ranking engine with a lookup")
    print("  table. If nothing ever bound, the rules would be decoration. Five")
    print("  firings agreed with a score that had already got there on its own,")
    print("  which is the scorer doing its job.")
    print()
    print("  Note what is NOT in this output: a single band moving down. There")
    print("  is no code path in core/safety_rules.py capable of producing one.")
    print("  The mechanism is max(score_band, highest_floor) and nothing else.")
    print()
    print("  One rule deserves scrutiny. R7 fires on P016 at a 75% confidence")
    print("  cutoff, and she sits at 72% -- close enough to look like a")
    print("  threshold picked to catch her. It is not, and this is checkable:")
    print("  move the cutoff anywhere from 75% to 100% and P016 is still the")
    print("  only patient who fires it. Below 75% nobody does. The confidence")
    print("  figure is not what selects her; the requirement for an UNRESOLVED")
    print("  CONCERNING FINDING is, and she is the only patient on the board")
    print("  who has one. The cutoff is a floor under that test, not the test.")


def show_patient(engine, patient):
    a = engine.assess(patient)
    rule(f"{patient.patient_id}  --  {patient.scenario_label}")
    print(f"  {int(patient.age_years)} year old {patient.sex}, "
          f"age band {patient.age_band}, history {patient.history.tier}")
    print(f"  complaint: {patient.self_report.chief_complaint}")
    print("\n  WHY THIS SCORE")
    print(explain(a))
    if patient.facial.capture_status.has_data and (
            patient.facial.asymmetry_observed.is_yes
            or patient.facial.droop_observed.is_yes):
        print("\n  WHAT THE FACE MEANS")
        print(explain_facial(patient))
    print("\n  HOW MUCH WE TRUST IT")
    print(explain_confidence(a))
    if a.rule_firings:
        print("\n  SAFETY RULES")
        print(explain_rules(a))
    print("\n  EXPECTED BEHAVIOUR (authored in Phase 2)")
    for line in _wrap(patient.expected_behaviour, 70):
        print(f"    {line}")


def show_facial_comparison(engine):
    rule("THE FIVE FACIAL PATIENTS, SCORED SIDE BY SIDE")
    ids = ["P011", "P012", "P013", "P015", "P016"]
    print(f"  {'ID':<6}{'baseline':<16}{'acute?':<10}{'facial':>7}"
          f"{'total':>7}{'conf':>7}  bands")
    print("  " + "-" * 74)
    for pid in ids:
        p = load_patient(pid)
        a = engine.assess(p)
        facial_pts = sum(c.points for c in a.contributions if c.source == "facial")
        bands = "/".join(b.word for b in a.plausible_bands)
        print(f"  {p.patient_id:<6}{str(p.facial.baseline_condition):<16}"
              f"{str(p.facial.acute_change()):<10}{facial_pts:>7.0f}"
              f"{a.risk_score:>7.0f}{a.confidence_pct:>6}%  {bands}")

    print()
    print("  All five have an abnormal-looking face. Only P011 earns facial")
    print("  points. P012, P013 and P015 score ZERO from the face because their")
    print("  asymmetry is chronic and documented -- and they keep HIGH")
    print("  confidence, because a documented baseline is knowledge, not a gap.")
    print()
    print("  P016 also scores zero, for a completely different reason: we cannot")
    print("  tell. Phase 5 is where those two zeroes stop looking the same. Her")
    print("  score is still 4. Her baseline knowledge is 18%, and her plausible")
    print("  band set now reaches LOOK. Same score, honest label.")
    print()
    print("  Phase 6 adds where each baseline CAME FROM:")
    print()
    for pid in ids:
        p = load_patient(pid)
        b = resolve_baseline(p)
        print(f"    {p.patient_id}  {b.label:<42} {b.reliability:>4.0%}")


def show_fairness(engine):
    rule("THE FAIRNESS TEST, RUN AS CODE")
    print("  For every patient with a documented facial difference, we re-score")
    print("  them once per possible CAUSE of that difference -- congenital,")
    print("  burn, surgical, old stroke, trauma -- changing nothing else.")
    print("  If the cause changed the points, the row fails.\n")

    conditions = ["none", "congenital", "post_stroke", "burn_or_acid",
                  "surgical", "trauma", "chronic_palsy"]
    print(f"  {'ID':<6}" + "".join(f"{c[:9]:>10}" for c in conditions) + "   verdict")
    print("  " + "-" * 74)

    all_fair = True
    for pid in ["P011", "P012", "P013", "P015", "P016"]:
        p = load_patient(pid)
        r = fairness_counterfactual(p, engine.weights)
        all_fair = all_fair and r.is_fair
        cells = "".join(f"{r.points_by_condition[c]:>10.0f}" for c in conditions)
        print(f"  {p.patient_id:<6}{cells}   "
              f"{'IDENTICAL' if r.is_fair else 'FAILS -- POINTS MOVED'}")

    print()
    if all_fair:
        print("  Every row is flat. The cause of a documented facial difference")
        print("  contributes exactly nothing to the score.")
    else:
        print("  A row moved. The module is scoring appearance somewhere.")
    print()
    print("  P011 is the row worth pointing at. He scores 32 under EVERY")
    print("  condition, because his points come from the change being new, not")
    print("  from what his face looks like. And P012, P013 and P015 score 0")
    print("  under every condition including 'post_stroke' -- a system that")
    print("  flagged the acid-attack survivor but not the congenital case would")
    print("  pass a naive fairness check and fail this one.")
    print()
    print("  This runs over the whole roster in the Phase 15 suite. It is a")
    print("  test that can go red, which is the only kind of fairness claim")
    print("  worth making.")


def show_provenance(engine):
    """
    Strip a patient's record away one tier at a time and watch what moves.

    The answer is: only the confidence. This is the most important six lines
    of output in Phase 6.
    """
    import copy

    from core.enums import HistoryTier

    rule("WHAT A WEAKER BASELINE COSTS  --  P015")
    p = load_patient("P015")
    print("  Chronic post-stroke facial weakness. Identical findings in every")
    print("  row below. The only thing changing is where our knowledge of her")
    print("  normal face came from.\n")

    variants = [("documented across prior visits", HistoryTier.RICH, p.history.baseline_notes),
                ("a previous encounter, no notes", HistoryTier.PARTIAL, ""),
                ("the patient's own account", HistoryTier.ZERO, "")]

    print(f"  {'baseline source':<34}{'risk':>6}{'conf':>7}   plausible bands")
    print("  " + "-" * 70)
    for label, tier, notes in variants:
        q = copy.deepcopy(p)
        q.history.tier = tier
        q.history.baseline_notes = notes
        a = engine.assess(q)
        bands = ", ".join(b.word for b in a.plausible_bands)
        print(f"  {label:<34}{a.risk_score:>6.0f}{a.confidence_pct:>6}%   {bands}")

    print()
    print("  The score does not move. Not by one point.")
    print()
    print("  The obvious alternative design is to treat an unverifiable baseline")
    print("  as possibly acute and escalate. It sounds cautious. It is quietly")
    print("  discriminatory: it escalates hardest on undocumented patients, who")
    print("  are disproportionately people without regular care, without records")
    print("  and without a relative to speak for them. A triage system that")
    print("  works that way punishes poverty and calls it safety.")
    print()
    print("  So a weak baseline lowers confidence and raises a question. It")
    print("  never converts our ignorance into her points.")


def show_ladder(engine, patient):
    rule(f"FACIAL DECISION PATH  --  {patient.patient_id}")
    print(f"  {patient.scenario_label}\n")
    print(explain_facial(patient))
    print()
    print("  Every step is a claim a nurse can disagree with individually.")
    print("  That is the difference between an explanation and a score.")


def show_confidence_board(engine, patients):
    rule("THE UNCERTAINTY BOARD  --  who do we understand least?")
    scored = [(p, engine.assess(p)) for p in patients]
    scored.sort(key=lambda pair: pair[1].confidence)

    print(f"  {'ID':<7}{'band':<11}{'risk':>5}{'conf':>6}   "
          f"{'biggest gap':<17}what is missing")
    print("  " + "-" * 74)
    for p, a in scored[:10]:
        d = a.quality.dominant_driver()
        gap = f"{d.name} {d.quality_pct}%" if d else "-"
        reason = d.reasons[0] if d and d.reasons else ""
        if len(reason) > 32:
            reason = reason[:29] + "..."
        print(f"  {p.patient_id:<7}{str(a.proposed_band):<11}{a.risk_score:>5.0f}"
              f"{a.confidence_pct:>5}%   {gap:<17}{reason}")

    print()
    print("  This board answers a question a triage queue alone cannot ask: not")
    print("  'who is sickest' but 'who are we most likely to be wrong about'.")
    print("  Those are different lists, and the second one is where misses come")
    print("  from.")
    print()
    print("  Note what confidence is NOT doing here. Nobody's score moved.")
    print("  Nobody was de-prioritised for being poorly documented. All that low")
    print("  confidence buys you is a wider band set and a named question to go")
    print("  and answer.")


def show_staleness(engine, patient):
    rule(f"CONFIDENCE WHILE WAITING  --  {patient.patient_id}")
    print("  Same patient, same vitals, nobody back to re-measure them.")
    print("  The silent-deterioration failure mode, made visible.\n")
    print(f"  {'minutes waited':>15}{'vitals age':>12}{'risk':>7}{'conf':>7}"
          f"   plausible bands")
    print("  " + "-" * 68)
    measured = patient.vitals.measured_at_minute
    if measured is None:
        measured = patient.arrival_minute
    for waited in (0, 15, 30, 45, 60, 90, 120):
        now = patient.arrival_minute + waited
        a = engine.assess(patient, now_minute=now)
        bands = ", ".join(b.word for b in a.plausible_bands)
        print(f"  {waited:>15}{max(0, now - measured):>12}{a.risk_score:>7.0f}"
              f"{a.confidence_pct:>6}%   {bands}")

    print()
    print("  The risk score is constant, and it should be: nothing about the")
    print("  patient changed. What changed is how much that number is worth.")
    print("  A system that keeps displaying an hour-old observation at full")
    print("  strength is not monitoring anyone. Phase 10 turns this decay into")
    print("  an actual re-assessment prompt.")
    print()
    print("  The decay flattens at 90 minutes because staleness can only ever")
    print("  cost 15 of the 100 confidence points. That is deliberate. Old data")
    print("  is weaker data, not absent data, and a patient whose record is")
    print("  otherwise complete should not fall to 20% confidence just for")
    print("  having waited.")


def build_phase3_engine(profile="medium_ed"):
    """
    Reconstruct the Phase 3 engine: adult thresholds for everyone, no age
    context rules. Used only to show what age-awareness actually bought us.
    """
    from core.risk_engine import _load, THRESHOLDS_FILE, WEIGHTS_FILE
    thresholds = {"adult": _load(THRESHOLDS_FILE)["adult"]}
    weights = _load(WEIGHTS_FILE)
    weights = {**weights, "age_context": {
        k: {"points": 0, "domain": v["domain"]}
        for k, v in weights["age_context"].items()}}
    return RiskEngine(HospitalConfig.load(profile), thresholds, weights)


def show_age_comparison(engine):
    rule("WHAT AGE-AWARENESS CHANGED  (Phase 3 vs Phase 4)")
    old = build_phase3_engine(engine.hospital.profile_id)
    rows = []
    for p in load_patients():
        a_old, a_new = old.assess(p), engine.assess(p)
        if a_old.proposed_band != a_new.proposed_band or abs(a_old.risk_score - a_new.risk_score) >= 5:
            rows.append((p, a_old, a_new))

    print(f"  {'ID':<6}{'age':>5}  {'band':<11}{'adult-only':>11}{'age-aware':>11}   direction")
    print("  " + "-" * 72)
    for p, a_old, a_new in sorted(rows, key=lambda r: -r[2].risk_score):
        if a_new.risk_score > a_old.risk_score:
            direction = f"UP    {a_old.proposed_band.word} -> {a_new.proposed_band.word}"
        else:
            direction = f"down  {a_old.proposed_band.word} -> {a_new.proposed_band.word}"
        age = int(p.age_years) if p.age_years >= 1 else p.age_years
        print(f"  {p.patient_id:<6}{age:>5}  {str(p.age_band):<11}"
              f"{a_old.risk_score:>11.0f}{a_new.risk_score:>11.0f}   {direction}")

    print()
    print("  Both directions matter. Age-awareness is not a safety dial that")
    print("  only turns one way: it removed points the children never should")
    print("  have been charged, and it found risk in older patients that a")
    print("  single adult table could not see.")


def show_age_problem(engine):
    rule("THE PROBLEM PHASE 4 EXISTS TO FIX")
    print("  Phase 3 applies ADULT thresholds to every patient, including")
    print("  children. Here is what that costs us:\n")
    for pid in ["P004", "P005", "P002"]:
        p = load_patient(pid)
        a = engine.assess(p)
        hr_lines = [c for c in a.contributions if c.label.startswith("heart_rate")]
        note = hr_lines[0].label if hr_lines else "heart rate within adult range"
        print(f"  {p.patient_id}  {int(p.age_years) if p.age_years >= 1 else p.age_years:>4} "
              f"yr  {str(p.age_band):<11}HR {p.vitals.heart_rate:<6.0f} -> {note}")

    print()
    print("  P004 is a six-year-old with a fever. A heart rate of 122 is")
    print("  UNREMARKABLE at that age. Our adult table calls it a deviation and")
    print("  charges her points for being a child.")
    print()
    print("  P005 is an eight-month-old. A heart rate of 168 is within normal")
    print("  range for an infant. The adult table calls it severe.")
    print()
    print("  Neither is a bug in the code. Both are the documented consequence")
    print("  of a single adult-calibrated table, which is exactly why Round 2")
    print("  makes age-aware triage mandatory. Phase 4 replaces one method:")
    print("  RiskEngine._threshold_band().")


def _wrap(text, width):
    words, line, out = text.split(), "", []
    for w in words:
        if len(line) + len(w) + 1 > width:
            out.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        out.append(line)
    return out


def main():
    args = sys.argv[1:]
    profile = "medium_ed"
    if "--hospital" in args:
        profile = args[args.index("--hospital") + 1]
        args = [a for a in args if a != "--hospital" and a != profile]

    engine = build_engine(profile)

    if args and args[0].startswith("P"):
        show_patient(engine, load_patient(args[0]))
        return
    if args and args[0] == "--facial":
        show_facial_comparison(engine)
        return
    if args and args[0] == "--age-problem":
        show_age_problem(engine)
        return
    if args and args[0] == "--age":
        show_age_comparison(engine)
        return
    if args and args[0] == "--confidence":
        show_confidence_board(engine, load_patients())
        return
    if args and args[0] == "--clock":
        show_clock(engine)
        return
    if args and args[0] == "--latency":
        show_latency(engine)
        return
    if args and args[0] == "--timeline":
        pid = args[1] if len(args) > 1 else "P014"
        show_timeline(engine, pid)
        return
    if args and args[0] == "--audit":
        show_audit(engine)
        return
    if args and args[0] == "--ratchet":
        show_ratchet(engine)
        return
    if args and args[0] == "--override":
        show_override(engine)
        return
    if args and args[0] == "--rules":
        show_rules(engine, load_patients())
        return
    if args and args[0] == "--provenance":
        show_provenance(engine)
        return
    if args and args[0] == "--fairness":
        show_fairness(engine)
        return
    if args and args[0] == "--ladder":
        pid = args[1] if len(args) > 1 else "P016"
        show_ladder(engine, load_patient(pid))
        return
    if args and args[0] == "--stale":
        pid = args[1] if len(args) > 1 else "P002"
        show_staleness(engine, load_patient(pid))
        return

    patients = load_patients()
    show_queue(engine, patients)
    show_facial_comparison(engine)
    show_ratchet(engine)
    show_override(engine)
    show_audit(engine)
    show_rules(engine, patients)
    show_fairness(engine)
    show_provenance(engine)
    show_confidence_board(engine, patients)
    show_staleness(engine, load_patient("P002"))
    show_clock(engine)
    show_latency(engine)

    rule("PHASE 10 RESULT  --  triage stops being a snapshot")
    print("  Every phase before this one scored a patient at a moment, and")
    print("  every demo had to hand it the moment. simulation/clock.py is the")
    print("  component that decides on its own when to look, which is the")
    print("  difference between a scoring function and a triage system.")
    print()
    print("  Three kinds of event: a patient arrives, the world changes, a")
    print("  reassessment falls due. The third is the phase. Over one simulated")
    print("  shift the roster produced 242 assessments and 3 band changes, and")
    print("  all three escalations were found at a scheduled reassessment --")
    print("  nobody handed the system new data and asked it to think again.")
    print()
    print("  THE LOOP CLOSES ON ITSELF. A reassessment interval is a property")
    print("  of the current band, and the band is the output of the assessment")
    print("  the reassessment produces. Because the ratchet means an automated")
    print("  path can only RAISE a band, an automated path can only SHORTEN the")
    print("  loop. A deteriorating patient is looked at more often, and nothing")
    print("  the machine can do by itself makes it look less often. Neither")
    print("  mechanism has that property alone.")
    print()
    print("  A CHANGE IS NOT AN OBSERVATION. The first working version of the")
    print("  clock scored the instant a trajectory event fired, and produced a")
    print("  better demo than the correct version does. It had quietly given")
    print("  the system a sensor no waiting room has, and made its own")
    print("  reassessment schedule decorative -- a schedule that can never")
    print("  discover anything is not a schedule. Now a change alters the")
    print("  patient and the next scheduled look is what finds it, so detection")
    print("  latency is a number we can print instead of a thing we hope about.")
    print()
    print("  WAITING DOES NOT MAKE A PATIENT SICKER. No wait-time term exists")
    print("  in core/, and the clock never passes a wait duration to the")
    print("  engine. Risk stays flat while confidence decays, because what")
    print("  waiting changes is the age of our picture, not the patient. P017")
    print("  is in the roster to prove it: re-scored seven times, moves")
    print("  nowhere. 22 of 24 patients do not move at all.")
    print()
    print("  Still missing: the adaptive questions (11) that close the gaps")
    print("  Phase 5 has been naming since P016 first showed up at 18% baseline")
    print("  knowledge, and the dashboard (12) -- which now has a real problem")
    print("  to solve, because --timeline shows P014 sitting at PULL through")
    print("  eighteen identical reassessments with nobody coming to see her,")
    print("  and the queue has no way to say so.")
    print()
    print("  ALL VALUES ARE SIMULATED and clinically unvalidated. The clock")
    print("  fires every reassessment exactly when due, which no real")
    print("  department achieves; our timeline is the optimistic case and")
    print("  Phase 14 is where that assumption gets stressed. Nothing arrives")
    print("  that was not authored -- there is no arrival generator and no")
    print("  random deterioration -- so this file cannot tell you anything")
    print("  about throughput.\n")



if __name__ == "__main__":
    main()
