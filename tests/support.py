"""
tests/support.py
================
Shared fixtures. Deliberately thin.

Every helper here builds the SAME objects scripts/run_triage.py builds, through
the same public entry points. A test harness with its own construction path is
a harness that can pass while the product is broken, and that failure is
particularly easy to arrange in a pipeline with this many stages.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import List

from core.config import HospitalConfig
from core.patient_loader import load_patient, load_patients, patients_demonstrating
from core.questions import QuestionEngine
from core.risk_engine import RiskEngine
from core.schema import Patient

DEFAULT_PROFILE = "medium_ed"


def hospital(profile: str = DEFAULT_PROFILE) -> HospitalConfig:
    return HospitalConfig.load(profile)


def engine(profile: str = DEFAULT_PROFILE) -> RiskEngine:
    return RiskEngine(hospital(profile))


def questions(eng=None) -> QuestionEngine:
    return QuestionEngine(eng or engine())


def roster() -> List[Patient]:
    return load_patients()


def tagged(tag: str) -> List[Patient]:
    """
    Patients authored to demonstrate a named property.

    Tests read against these rather than against hard-coded IDs, so they say
    what they mean: `for p in tagged("no_false_emergency")` survives somebody
    renumbering the roster, and it fails loudly if the scenario it depends on
    is deleted. The loader has carried this function since Phase 2 for exactly
    this purpose.
    """
    found = patients_demonstrating(tag)
    if not found:
        raise AssertionError(
            f"no patient in the roster is tagged '{tag}'. The scenario this "
            f"test depends on has been removed or renamed.")
    return found


def has_teeth(fn):
    """
    Mark a test that deliberately breaks an invariant to prove the CHECK works.

    Several properties in this project are enforced by the absence of a code
    path. Asserting that an impossible thing did not happen passes on an empty
    function, so the central claims carry a companion test that plants a
    violation and confirms it is caught. The runner prints these separately,
    because they are the tests that make the others mean something.
    """
    fn.has_teeth = True
    return fn


class ClaimTest(unittest.TestCase):
    """
    Base class carrying the claim text.

    `claim` is the sentence from the README that this file defends. The runner
    prints it as a heading, which is what makes the suite readable as an
    argument rather than as a list of function names.
    """

    claim: str = ""

    def assertNeverLower(self, before, after, who: str):
        self.assertGreaterEqual(
            int(after), int(before),
            f"{who}: band went DOWN ({before.word} -> {after.word}) "
            f"through an automated path")
