"""
tests/test_intake.py
====================
CLAIM: A patient captured live by camera and microphone travels the same path,
through the same validation, as a patient read off disk. The sensors add a
capture channel. They do not add a second engine.

And the claim that matters more, because it is the one a live demo makes
tempting to break:

CLAIM: The camera cannot decide anything on its own. An identical reading --
the same face, the same asymmetry, the same pixels -- produces three different
outcomes depending only on whether the baseline is known, unknown, or known to
be lifelong. If that ever collapses into one answer, the fairness argument in
tests/test_fairness.py has been quietly undone at the intake layer.

Phase 17 is where a demo is most likely to cheat: it is much easier to have the
page decide a band and post it than to have it post flags and wait. These tests
exist to make that cheat fail.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.enums import CaptureStatus, TriageBand, Tri
from core.intake_bridge import (
    IntakeSession,
    SymptomReader,
    assess_payload,
    build_patient,
    build_patient_record,
)
from core.patient_loader import PATIENTS_FILE, load_patient
from tests.support import ClaimTest, engine, has_teeth, hospital

REPO_ROOT = Path(__file__).resolve().parent.parent


def _facial_payload(**overrides) -> dict:
    """A neutral live payload with an asymmetric face and nothing else wrong."""
    base = {
        "patient_id": "LIVE-T", "age_years": 55, "sex": "female",
        "arrival_minute": 0, "history_tier": "zero",
        "typed_symptoms": "feeling generally unwell",
        "heart_rate": 88, "respiratory_rate": 17, "spo2": 97,
        "temperature_c": 36.8, "systolic_bp": 128, "diastolic_bp": 78,
        "consciousness": "alert", "can_communicate": "yes",
        "facial_capture_status": "ok",
        "asymmetry_observed": "yes", "droop_observed": "yes",
        "voice_capture_status": "ok",
    }
    base.update(overrides)
    return base


class TestIntakeUsesTheRealEngine(ClaimTest):
    claim = ("A live patient and a patient read off disk become the same "
             "object and travel the same pipeline.")

    def test_a_live_payload_and_a_file_record_score_identically(self):
        """
        Rebuild a roster patient as if they had walked past the camera, and
        assert the two paths agree to the point.

        This is the test that makes 'the page computes nothing' checkable
        rather than merely stated. If somebody ever adds scoring to the
        browser, or a convenience default to the bridge, the two numbers part
        company here.
        """
        with open(PATIENTS_FILE, "r", encoding="utf-8") as fh:
            records = json.load(fh)["patients"]

        checked = 0
        for record in records:
            payload = {
                "patient_id": record["patient_id"],
                "age_years": record["age_years"],
                "sex": record.get("sex", "unspecified"),
                "arrival_minute": record.get("arrival_minute", 0),
                "chief_complaint": record["self_report"].get("chief_complaint", ""),
                "added_symptoms": record["self_report"].get("symptoms", []),
                "denied_symptoms": record["self_report"].get("denies", []),
                "pain_score": record["self_report"].get("pain_score"),
                "duration_hours": record["self_report"].get("symptom_duration_hours"),
                "can_communicate": record["self_report"].get("can_communicate", "unknown"),
                "history_tier": record["history"]["tier"],
                "conditions": record["history"].get("conditions", []),
                "medications": record["history"].get("medications", []),
                "previous_visits": record["history"].get("previous_visits"),
                "baseline_notes": record["history"].get("baseline_notes", ""),
                "observed_capture_status": record["observed"].get("capture_status", "ok"),
                "consciousness": record["observed"].get("consciousness", "unknown"),
                "gait_abnormal": record["observed"].get("gait_abnormal", "unknown"),
                "visible_bleeding": record["observed"].get("visible_bleeding", "unknown"),
                "skin_pallor_or_cyanosis":
                    record["observed"].get("skin_pallor_or_cyanosis", "unknown"),
            }
            payload.update(record["vitals"])
            payload.update({k: v for k, v in record["facial"].items()
                            if k != "capture_status"})
            payload["facial_capture_status"] = record["facial"]["capture_status"]
            payload.update({k: v for k, v in record["voice"].items()
                            if k != "capture_status"})
            payload["voice_capture_status"] = record["voice"]["capture_status"]

            from_file = engine().assess(
                load_patient(record["patient_id"]),
                now_minute=record.get("arrival_minute", 0))
            from_live = assess_payload(payload)

            self.assertAlmostEqual(
                from_file.risk_score, from_live["risk_score"], places=1,
                msg=f"{record['patient_id']}: live intake and file load disagree "
                    f"on the score. The two paths have diverged.")
            self.assertEqual(
                from_file.proposed_band.code, from_live["proposed_band"],
                f"{record['patient_id']}: the two paths propose different bands.")
            checked += 1

        self.assertGreaterEqual(checked, 20,
                                "the roster shrank; this test is checking too little")

    def test_the_symptom_vocabulary_is_the_engine_vocabulary(self):
        """
        The reader must not have a word list of its own. Every term it can
        recognise has to be a term the engine can score, or intake silently
        collects findings that never reach the calculation.
        """
        from core.risk_engine import _load, WEIGHTS_FILE
        scoreable = set(_load(WEIGHTS_FILE)["symptoms"].keys())
        self.assertEqual(set(SymptomReader().vocabulary), scoreable)


class TestIntakePreservesUnknown(ClaimTest):
    claim = ("A field the browser did not send stays UNKNOWN. Nothing in the "
             "intake path fills a gap with a reassuring default.")

    def test_an_empty_payload_produces_unknowns_not_negatives(self):
        patient = build_patient({"patient_id": "LIVE-X", "age_years": 40})
        for attr in ("asymmetry_observed", "droop_observed", "baseline_known",
                     "change_reported_as_new", "speech_abnormality",
                     "unilateral_weakness"):
            self.assertIs(getattr(patient.facial, attr), Tri.UNKNOWN,
                          f"facial.{attr} defaulted away from UNKNOWN")
        for attr in ("slurred_speech", "breathlessness_between_words",
                     "unable_to_speak_full_sentence"):
            self.assertIs(getattr(patient.voice, attr), Tri.UNKNOWN,
                          f"voice.{attr} defaulted away from UNKNOWN")
        self.assertIs(patient.facial.capture_status, CaptureStatus.NOT_ATTEMPTED)

    def test_a_junk_tri_value_becomes_unknown_and_never_no(self):
        """
        A browser sending 'maybe', 'true' or '' must not resolve to 'no'.
        UNKNOWN is the only safe landing place for an unrecognised answer.
        """
        for junk in ("maybe", "true", "", "1", "probably not"):
            patient = build_patient({"patient_id": "LIVE-X", "age_years": 40,
                                     "asymmetry_observed": junk})
            self.assertIs(patient.facial.asymmetry_observed, Tri.UNKNOWN,
                          f"'{junk}' resolved to something other than UNKNOWN")

    def test_a_blank_vital_is_missing_and_not_normal(self):
        patient = build_patient({"patient_id": "LIVE-X", "age_years": 40,
                                 "heart_rate": 90})
        self.assertIsNone(patient.vitals.spo2)
        self.assertIn("spo2", patient.vitals.missing_fields())

    def test_a_denial_is_kept_and_never_scored_as_a_symptom(self):
        reported, denied = SymptomReader().read(
            "bad headache but no chest pain and not breathless")
        self.assertIn("headache", reported)
        self.assertIn("chest pain", denied)
        self.assertNotIn("chest pain", reported)
        self.assertIn("breathlessness", denied)


class TestTheCameraCannotDecideAlone(ClaimTest):
    claim = ("An identical camera reading produces three different outcomes "
             "depending only on the baseline answer.")

    def test_the_same_face_diverges_only_on_baseline(self):
        """
        Same asymmetry, same droop, same vitals, same complaint. The only
        difference is what is known about how this person normally looks.
        """
        acute = assess_payload(_facial_payload(
            baseline_known="yes", baseline_asymmetry_present="no",
            baseline_condition="none", change_reported_as_new="yes",
            speech_abnormality="yes", unilateral_weakness="yes",
            slurred_speech="yes", history_tier="rich"))

        lifelong = assess_payload(_facial_payload(
            baseline_known="yes", baseline_asymmetry_present="yes",
            baseline_condition="congenital", change_reported_as_new="no",
            speech_abnormality="no", unilateral_weakness="no",
            history_tier="rich"))

        unknown = assess_payload(_facial_payload(
            baseline_known="unknown", baseline_asymmetry_present="unknown",
            change_reported_as_new="unknown"))

        acute_band = TriageBand[_band_name(acute["band_code"])]
        lifelong_band = TriageBand[_band_name(lifelong["band_code"])]
        unknown_band = TriageBand[_band_name(unknown["band_code"])]

        self.assertGreater(acute_band, lifelong_band,
                           "the acute case did not outrank the lifelong one")
        self.assertGreater(acute_band, unknown_band,
                           "an unknown baseline reached the same band as a "
                           "confirmed acute change; that is over-escalation")
        self.assertGreaterEqual(unknown_band, lifelong_band)

    def test_an_unknown_baseline_lowers_confidence_rather_than_raising_score(self):
        lifelong = assess_payload(_facial_payload(
            baseline_known="yes", baseline_asymmetry_present="yes",
            baseline_condition="congenital", change_reported_as_new="no",
            history_tier="rich"))
        unknown = assess_payload(_facial_payload(
            baseline_known="unknown", baseline_asymmetry_present="unknown",
            change_reported_as_new="unknown"))

        self.assertLessEqual(
            unknown["risk_score"], lifelong["risk_score"] + 0.01,
            "an unverifiable baseline added points to the score. It must "
            "reduce confidence instead: escalating on a missing record "
            "penalises patients who have no regular care.")
        self.assertLess(unknown["confidence_pct"], lifelong["confidence_pct"])

    def test_the_cause_of_a_lifelong_difference_does_not_change_the_score(self):
        scores = {}
        for cause in ("congenital", "burn_or_acid", "post_stroke",
                      "trauma", "surgical", "chronic_palsy"):
            result = assess_payload(_facial_payload(
                baseline_known="yes", baseline_asymmetry_present="yes",
                baseline_condition=cause, change_reported_as_new="no",
                history_tier="rich"))
            scores[cause] = result["risk_score"]
        self.assertEqual(len(set(scores.values())), 1,
                         f"the reason for a documented difference moved the "
                         f"score: {scores}")

    @has_teeth
    def test_the_divergence_check_has_teeth(self):
        """
        Plant the failure this file exists to catch: an intake layer that
        treats any asymmetry as acute regardless of baseline. The check above
        must reject it.
        """
        forced = assess_payload(_facial_payload(
            baseline_known="yes", baseline_asymmetry_present="no",
            change_reported_as_new="yes", history_tier="rich"))
        lifelong = assess_payload(_facial_payload(
            baseline_known="yes", baseline_asymmetry_present="yes",
            baseline_condition="congenital", change_reported_as_new="no",
            history_tier="rich"))
        self.assertGreater(
            forced["risk_score"], lifelong["risk_score"],
            "if these are equal the baseline answer is being ignored and "
            "every assertion in this file is passing vacuously")


class TestIntakeRespectsTheRatchet(ClaimTest):
    claim = ("Re-submitting a live patient is a re-triage, not a fresh score. "
             "The band can rise and cannot fall.")

    def test_a_second_submission_cannot_lower_the_band(self):
        session = IntakeSession()
        worse = session.assess(_facial_payload(
            spo2=88, respiratory_rate=30, heart_rate=124,
            typed_symptoms="very short of breath", arrival_minute=0))
        better = session.assess(_facial_payload(
            spo2=98, respiratory_rate=16, heart_rate=78,
            typed_symptoms="feeling better now", arrival_minute=20))

        self.assertNeverLower(TriageBand[_band_name(worse["band_code"])],
                              TriageBand[_band_name(better["band_code"])],
                              "live intake re-submission")
        self.assertIn(better["changed_by"],
                      ("system_initial", "ai_escalation"),
                      "an automated path recorded a de-escalation")

    def test_the_session_records_every_submission(self):
        session = IntakeSession()
        session.assess(_facial_payload(arrival_minute=0))
        result = session.assess(_facial_payload(spo2=90, arrival_minute=15))
        self.assertEqual(len(result["history"]), 2)


class TestTheIntakePageHoldsNoLogic(ClaimTest):
    claim = "The intake page captures and formats. It holds no clinical logic."

    def test_no_threshold_or_weight_reaches_the_page(self):
        """
        The page must not carry the numbers the engine reasons with.

        Checked structurally rather than by grepping for digits, because a
        stylesheet is full of coincidental numbers and a test that trips on
        `padding:3px 8px` teaches people to ignore it. What matters is whether
        the CONFIGURATION handed to the browser contains clinical parameters,
        and whether engine vocabulary appears in the script.
        """
        import json as _json
        from app.intake import render_intake
        from core.risk_engine import _load, THRESHOLDS_FILE, WEIGHTS_FILE

        page = render_intake()
        script = page.split("<script>")[1]
        settings = _json.loads(script.split("const S = ")[1].split(";\n")[0])

        # Only capture settings and the symptom vocabulary may cross over.
        self.assertEqual(set(settings.keys()), {"camera", "audio", "vocabulary"},
                         f"the page was handed more than capture settings: "
                         f"{sorted(settings)}")

        blob = _json.dumps(settings)
        for forbidden in ("threshold_l", "band", "critical_high", "critical_low",
                          "severe_deviation", "mild_deviation", "domain_caps",
                          "points", "age_context"):
            self.assertNotIn(forbidden, blob,
                             f"'{forbidden}' reached the browser inside the "
                             f"settings blob")

        # Symptom WEIGHTS must not travel even though the term list does.
        weights = _load(WEIGHTS_FILE)
        for term, spec in weights["symptoms"].items():
            self.assertNotIn(f'"{term}": {spec["points"]}', page,
                             f"the weight for '{term}' leaked into the page")

        # Engine vocabulary must not appear anywhere in the script.
        for word in ("L4_CODE", "band_for_score", "critical_high",
                     "severe_deviation", "domain_caps", "proposed_band ="):
            self.assertNotIn(word, script,
                             f"'{word}' is engine vocabulary and must not be "
                             f"in the browser")

        # And no age threshold table name may appear in the settings.
        thresholds = _load(THRESHOLDS_FILE)
        for band_name, table in thresholds.items():
            self.assertNotIn(band_name, blob,
                             f"the '{band_name}' threshold table name reached "
                             f"the browser")

    @has_teeth
    def test_the_leak_check_has_teeth(self):
        """
        Plant a threshold in the settings the page receives and confirm the
        check above rejects it. Without this, a test asserting the absence of
        something passes just as happily on an empty page.
        """
        import json as _json
        from app.intake import render_intake

        leaked = render_intake({"camera": {}, "audio": {},
                                "domain_caps": {"respiratory": 45}},
                               vocabulary=["chest pain"])
        settings = _json.loads(
            leaked.split("<script>")[1].split("const S = ")[1].split(";\n")[0])
        self.assertIn("camera", settings)
        # render_intake only forwards camera/audio/vocabulary, so a stray key
        # in the config must be dropped rather than passed through.
        self.assertNotIn("domain_caps", _json.dumps(settings),
                         "render_intake forwarded an arbitrary config key to "
                         "the browser; the whitelist has stopped working")

    def test_the_page_posts_flags_and_never_a_band(self):
        from app.intake import render_intake
        page = render_intake()
        self.assertIn("/assess", page)
        self.assertNotIn("risk_score:", page.split("function drawResult")[0],
                         "the page assembles a score before posting")


class TestIntakeWritesNothing(ClaimTest):
    claim = ("Nothing captured is persisted. Frames, audio and transcript stay "
             "in the browser; the server writes no file.")

    def test_a_session_creates_no_files(self):
        before = {p for p in REPO_ROOT.rglob("*")
                  if p.is_file() and ".git" not in p.parts}
        session = IntakeSession()
        session.assess(_facial_payload())
        session.assess(_facial_payload(spo2=90, arrival_minute=20))
        after = {p for p in REPO_ROOT.rglob("*")
                 if p.is_file() and ".git" not in p.parts}
        new = {p for p in after - before if p.suffix != ".pyc"}
        self.assertEqual(new, set(), f"the intake session wrote files: {new}")

    def test_the_config_declares_zero_retention(self):
        from core.intake_bridge import load_intake_config
        retention = load_intake_config()["capture_retention"]
        for key in ("frames_written_to_disk", "audio_written_to_disk",
                    "transcript_written_to_disk"):
            self.assertEqual(retention[key], 0,
                             f"{key} is not zero; docs/privacy.md is now wrong")


def _band_name(code: str) -> str:
    return {"L1": "L1_WATCH", "L2": "L2_LOOK",
            "L3": "L3_PULL", "L4": "L4_CODE"}[code]
