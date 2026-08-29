"""
scripts/run_tests.py
====================
Phase 15. Runs the safety argument and prints it as an argument.

    python -m scripts.run_tests            # the whole suite, grouped by claim
    python -m scripts.run_tests --quiet    # just the verdict
    python -m scripts.run_tests --gaps     # only the known gaps

Plain unittest underneath, so `python -m unittest discover -s tests -t .` and
`pytest` both work unchanged. This wrapper exists for one reason: the default
output is a list of function names, and the point of this suite is that it
reads as the safety argument rather than as a coverage report.
"""

import sys
import unittest
from io import StringIO

from tests.support import ClaimTest

REPO_TESTS = "tests"


def _load():
    loader = unittest.TestLoader()
    return loader.discover(REPO_TESTS, top_level_dir=".")


def _flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _flatten(item)
        else:
            yield item


def _claim_of(test):
    cls = test.__class__
    return getattr(cls, "claim", "") or cls.__name__


def _sentence(test):
    """Turn test_a_failed_sensor_is_recorded into readable English."""
    name = test._testMethodName
    if name.startswith("test_"):
        name = name[5:]
    return name.replace("_", " ")


def rule(title):
    print("\n" + "=" * 76)
    print(title)
    print("=" * 76)


def main():
    args = sys.argv[1:]
    quiet = "--quiet" in args
    gaps_only = "--gaps" in args

    tests = list(_flatten(_load()))

    grouped = {}
    for test in tests:
        grouped.setdefault(_claim_of(test), []).append(test)

    if not quiet:
        rule("THE SAFETY ARGUMENT, RUN AS CODE")
        print("  Each block below is a CLAIM this repository makes, and the")
        print("  lines under it are what would have to be true for the claim to")
        print("  hold. The suite is organised by claim rather than by module")
        print("  because the properties do not live inside single files:")
        print("  'missing data never lowers risk' is enforced by the loader,")
        print("  the uncertainty engine, the facial module and the safety guard")
        print("  acting together, and a test of any one of them would pass")
        print("  while the property was broken.")

    total_failures = 0
    total_run = 0
    gap_count = 0

    for claim, cases in grouped.items():
        is_gap = claim.startswith("KNOWN GAPS")
        if gaps_only and not is_gap:
            continue

        suite = unittest.TestSuite(cases)
        stream = StringIO()
        result = unittest.TextTestRunner(
            stream=stream, verbosity=0).run(suite)

        total_run += result.testsRun
        total_failures += len(result.failures) + len(result.errors)
        gap_count += len(result.expectedFailures)

        if quiet:
            continue

        failed = {t.id() for t, _ in result.failures + result.errors}
        expected = {t.id() for t, _ in result.expectedFailures}
        unexpected = {t.id() for t in result.unexpectedSuccesses}

        print()
        print(f"  {'GAP  ' if is_gap else 'CLAIM'}  {claim}")
        print("  " + "-" * 74)
        for test in cases:
            tid = test.id()
            if tid in failed:
                mark = "FAIL"
            elif tid in expected:
                mark = "gap "
            elif tid in unexpected:
                mark = "FIXED"
            else:
                mark = "ok  "
            method = getattr(test, test._testMethodName, None)
            teeth = "  [has teeth]" if getattr(method, "has_teeth", False) else ""
            print(f"    {mark}  {_sentence(test)}{teeth}")

        for test, trace in result.failures + result.errors:
            print(f"\n    FAILED: {_sentence(test)}")
            for line in trace.strip().splitlines()[-3:]:
                print(f"      {line}")

    rule("VERDICT")
    print(f"  {total_run} checks, {total_failures} failed, "
          f"{gap_count} known gaps pinned")
    print()
    if total_failures:
        print("  The safety argument does not currently hold.")
    else:
        print("  Every claim in this repository is checked and holds.")
    print()
    print("  Two things this does NOT mean, and both matter more than the")
    print("  number above.")
    print()
    print("  It is not clinical validation. These tests confirm the system")
    print("  behaves the way the README says it does. They say nothing about")
    print("  whether that behaviour is clinically correct, and every threshold")
    print("  they assert against is a simulated demonstration value.")
    print()
    print("  A passing test of an absent code path proves very little. Several")
    print("  properties here are enforced by something NOT existing -- the")
    print("  ratchet has no branch that lowers a band, AuditLog has no update")
    print("  method, nothing can mark a patient seen. Asserting that an")
    print("  impossible thing did not happen passes on an empty function. So")
    print("  the central claims carry a companion test that breaks the")
    print("  invariant deliberately and checks that the CHECK catches it.")
    print("  Those are marked [has teeth].")
    print()
    if gap_count:
        print(f"  {gap_count} known gaps are pinned rather than deleted, so")
        print("  they appear in every run and so the suite reports it if one")
        print("  is ever fixed. A suite containing only things that pass is a")
        print("  suite that has stopped looking.\n")

    return 1 if total_failures else 0


if __name__ == "__main__":
    sys.exit(main())
