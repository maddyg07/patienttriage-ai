"""
scripts/run_scenarios.py
========================
Runs the seven scenarios from the brief against the live pipeline and prints
what each one did.

    python -m scripts.run_scenarios
    python -m scripts.run_scenarios --provider claude    # needs a key
    python -m scripts.run_scenarios emergency            # one scenario

Every scenario is a conversation, not a single utterance, because the thing
being tested is what happens BETWEEN turns: the score moving, the gate firing,
questioning stopping.

Defaults to the deterministic provider so the output is the same on every
machine. With a key exported, `--provider claude` runs the same scenarios
through the model and the difference in what gets recognised is the honest
argument for the model path.
"""

from __future__ import annotations

import sys

from core.ai import get_provider
from core.notes import generate
from core.session import ClinicSession

SCENARIOS = {
    "normal": {
        "why": "an ordinary complaint should stay ordinary",
        "expect": "low band, no emergency, questioning continues",
        "demographics": {"age_years": 29, "sex": "female", "history_tier": "partial"},
        "observations": {"heart_rate": 76, "respiratory_rate": 15, "spo2": 99,
                         "temperature_c": 36.8, "consciousness": "alert"},
        "turns": [(4, "I've had a mild headache since this morning"),
                  (14, "it's not too bad, maybe a three out of ten")],
    },
    "ambiguous": {
        "why": "vague speech should lower confidence, not manufacture findings",
        "expect": "wide plausible band set, low confidence, no invented symptom",
        "demographics": {"age_years": 46, "sex": "female", "history_tier": "zero"},
        "observations": {"heart_rate": 96, "spo2": 96, "consciousness": "alert"},
        "turns": [(5, "I don't know, I just feel really weird and weak"),
                  (18, "I can't explain it, something is just off")],
    },
    "colloquial": {
        "why": "nobody says 'headache' when it is bad",
        "expect": "headache recognised from ordinary speech",
        "demographics": {"age_years": 33, "sex": "male", "history_tier": "partial"},
        "observations": {"heart_rate": 88, "spo2": 98, "consciousness": "alert"},
        "turns": [(3, "my head is absolutely killing me"),
                  (12, "it started out of nowhere about an hour ago")],
    },
    "multi": {
        "why": "several symptoms in one breath",
        "expect": "all three extracted with their own entries",
        "demographics": {"age_years": 52, "sex": "female", "history_tier": "partial"},
        "observations": {"heart_rate": 102, "spo2": 97, "temperature_c": 37.6,
                         "consciousness": "alert"},
        "turns": [(6, "I've been feeling nauseous, my stomach hurts, and I "
                      "feel dizzy whenever I stand")],
    },
    "contradiction": {
        "why": "the patient says one thing and the observations say another",
        "expect": "both preserved, contradiction logged, risk not lowered",
        "demographics": {"age_years": 61, "sex": "male", "history_tier": "rich"},
        "observations": {"heart_rate": 104, "respiratory_rate": 24, "spo2": 93,
                         "consciousness": "alert", "skin_pallor_or_cyanosis": "yes"},
        "visual": [(38, "distress", "apparent facial discomfort, grimacing")],
        "audio": [(39, "distress", "strained voice, effortful speech")],
        "turns": [(20, "my wife made me come, there's nothing wrong with me"),
                  (41, "I'm completely fine")],
    },
    "emergency": {
        "why": "the case the gate exists for",
        "expect": "EMERGENCY on the utterance, questioning stops, nurse notified",
        "demographics": {"age_years": 58, "sex": "male", "history_tier": "partial"},
        "observations": {"heart_rate": 118, "respiratory_rate": 26, "spo2": 91,
                         "consciousness": "alert", "skin_pallor_or_cyanosis": "yes"},
        "turns": [(8, "my chest feels heavy and I've been sweating a lot"),
                  (24, "I have severe chest pressure, I can't breathe properly, "
                       "and I feel like I'm going to pass out"),
                  (34, "actually I feel a bit better now")],
    },
    "visual": {
        "why": "the camera saw something that is not a diagnosis",
        "expect": "logged as an observation, unreviewed, awaiting a nurse",
        "demographics": {"age_years": 31, "sex": "female", "history_tier": "rich"},
        "observations": {"heart_rate": 88, "spo2": 98, "consciousness": "alert"},
        "visual": [(12, "scar", "visible linear scar on the left forearm"),
                   (30, "asymmetry", "facial asymmetry candidate (possible)")],
        "turns": [(6, "I've had a migraine since last night"),
                  (33, "my face has looked like this since the accident years ago")],
    },
}


def rule(title):
    print("\n" + "=" * 76)
    print(title)
    print("=" * 76)


def run(name: str, spec: dict, provider) -> None:
    rule(f"{name.upper()}  --  {spec['why']}")
    print(f"  expected: {spec['expect']}\n")

    session = ClinicSession(f"SCN-{name}", provider)
    session.set_demographics(**spec.get("demographics", {}))
    session.set_observations(**spec.get("observations", {}))

    feed = ([("visual", t, k, d) for t, k, d in spec.get("visual", [])]
            + [("audio", t, k, d) for t, k, d in spec.get("audio", [])]
            + [("say", t, "", text) for t, text in spec["turns"]])
    feed.sort(key=lambda item: item[1])

    for kind, at, obs_kind, text in feed:
        if kind == "visual":
            session.observe_visual(obs_kind, text, at)
            print(f"  {at:>5.0f}s  [camera]  {text}")
        elif kind == "audio":
            session.observe_audio(obs_kind, text, at)
            print(f"  {at:>5.0f}s  [mic]     {text}")
        else:
            allowed = session.routine_questions_allowed
            session.hear(text, at)
            a = session.last_result
            print(f"  {at:>5.0f}s  [patient] \"{text}\"")
            print(f"            -> {a.get('band_code')} {a.get('band_word')}"
                  f"  risk {a.get('risk_score')}"
                  f"  confidence {a.get('confidence_pct')}%"
                  f"  status {session.status.upper()}")
            if session.emergency.active and allowed:
                print("            *** EMERGENCY DECLARED, QUESTIONING STOPPED")

    print()
    active = [s for s in session.ledger.values() if s.active]
    print(f"  symptoms   {', '.join(s.normalised for s in active) or 'none'}")
    if session.denials:
        print(f"  denied     {', '.join(session.denials)}")
    if session.concerns:
        print(f"  concerns   {', '.join(c.get('concern', '') for c in session.concerns)}")
    if session.baseline_hints:
        print(f"  baseline   {', '.join(h.get('hint', '') for h in session.baseline_hints)}"
              f"   <- the patient's own words may explain a physical difference")

    for trigger in session.emergency.triggers:
        print(f"  TRIGGER    {trigger.trigger_id:<24} {trigger.why}")
        if trigger.evidence:
            print(f"             heard: \"{trigger.evidence[:60]}\"")

    for flag in session.review_flags:
        print(f"  REVIEW     {flag['why']}")
        print(f"             patient said: \"{flag['statement']}\"")
        print("             flagged for the nurse, NOT escalated to emergency;")
        print("             both signals kept and questioning continues")

    unreviewed = [o for o in session.visual_observations + session.audio_observations
                  if o["status"] == "unreviewed"]
    for obs in unreviewed:
        print(f"  OBSERVED   [{obs['id']}] {obs['description']}  (awaiting nurse)")

    print(f"  questions  {'allowed' if session.routine_questions_allowed else 'STOPPED'}")
    if session.degraded:
        print("  provider   DEGRADED -- the fallback matcher served this")


def main():
    args = sys.argv[1:]
    prefer = args[args.index("--provider") + 1] if "--provider" in args else "local"
    wanted = [a for a in args if not a.startswith("--") and a in SCENARIOS]

    provider = get_provider(prefer)
    rule("SCENARIO RUN")
    print(f"  language provider: {provider.describe()}")
    if provider.kind != "model":
        print("  Running on the deterministic matcher, so results are identical")
        print("  on every machine. Export ANTHROPIC_API_KEY and pass")
        print("  --provider claude to run the same scenarios through the model.")

    for name in (wanted or SCENARIOS):
        run(name, SCENARIOS[name], provider)

    rule("NOTES PRODUCED FOR THE EMERGENCY SCENARIO")
    session = ClinicSession("SCN-notes", provider)
    spec = SCENARIOS["emergency"]
    session.set_demographics(**spec["demographics"])
    session.set_observations(**spec["observations"])
    for at, text in spec["turns"]:
        session.hear(text, at)
    print(generate(session.snapshot()))


if __name__ == "__main__":
    main()
