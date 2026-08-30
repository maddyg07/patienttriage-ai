"""
core/notes.py
=============
Turns a session into notes a nurse can read in twenty seconds and correct in
one edit.

WHAT THESE NOTES ARE FOR
------------------------
A nurse arriving at an alert has not heard the conversation. They need the
complaint, what was found, when things changed, what the machine is unsure
about, and what it wants a human to look at. In that order, on one screen,
without prose.

WHY IT IS ASSEMBLED AND NOT GENERATED
-------------------------------------
A language model would write nicer notes. It would also, occasionally, write
notes containing something the patient did not say, and a fabricated symptom in
a clinical record is a worse failure than an awkward sentence.

So every line here is assembled from the event log. The section that quotes the
patient quotes the transcript verbatim. The section listing symptoms lists the
ledger. Nothing is paraphrased into existence, and a claim in these notes can
always be traced to a row in the timeline.

The one thing the notes never contain is a diagnosis. They contain findings,
observations, what the engine scored, and what needs a human. The heading is
"ITEMS REQUIRING NURSE REVIEW" rather than "IMPRESSION" for that reason.

EDITABLE
--------
Marked AI-generated wherever it is displayed, and the nurse's edit replaces it
wholesale. The original stays in the event log, so what the machine proposed
and what the human filed are both recoverable.
"""

from __future__ import annotations

from typing import List


def _rule(title: str) -> List[str]:
    return ["", title, "-" * len(title)]


def _duration(hours) -> str:
    if hours is None:
        return ""
    if hours < 1:
        return f"{int(hours * 60)} min"
    if hours < 48:
        return f"{hours:g} h"
    return f"{hours / 24:g} days"


def generate(snapshot: dict) -> str:
    """Assemble the note. Plain text, fixed sections, no invention."""
    lines: List[str] = []
    assessment = snapshot.get("assessment") or {}
    emergency = snapshot.get("emergency") or {}
    symptoms = [s for s in snapshot.get("symptoms", []) if s["active"]]
    transcript = snapshot.get("transcript", [])

    lines.append("AI-GENERATED DRAFT -- REVIEW AND EDIT BEFORE FILING")
    lines.append(f"Session {snapshot.get('session_id', '')}   "
                 f"status {snapshot.get('status', '').upper()}")

    # -- chief concern -----------------------------------------------------
    lines += _rule("CHIEF CONCERN")
    if transcript:
        lines.append(f'  Patient\'s own words: "{transcript[0]["text"]}"')
    else:
        lines.append("  Nothing recorded.")
    for concern in snapshot.get("concerns", []):
        lines.append(f"  Patient {concern.get('label', concern.get('concern'))}"
                     f" -- their account, not a diagnosis.")

    demo = snapshot.get("demographics", {})
    lines.append(f"  {demo.get('age_years', '?')} year old {demo.get('sex', '')}, "
                 f"history on file: {demo.get('history_tier', 'unknown')}")

    # -- symptoms ----------------------------------------------------------
    lines += _rule("SYMPTOMS")
    if not symptoms:
        lines.append("  None extracted.")
    for entry in sorted(symptoms, key=lambda s: s["first_at_second"]):
        bits = [entry["normalised"]]
        if entry["severity"] is not None:
            mark = " (nurse)" if entry["overridden"] else ""
            bits.append(f"severity {entry['severity']}/10{mark}")
        if entry["onset"]:
            bits.append(f"onset {entry['onset']}")
        if entry["duration_hours"] is not None:
            bits.append(_duration(entry["duration_hours"]))
        if entry["progression"]:
            bits.append(entry["progression"])
        if entry["laterality"]:
            bits.append(entry["laterality"])
        if entry["uncertain"]:
            bits.append("patient uncertain")
        lines.append(f"  - {', '.join(b for b in bits if b)}")
        if entry["said"]:
            lines.append(f'      said: "{entry["said"]}"')
        if entry["added_by"]:
            lines.append(f"      added by {entry['added_by']}")

    denials = [d for d in snapshot.get("denials", [])]
    if denials:
        lines.append("")
        lines.append("  Explicitly denied (recorded, never subtracted):")
        for entry in denials:
            lines.append(f"    - {entry['normalised']}")

    # -- timeline ----------------------------------------------------------
    lines += _rule("TIMELINE")
    interesting = {"speech", "symptom", "visual", "audio", "emergency",
                   "emergency_trigger", "band", "contradiction", "override",
                   "question", "concern"}
    for event in snapshot.get("timeline", []):
        if event["kind"] not in interesting:
            continue
        lines.append(f"  {event['at_clock']}  {event['at_second']:>6.1f}s  "
                     f"{event['kind']:<18} {event['text'][:72]}")

    # -- multimodal --------------------------------------------------------
    lines += _rule("MULTIMODAL OBSERVATIONS")
    visual = snapshot.get("visual_observations", [])
    audio = snapshot.get("audio_observations", [])
    if not visual and not audio:
        lines.append("  None recorded.")
    for entry in visual:
        lines.append(f"  [{entry['id']}] {entry['at_clock']}  visual: "
                     f"{entry['description']}  ({entry['status']})")
        if entry["nurse_note"]:
            lines.append(f"      nurse: {entry['nurse_note']}")
    for entry in audio:
        lines.append(f"  [{entry['id']}] {entry['at_clock']}  audio: "
                     f"{entry['description']}  ({entry['status']})")
        if entry["nurse_note"]:
            lines.append(f"      nurse: {entry['nurse_note']}")

    for flag in snapshot.get("review_flags", []):
        lines.append("")
        lines.append(f"  REVIEW FLAG {flag['at_clock']}  {flag['why']}")
        lines.append(f'      patient said: "{flag["statement"]}"')
        lines.append(f"      observed: "
                     f"{'visual' if flag['visual'] else ''}"
                     f"{' and ' if flag['visual'] and flag['audio'] else ''}"
                     f"{'audio' if flag['audio'] else ''} disagree")
        lines.append("      Not an emergency and not treated as one. Both "
                     "signals are kept and")
        lines.append("      risk was not lowered on the statement.")
        if flag["nurse_note"]:
            lines.append(f"      nurse: {flag['nurse_note']}")

    conflicts = [e for e in snapshot.get("timeline", [])
                 if e["kind"] in ("contradiction", "conflict")]
    if conflicts:
        lines.append("")
        lines.append("  Contradictions between what was said and what was observed:")
        for event in conflicts:
            lines.append(f"    {event['at_clock']}  {event['text']}")
        lines.append("    Both signals are preserved. Neither was discarded and")
        lines.append("    risk was not lowered on the patient's statement.")

    # -- risk --------------------------------------------------------------
    lines += _rule("RISK AND ESCALATION")
    if assessment:
        lines.append(f"  Score {assessment.get('risk_score')}/100   "
                     f"band {assessment.get('band_code')} "
                     f"{assessment.get('band_word')}   "
                     f"confidence {assessment.get('confidence_pct')}%")
        plausible = assessment.get("plausible_bands") or []
        if len(plausible) > 1:
            lines.append(f"  Plausible bands: {', '.join(plausible)} -- the score "
                         f"is close enough to a boundary that the band is not settled.")
        for rule in assessment.get("safety_rules", []):
            lines.append(f"  Safety rule: {rule}")
        for driver in assessment.get("uncertainty_drivers", []):
            lines.append(f"  Uncertainty: {driver}")
    else:
        lines.append("  No assessment computed.")

    if emergency.get("active") or emergency.get("triggers"):
        lines.append("")
        lines.append(f"  EMERGENCY {'ACTIVE' if emergency.get('active') else 'STOOD DOWN'}"
                     f"  declared {emergency.get('declared_at', '')}")
        for trigger in emergency.get("triggers", []):
            mark = " " if trigger["active"] else "x"
            lines.append(f"    [{mark}] {trigger['at_clock']} "
                         f"{trigger['trigger_id']} -- {trigger['why']}")
            if trigger["evidence"]:
                lines.append(f'        heard: "{trigger["evidence"]}"')
            if trigger["dismissed_by"]:
                lines.append(f"        dismissed by {trigger['dismissed_by']}: "
                             f"{trigger['dismiss_reason']}")

    # -- statements --------------------------------------------------------
    lines += _rule("IMPORTANT STATEMENTS, VERBATIM")
    quoted = 0
    for event in snapshot.get("timeline", []):
        if event["kind"] == "emergency_trigger" and event["detail"].get("evidence"):
            lines.append(f'  {event["at_clock"]}  "{event["detail"]["evidence"]}"')
            quoted += 1
    if not quoted and transcript:
        for fragment in transcript[:3]:
            lines.append(f'  {fragment["at_clock"]}  "{fragment["text"]}"')

    # -- review ------------------------------------------------------------
    lines += _rule("ITEMS REQUIRING NURSE REVIEW")
    todo: List[str] = []
    if emergency.get("active") and not emergency.get("acknowledged_by"):
        todo.append("Emergency alert is unacknowledged.")
    for flag in snapshot.get("review_flags", []):
        if flag["status"] == "unreviewed":
            todo.append(f"Unreviewed contradiction at {flag['at_clock']}: the "
                        f"patient said they were fine while observations "
                        f"disagreed.")
    for entry in visual + audio:
        if entry["status"] == "unreviewed":
            todo.append(f"Observation {entry['id']} unreviewed: "
                        f"{entry['description']}")
    if snapshot.get("baseline_hints"):
        hints = ", ".join(h.get("hint", "") for h in snapshot["baseline_hints"])
        todo.append(f"Patient's own words mention {hints}. Confirm whether any "
                    f"facial or physical difference is pre-existing.")
    if snapshot.get("degraded"):
        todo.append("Language extraction ran on the fallback matcher. Unusual "
                    "phrasings may have been missed; re-read the transcript.")
    missing = (assessment.get("missing_fields") or []) if assessment else []
    if missing:
        todo.append(f"Not measured: {', '.join(missing)}. Missing data lowered "
                    f"confidence; it did not lower risk.")
    if not snapshot.get("routine_questions_allowed"):
        todo.append("Routine questioning was stopped by the emergency gate. "
                    "History is incomplete by design.")
    for entry in symptoms:
        if entry["uncertain"]:
            todo.append(f"Patient was uncertain about {entry['normalised']}.")
    if not todo:
        todo.append("Nothing outstanding.")
    for item in todo:
        lines.append(f"  - {item}")

    lines.append("")
    lines.append("-" * 68)
    lines.append("SIMULATED PROTOTYPE. Synthetic thresholds, no clinical")
    lines.append("validation. Assistive triage support, not a diagnosis and not")
    lines.append("a disposition. The nurse is the clinical authority.")
    return "\n".join(lines)
