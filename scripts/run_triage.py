"""
scripts/run_triage.py
=====================
Phase 7 verification. Scores all 24 synthetic patients, attaches confidence and
a plausible band set to each, and prints the ranked queue plus explanation and
uncertainty panels.

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
    python -m scripts.run_triage --stale P002   # confidence decaying while waiting
    python -m scripts.run_triage --hospital small_ed
"""

import sys

from core.config import HospitalConfig
from core.enums import TriageBand
from core.patient_loader import load_patient, load_patients, patients_demonstrating
from core.facial import (
    explain_facial,
    fairness_counterfactual,
    resolve_baseline,
)
from core.risk_engine import RiskEngine, explain
from core.safety_rules import explain_rules
from core.uncertainty import explain_confidence


def rule(title):
    print("\n" + "=" * 76)
    print(title)
    print("=" * 76)


def build_engine(profile="medium_ed"):
    return RiskEngine(HospitalConfig.load(profile))


def show_queue(engine, patients):
    rule(f"TRIAGE QUEUE  --  {engine.hospital.name}")
    scored = [(p, engine.assess(p)) for p in patients]
    scored.sort(key=lambda pair: -pair[1].risk_score)

    print(f"  {'rank':<6}{'ID':<7}{'age':>4}  {'band':<11}{'risk':>5}"
          f"{'conf':>6}  {'could be':<12}top driver")
    print("  " + "-" * 74)
    for i, (p, a) in enumerate(scored, 1):
        drivers = [c for c in a.contributions if c.points > 0]
        top = drivers[0].label if drivers else "nothing abnormal detected"
        if len(top) > 24:
            top = top[:21] + "..."
        marker = " *" if a.proposed_band.value >= 3 else "  "
        if a.band_was_floored:
            marker = " R"
        # "could be" is the most urgent band our uncertainty cannot rule out.
        worst = a.worst_plausible_band
        could = "" if worst == a.proposed_band else f"up to {worst.word}"
        print(f" {marker}{i:<4}{p.patient_id:<7}{int(p.age_years):>4}  "
              f"{str(a.proposed_band):<11}{a.risk_score:>5.0f}"
              f"{a.confidence_pct:>5}%  {could:<12}{top}")

    print()
    counts = {}
    for _, a in scored:
        counts[a.proposed_band] = counts.get(a.proposed_band, 0) + 1
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
    show_rules(engine, patients)
    show_fairness(engine)
    show_provenance(engine)
    show_confidence_board(engine, patients)
    show_staleness(engine, load_patient("P002"))

    rule("PHASE 7 RESULT")
    print("  Both long-standing gaps are closed, and neither was closed by")
    print("  changing a weight.")
    print()
    print("  P011, the acute stroke, has sat at L3 since Phase 3. He still")
    print("  scores 64 and CODE still starts at 75. What changed is that a")
    print("  named rule now floors the acute neurological cluster at CODE and")
    print("  shows its evidence. The tempting fix -- inflate the facial and")
    print("  speech weights until he crosses -- would have re-ranked all 24")
    print("  patients to move one, and the distortion would have been")
    print("  invisible because the arithmetic still looks principled.")
    print()
    print("  P016 no longer sits last. She is L2 LOOK, floored by R7, because")
    print("  a concerning finding we cannot resolve on thin information is not")
    print("  the same thing as a patient with nothing wrong. Her score is")
    print("  still 4. Nobody pretended otherwise.")
    print()
    print("  The cost, stated plainly: after Phase 7 the score and the band can")
    print("  disagree. P011 reads 64/100 and L4 CODE on the same panel. That")
    print("  looks like a bug until you read the rule underneath it, and we")
    print("  would rather explain it than hide it. A score you can trust to")
    print("  mean what it says, plus rules that can override it in the open,")
    print("  beats a score quietly bent until it produces the right answers.")
    print()
    print("  Still missing: the ratchet (8) and the audit log (9). The band")
    print("  above remains a PROPOSAL. Nothing in this system can lower it yet,")
    print("  and after Phase 8 only a nurse will be able to, with a reason on")
    print("  the record. ALL VALUES ARE SIMULATED and clinically unvalidated;")
    print("  the rules above are simplified demonstration patterns, not")
    print("  clinical protocols, and no clinician has reviewed them.\n")


if __name__ == "__main__":
    main()
