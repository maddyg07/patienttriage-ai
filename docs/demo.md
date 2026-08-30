# Demo runbook

Four minutes, three commands, one argument.

**The argument:** triage is not a snapshot, and a camera that sees a face
cannot tell you whether that face changed this morning or at birth. Everything
below exists to make those two sentences land without you having to say them.

---

## Before the room

```bash
cd patienttriage-ai
python -m scripts.run_tests          # 85 checks, 0 failed
python -m scripts.build_dashboard    # writes board.html
python -m scripts.run_intake         # opens the live console
```

Nothing to install. Python 3.10 or newer, standard library only.

Use **Chrome or Edge**. Speech recognition is a Chromium feature; everything
else works in any browser and the typed channel is always there.

Grant camera and microphone when the browser asks. It will, because the page is
served from `127.0.0.1`, and that is the only reason a live sensor demo works
with no certificate and no install.

**Have `board.html` already open in a second tab.** If the laptop's camera
fails in the room, you switch tabs and the demo continues from the department
board. Do not let a webcam permission dialog be a single point of failure.

---

## The four minutes

### 0:00 — The problem, stated once (20 seconds)

> "A patient is scored at the door and then waits. Risk does not wait with
> them. And the moment a system starts looking at faces, it has a second
> problem: some people's faces are simply different, and a detector cannot tell
> the difference between a stroke and how someone has always looked."

Do not explain the architecture. Show it.

### 0:20 — Live intake, patient one: the stroke (60 seconds)

On the intake console.

1. **Start camera.** Sit in frame. **Capture frame.**
   The readout shows a symmetry index and a candidate. Say out loud:
   *"That is not a finding. It is a candidate, and the system will not score it
   until someone confirms it."*
2. **Start listening.** Say, in a normal voice:
   *"My face dropped about forty minutes ago and my speech went funny, my right
   arm feels weak. No chest pain."*
   Stop. The transcript fills in.
3. **Read symptoms from text.** Chips appear: facial droop, slurred speech,
   arm weakness. And separately, under *denied*: chest pain.
   *"It kept the denial. It did not throw away what the patient told us."*
4. Confirm the camera questions. On the baseline question choose **new**.
5. Age 67, history **rich**. Vitals: HR 88, RR 18, SpO₂ 97, BP 168/94.
6. **Assess patient.**

**L4 CODE.** Point at the safety-rules box:

> "The score alone did not reach CODE. A hard clinical rule did, and it says so.
> The scoring model is not the final authority in this system."

### 1:20 — Patient two: the same face, a different person (50 seconds)

Do not restart anything. Change three things.

1. Baseline question → **normal for them**.
2. Documented reason → **burn or chemical injury**.
3. One-sided weakness → **no**. Slurred speech → **no**.
4. Clear the symptom chips. Type: *"severe migraine, same as my usual ones"*.
   Age 31, history rich.
5. **Assess patient.**

**L1 WATCH.** Confidence high.

> "Same camera reading. Same asymmetry. This is an acid-attack survivor with a
> migraine, and a detector-only system flags her as a stroke every time she
> enters a hospital for the rest of her life. Ours triages the migraine."

This is the moment the demo is for. Let it sit for a beat.

### 2:10 — Patient three: nobody knows (40 seconds)

1. Baseline question → **cannot say**.
2. History → **zero**. Leave temperature and blood pressure **blank**.
3. **Assess patient.**

**L2 LOOK**, confidence around 60%, and a list of named uncertainty drivers.

> "First visit, no record, and he genuinely cannot say whether his face has
> always looked like this. So the system does not guess in either direction. It
> says what it does not know, and a rule floors him at LOOK for human review
> rather than confidently dismissing him."

Then the line worth rehearsing:

> "Escalating on a missing record would sound cautious and would fall hardest
> on patients with no regular care and no relative to speak for them. The
> unknown baseline lowers our confidence. It does not raise his score."

### 2:50 — Deterioration and the ratchet (40 seconds)

Same patient, still on screen. Change **minutes since arrival** to 34, SpO₂ to
91, respiratory rate to 28. **Assess patient.**

The session panel shows both assessments. The band has risen.

Now put SpO₂ back to 98 and assess again.

> "The band does not come back down. The AI can raise acuity and has no code
> path that lowers it. Only a named nurse can de-escalate, with a logged
> reason. That is the Ratchet, and `python -m scripts.run_tests` proves it
> rather than asserting it."

### 3:30 — The board, and what you are not claiming (30 seconds)

Switch to `board.html`.

> "Twenty-four synthetic patients through a simulated shift. Three lists, not
> one ranking: who is sickest, who we might be wrong about, and who has waited
> past their target. Blending those into one number would hide a clinical
> trade-off nobody agreed to."

Close on this, and do not soften it:

> "Every threshold here is a simulated demonstration value. We have not
> validated this clinically and we are not claiming a miss rate. What we are
> claiming is an architecture where the model is not the final authority, the
> unknown stays visible, and the human keeps the decision."

---

## If something breaks

| Problem | What to do |
| --- | --- |
| Camera permission denied | The page reports the channel as failed and keeps scoring. Say so — it is the fail-safe requirement working. Continue by answering the questions manually. |
| No speech recognition | You are not on Chrome or Edge. Type into the transcript box; nothing else changes. |
| Port already in use | `python -m scripts.run_intake --port 8771` |
| Anything else | Switch to the `board.html` tab. It is a static file with no server and no dependencies. |

Rehearse the camera-denied path at least once. A demo that survives its own
failure mode is more convincing than one that never meets it.

---

## Questions to expect

**"Is the facial analysis real computer vision?"**
It is a luminance symmetry index computed in the browser canvas, and it is
crude. We chose not to build a landmark model, because detection is not where
the safety problem is. A perfect detector still cannot tell you whether a
difference is new, and that single question decides the case. The reasoning
above the sensor is the contribution; the sensor is replaceable.

**"Why not a trained model?"**
Every patient in this repository is synthetic and written by us. A model
trained on our own data would learn our own rules, and any accuracy figure we
quoted would be a property of our generator. `docs/limitations.md` sets out
what real validation would require.

**"Where does the captured data go?"**
Nowhere. Frames are analysed in the browser canvas and discarded, audio never
leaves the tab, and the server writes no file. Only the confirmed clinical
flags are posted. `tests/test_intake.py` asserts that a session creates no
files; `docs/privacy.md` has the rest.

**"What happens if the sensors disagree with the patient?"**
The conflict is recorded and risk goes up, never down. A patient denying
breathlessness while hypoxic is a conflict worth surfacing. A patient reporting
severe pain with normal observations is also a conflict, and it is flagged at
zero points — we record that we noticed, and we do not discount them for it.
