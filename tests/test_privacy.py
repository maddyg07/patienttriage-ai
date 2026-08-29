"""
tests/test_privacy.py
=====================
CLAIM: There is not a single direct identifier in this repository, and the
audit log carries pseudonyms only.

CLAIM: A person can be erased without touching the audit log, and the chain
still verifies afterwards.

CLAIM: We do not claim anonymity, and nothing in the code lets us claim it by
accident.

The first of these is the load-bearing one. The erasure resolution only works
because the log never held a name in the first place, so "we do not store
names" cannot be left as a sentence in a README -- it is exactly the kind of
claim that stays true for two years and then quietly stops when somebody adds a
`patient_name` field for debugging.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.audit import AuditLog
from core.privacy import (
    DIRECT_IDENTIFIER_FIELDS,
    Exporter,
    Identity,
    IdentityVault,
    RetentionPolicy,
    SubjectNotFound,
    looks_like_a_pseudonym,
    scan_for_direct_identifiers,
)
from core.ratchet import Ratchet
from simulation.clock import SimulationClock
from tests.support import ClaimTest, engine, has_teeth, roster

REPO = Path(__file__).resolve().parent.parent


class TestNoDirectIdentifiers(ClaimTest):
    claim = ("There is not a single direct identifier in this repository, and "
             "the audit log carries pseudonyms only.")

    def test_the_patient_roster_holds_no_direct_identifiers(self):
        records = json.loads(
            (REPO / "data" / "patients.json").read_text(encoding="utf-8"))
        found = []
        for record in records["patients"]:
            found += scan_for_direct_identifiers(record, record["patient_id"])
        self.assertEqual(
            [], found,
            f"direct identifier fields present at {found}. The erasure "
            f"resolution in core/privacy.py depends on this being empty.")

    def test_every_patient_id_is_a_pseudonym(self):
        for patient in roster():
            self.assertTrue(
                looks_like_a_pseudonym(patient.patient_id),
                f"{patient.patient_id} does not look like a pseudonym")

    def test_the_audit_log_carries_pseudonyms_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = AuditLog(path=Path(tmp) / "audit.jsonl")
            clock = SimulationClock(engine(), roster(),
                                    ratchet=Ratchet(audit=log))
            clock.run()

            for entry in log.entries():
                self.assertTrue(
                    looks_like_a_pseudonym(entry.patient_id),
                    f"entry {entry.seq} names a subject directly: "
                    f"{entry.patient_id}")
                self.assertEqual(
                    [], scan_for_direct_identifiers(entry.payload),
                    f"entry {entry.seq} payload holds a direct identifier")

    @has_teeth
    def test_the_scanner_finds_an_identifier_when_one_is_planted(self):
        """
        Without this, an empty result proves nothing: it is also what a broken
        scanner returns for a record full of names.
        """
        planted = {"patient_id": "P001",
                   "self_report": {"chief_complaint": "chest pain",
                                   "patient_name": "A Real Person"},
                   "history": {"hospital_number": "12345"}}
        found = scan_for_direct_identifiers(planted)
        self.assertIn("self_report.patient_name", found)
        self.assertIn("history.hospital_number", found)

    def test_the_scanner_covers_the_obvious_field_names(self):
        for field in ("name", "date_of_birth", "hospital_number", "aadhaar"):
            self.assertIn(field, DIRECT_IDENTIFIER_FIELDS)


class TestErasureAgainstAnAppendOnlyLog(ClaimTest):
    claim = ("A person can be erased without touching the audit log, and the "
             "chain still verifies afterwards.")

    def setUp(self):
        self.engine = engine()
        self._tmp = tempfile.TemporaryDirectory()
        self.log = AuditLog(path=Path(self._tmp.name) / "audit.jsonl")
        clock = SimulationClock(self.engine, roster(),
                                ratchet=Ratchet(audit=self.log))
        clock.run()
        self.vault = IdentityVault()
        self.vault.enrol(Identity(pseudonym="P014", name="[a real person]",
                                  date_of_birth="1988-03-14"))

    def tearDown(self):
        self._tmp.cleanup()

    def test_forgetting_destroys_the_mapping(self):
        self.assertTrue(self.vault.knows("P014"))
        self.vault.forget("P014")
        self.assertFalse(self.vault.knows("P014"))
        with self.assertRaises(SubjectNotFound):
            self.vault.resolve("P014")

    def test_the_log_is_untouched_and_still_verifies(self):
        before = len(self.log.for_patient("P014"))
        self.assertGreater(before, 0)

        self.vault.forget("P014")

        self.assertEqual(before, len(self.log.for_patient("P014")))
        ok, problems = self.log.verify()
        self.assertTrue(ok, problems)

    def test_the_record_still_replays_after_an_erasure(self):
        """
        Erasure must not break the completeness property. A governance team
        asking "has this system ever lowered a band without a human" gets the
        same answer before and after somebody exercises their rights.
        """
        before = self.log.replay_bands()
        self.vault.forget("P014")
        self.assertEqual(before, self.log.replay_bands())
        self.assertEqual([], self.log.ratchet_violations())

    def test_the_vault_is_the_only_thing_with_a_delete_operation(self):
        """
        Structural. If a delete appears anywhere else, the erasure story stops
        being 'one place holds the mapping' and becomes untraceable.
        """
        offenders = []
        for path in (REPO / "core").glob("*.py"):
            if path.name == "privacy.py":
                continue
            text = path.read_text(encoding="utf-8")
            for pattern in ("def delete", "def erase", "def purge", "def forget"):
                if pattern in text:
                    offenders.append(f"{path.name}: {pattern}")
        self.assertEqual(
            [], offenders,
            f"a delete operation exists outside the vault: {offenders}")


class TestWeDoNotClaimAnonymity(ClaimTest):
    claim = ("We do not claim anonymity, and nothing in the code lets us "
             "claim it by accident.")

    def test_the_risk_statement_is_explicit_about_what_remains(self):
        text = IdentityVault.reidentification_risk().lower()
        self.assertIn("not anonymisation", text)
        self.assertIn("personal data", text)

    def test_retention_reports_that_it_enforces_nothing(self):
        """
        A method rather than a constant, so nothing can quietly assume True.
        If a purge job is ever built, this test is what says the docs need
        changing too.
        """
        self.assertFalse(RetentionPolicy.enforced())

    def test_every_retained_artefact_has_a_limit(self):
        policy = RetentionPolicy()
        for artefact in ("identity_map", "audit_log", "assessment_detail"):
            self.assertGreater(policy.limit_for(artefact), 0)

    def test_an_artefact_without_a_limit_is_an_error_not_a_default(self):
        """Adding data with no limit is how retention becomes permanent."""
        with self.assertRaises(KeyError):
            RetentionPolicy().limit_for("something_nobody_thought_about")

    def test_the_exporter_refuses_rather_than_stripping(self):
        """
        A record containing a direct identifier is a bug upstream. Silently
        removing it would hide the bug and leave the next export to chance.
        """
        with self.assertRaises(ValueError):
            Exporter().export_patient(
                {"patient_id": "P001", "age_years": 40,
                 "history": {"hospital_number": "12345"}})

    def test_the_oldest_ages_are_coarsened(self):
        exported = Exporter().export_patient({"patient_id": "P001",
                                              "age_years": 97})
        self.assertEqual("90+", exported["age_years"])

    def test_lawful_basis_is_recorded_as_unestablished(self):
        """
        Deliberately not a plausible-sounding placeholder. This test exists so
        that filling it in requires deleting a test, which is a conversation
        rather than an edit.
        """
        cfg = json.loads(
            (REPO / "data" / "privacy_config.json").read_text(encoding="utf-8"))
        self.assertEqual("NOT ESTABLISHED", cfg["lawful_basis"]["status"])
