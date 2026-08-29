"""
tests/
======
The safety argument, expressed as code.

HOW THIS SUITE IS ORGANISED, AND WHY IT IS NOT ORGANISED BY MODULE
------------------------------------------------------------------
A conventional suite mirrors the source tree: test_ratchet.py tests
core/ratchet.py. That arrangement optimises for finding the test that covers a
function, which is the right thing when the question is "did I break the code?"

It is the wrong thing here. The question this project has to answer is "is the
claim true?", and the claims do not live inside single modules. "Missing data
never lowers risk" is enforced by the loader, the uncertainty engine, the facial
module and the safety guard acting together, and a test that checked any one of
them in isolation would pass while the property was broken.

So each file below is a CLAIM, each test is named after the sentence in the
README it defends, and the runner prints them as claims rather than as function
names. A judge reading `python -m scripts.run_tests` should be reading the
safety argument, not a coverage report.

A TEST THAT CANNOT FAIL IS NOT EVIDENCE
---------------------------------------
Several of the properties here are enforced by the ABSENCE of a code path --
the ratchet has no branch that lowers a band, the uncertainty engine has no
write access to the score. Asserting that a thing which cannot happen did not
happen is theatre: the test passes on an empty function.

So the central claims carry a companion test that deliberately breaks the
invariant and asserts the check CATCHES it. Those are marked `has_teeth` in the
output. If somebody deletes the property, the first test goes red; if somebody
weakens the check itself, the second one does.

NO THIRD-PARTY DEPENDENCIES
---------------------------
Plain `unittest` from the standard library. requirements.txt still lists
nothing, so a judge can clone this and run the safety argument on any machine
with Python 3.10 and no network. pytest discovers and runs these unchanged if
you prefer it.

SAFETY NOTE: these tests verify that the system behaves the way this repository
says it does. They are not clinical validation and they say nothing about
whether the behaviour is clinically correct -- every threshold they assert
against is a simulated demonstration value.
"""
