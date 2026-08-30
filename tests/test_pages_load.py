"""
tests/test_pages_load.py
========================
CLAIM: Every page's script parses and runs, and its buttons are bound.

THE FAILURE THIS FILE EXISTS FOR
--------------------------------
The Start button on the patient dashboard stopped working. Not because the
handler was wrong -- because line two of the script read a constant that was
never declared:

    const $ = id => document.getElementById(id);
    window.__USE_LANDMARKS__ = !!(S && S.landmarks);   // S was never declared

A ReferenceError there aborts the entire script. Every handler below it,
including the one on the Start button, is never bound. The page renders
perfectly, looks completely normal, and does nothing at all when clicked.

Nothing in the Python test suite could see it. Every server-side test passed,
every endpoint worked, and the product was unusable. This file exists because
generated JavaScript is code that nothing was checking.

WHAT IT CHECKS, AND WHY IN TWO WAYS
-----------------------------------
When Node is available it parses each page's script and evaluates it against a
stub DOM, then asserts the handlers are actually attached -- which is the check
that would have caught this.

When Node is not available it falls back to a structural check in pure Python:
every top-level constant a script reads must be declared above the line that
reads it. Weaker, and it still catches this exact bug on a machine with no
Node, which matters because that machine may be the one running the demo.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from tests.support import ClaimTest, has_teeth

NODE = shutil.which("node")

DOM_STUB = """
const els = {};
const el = () => ({style:{}, classList:{add(){}, remove(){}, contains(){return false}},
                   addEventListener(){}, appendChild(){}, removeChild(){},
                   querySelectorAll:()=>[], value:"", textContent:"",
                   innerHTML:"", title:"", scrollIntoView(){}, click(){},
                   getContext:()=>({drawImage(){}, getImageData:()=>({data:[]})}),
                   videoWidth:0, videoHeight:0, srcObject:null, disabled:false});
global.window = {addEventListener(){}, scrollTo(){},
                 SpeechRecognition:function(){this.start=()=>{};this.stop=()=>{}}};
global.document = {getElementById:id => els[id] || (els[id] = el()),
                   createElement:() => el()};
global.navigator = {mediaDevices:{getUserMedia:()=>Promise.reject(new Error("stub"))}};
global.fetch = () => Promise.resolve({json:()=>Promise.resolve({session_id:"T"})});
global.EventSource = function(){ this.onmessage=null; this.onerror=null; };
global.AudioContext = function(){};
global.prompt = () => null;
global.performance = {now:()=>0};
"""


def _script(html: str) -> str:
    return html.split("<script>")[1].split("</script>")[0]


def _code_only(script: str) -> str:
    """
    Strip comments and string literals, keeping every character position.

    Blanked rather than removed so the offsets a match reports still line up
    with the real file. Without this the check matched the word 'S' inside the
    comment explaining the bug and reported the bug as still present, which is
    its own small lesson about grepping source as if it were code.
    """
    out = list(script)
    i, n = 0, len(script)
    while i < n:
        two = script[i:i + 2]
        if two == "/*":
            end = script.find("*/", i + 2)
            end = n if end == -1 else end + 2
            for j in range(i, end):
                if out[j] != "\n":
                    out[j] = " "
            i = end
            continue
        if two == "//":
            end = script.find("\n", i)
            end = n if end == -1 else end
            for j in range(i, end):
                out[j] = " "
            i = end
            continue
        if script[i] in "\"'`":
            quote, j = script[i], i + 1
            while j < n and script[j] != quote:
                j += 2 if script[j] == "\\" else 1
            for k in range(i, min(j + 1, n)):
                if out[k] != "\n":
                    out[k] = " "
            i = j + 1
            continue
        i += 1
    return "".join(out)


def _pages():
    from app.nurse import render_nurse
    from app.patient import render_patient
    return {
        "patient": render_patient({}, use_landmarks=False),
        "patient --landmarks": render_patient({}, use_landmarks=True),
        "nurse": render_nurse(["chest pain", "breathlessness"]),
    }


def _run_node(script: str, tail: str) -> subprocess.CompletedProcess:
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(DOM_STUB + "\ntry{\n" + script + "\n}catch(e){"
                 "console.log('ERROR:' + e.constructor.name + ':' + e.message);"
                 "process.exit(0)}\n" + tail)
        path = fh.name
    try:
        return subprocess.run([NODE, path], capture_output=True, text=True,
                              timeout=25)
    finally:
        Path(path).unlink(missing_ok=True)


class TestEveryPageParses(ClaimTest):
    claim = "Every generated page's script is syntactically valid JavaScript."

    def test_each_page_parses(self):
        if not NODE:
            self.skipTest("node is not installed on this machine")
        for name, html in _pages().items():
            with tempfile.NamedTemporaryFile("w", suffix=".js",
                                             delete=False) as fh:
                fh.write(_script(html))
                path = fh.name
            try:
                result = subprocess.run([NODE, "--check", path],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0,
                                 f"{name} does not parse:\n{result.stderr}")
            finally:
                Path(path).unlink(missing_ok=True)


class TestEveryPageRuns(ClaimTest):
    claim = ("Every page's script runs to the end and binds its handlers. A "
             "page that renders but binds nothing looks perfectly normal.")

    def test_no_page_throws_on_load(self):
        if not NODE:
            self.skipTest("node is not installed on this machine")
        for name, html in _pages().items():
            result = _run_node(_script(html), "")
            self.assertNotIn("ERROR:", result.stdout,
                             f"{name} threw on load: {result.stdout.strip()}")

    def test_the_patient_start_button_is_bound(self):
        """
        The exact regression. The button existed, looked right, and had no
        handler because the script had already aborted three hundred lines
        above it.
        """
        if not NODE:
            self.skipTest("node is not installed on this machine")
        from app.patient import render_patient
        for landmarks in (False, True):
            result = _run_node(
                _script(render_patient({}, use_landmarks=landmarks)),
                "console.log('BOUND:' + (typeof els['start'].onclick === "
                "'function'));")
            self.assertIn("BOUND:true", result.stdout,
                          f"the Start button is unbound with "
                          f"landmarks={landmarks}: {result.stdout.strip()}")

    def test_the_nurse_controls_are_bound(self):
        if not NODE:
            self.skipTest("node is not installed on this machine")
        from app.nurse import render_nurse
        result = _run_node(
            _script(render_nurse(["chest pain"])),
            "console.log('BOUND:' + (typeof els['saveVitals'].onclick === "
            "'function' && typeof els['saveNotes'].onclick === 'function'));")
        self.assertIn("BOUND:true", result.stdout,
                      f"nurse controls unbound: {result.stdout.strip()}")

    @has_teeth
    def test_the_runtime_check_can_actually_fail(self):
        """
        A harness that reports success on everything would pass every
        assertion above. Feed it the original bug and it must report it.
        """
        if not NODE:
            self.skipTest("node is not installed on this machine")
        result = _run_node("window.x = !!(S && S.landmarks);", "")
        self.assertIn("ReferenceError", result.stdout,
                      "the harness did not notice an undeclared constant")


class TestConstantsAreDeclaredBeforeUse(ClaimTest):
    claim = ("Every injected constant is declared above the first line that "
             "reads it. Checked without Node, because the machine running the "
             "demo may not have it.")

    def test_injected_constants_are_declared_first(self):
        for name, html in _pages().items():
            script = _code_only(_script(html))
            for const in ("S", "VOCAB"):
                declaration = re.search(r"\bconst\s+" + const + r"\s*=", script)
                usage = re.search(r"(?<![\w$.])" + const + r"(?![\w$])", script)
                if usage is None:
                    continue
                self.assertIsNotNone(
                    declaration,
                    f"{name} reads '{const}' and never declares it")
                self.assertLess(
                    declaration.start(), usage.start() + 1,
                    f"{name} reads '{const}' before declaring it, which "
                    f"aborts the whole script and unbinds every handler")

    def test_the_settings_blob_is_valid_json(self):
        for name, html in _pages().items():
            script = _script(html)
            match = re.search(r"const (?:S|VOCAB) = (.+?);\n", script)
            if not match:
                continue
            try:
                json.loads(match.group(1))
            except ValueError as exc:
                self.fail(f"{name} was handed malformed JSON: {exc}")
