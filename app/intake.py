"""
app/intake.py
=============
Phase 17. Renders the live intake console: a doorway scan, a voice channel, a
typed channel, and the operator confirmation step that sits between the sensors
and the engine.

WHY THE BROWSER
---------------
Round 1 promised a doorway scan and a voice stream. Delivering that in Python
means OpenCV or MediaPipe plus an audio stack: several hundred megabytes, a
compiler on some machines, and a live demo that can fail in the room because a
wheel did not build. This repository has had zero third-party dependencies since
Phase 1 and that is worth more than a logo on a slide.

Every browser already ships a camera API, a microphone API, an audio analyser
and a speech recogniser. Served from 127.0.0.1 they need no certificate, no
install and no network. So the capture layer is HTML and the engine stays
exactly where it was.

WHAT THIS FILE IS ALLOWED TO DO
-------------------------------
Capture, and format. The page computes two crude signal statistics -- a facial
symmetry index and a speech-pause pattern -- and it computes NO clinical
quantity. It does not know what a band is, it cannot score, and it has no copy
of a threshold. It posts confirmed flags to /assess and renders what comes back.
The same rule app/dashboard.py obeys.

THE CONFIRMATION STEP IS THE POINT
----------------------------------
The sensors do not report findings. They raise candidates, and an operator
accepts or corrects each one before anything is scored. That is not a
limitation we are apologising for -- it is the argument.

A camera can see that two halves of a face differ. It cannot see whether the
difference arrived this morning or at birth, and that question is the entire
clinical distinction between a stroke and a person's ordinary appearance. No
better detector answers it. The panel that asks 'is this new?' is doing more
work than the pixel analysis above it, and the demo is arranged so that this is
visible rather than asserted.
"""

from __future__ import annotations

import json
from typing import Optional

from core.intake_bridge import load_intake_config


def render_intake(config: Optional[dict] = None,
                  vocabulary: Optional[list] = None) -> str:
    cfg = config or load_intake_config()
    vocab = vocabulary or []
    settings = json.dumps({
        "camera": cfg.get("camera", {}),
        "audio": cfg.get("audio", {}),
        "vocabulary": vocab,
    })
    return _PAGE.replace("__SETTINGS__", settings)


_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PatientTriage.ai &mdash; Live Intake</title>
<style>
  :root{
    --bg:#0d1117; --panel:#151b23; --panel2:#1c2530; --line:#2a3441;
    --ink:#e6edf3; --dim:#8b98a5; --faint:#5c6773;
    --watch:#4a9eda; --look:#d9a441; --pull:#e07b39; --code:#e5484d;
    --ok:#3fb950; --accent:#a371f7;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
       font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
  header{background:var(--panel);border-bottom:1px solid var(--line);
         padding:12px 20px;display:flex;align-items:center;gap:18px;
         position:sticky;top:0;z-index:20}
  header h1{margin:0;font-size:16px;letter-spacing:.4px;font-weight:600}
  header .tag{font-size:11px;color:var(--dim);border:1px solid var(--line);
              padding:3px 8px;border-radius:3px;letter-spacing:.5px}
  header .spacer{flex:1}
  .warn{background:#3d1d1d;border-bottom:1px solid #5c2626;color:#ffc9c9;
        padding:7px 20px;font-size:12px;letter-spacing:.2px}
  main{max-width:1320px;margin:0 auto;padding:20px;
       display:grid;grid-template-columns:1fr 1fr;gap:18px}
  @media(max-width:1000px){main{grid-template-columns:1fr}}
  section{background:var(--panel);border:1px solid var(--line);border-radius:6px}
  section > h2{margin:0;padding:11px 16px;font-size:12px;font-weight:600;
     letter-spacing:1.1px;text-transform:uppercase;color:var(--dim);
     border-bottom:1px solid var(--line);display:flex;align-items:center;gap:10px}
  section > h2 .step{background:var(--panel2);color:var(--accent);
     width:20px;height:20px;border-radius:50%;display:grid;place-items:center;
     font-size:11px;flex:none}
  .body{padding:16px}
  .full{grid-column:1/-1}

  video,canvas.preview{width:100%;border-radius:4px;background:#000;display:block}
  .videowrap{position:relative}
  .videowrap .guide{position:absolute;border:1px dashed rgba(163,113,247,.65);
     pointer-events:none;border-radius:3px}
  .row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
  .row + .row{margin-top:12px}

  button{background:var(--panel2);color:var(--ink);border:1px solid var(--line);
     padding:8px 14px;border-radius:4px;cursor:pointer;font-size:13px;
     font-family:inherit;transition:border-color .12s,background .12s}
  button:hover:not(:disabled){border-color:var(--accent)}
  button:disabled{opacity:.4;cursor:not-allowed}
  button.primary{background:var(--accent);border-color:var(--accent);color:#0d1117;
     font-weight:600}
  button.rec{border-color:var(--code);color:#ffb3b5}
  button.wide{width:100%;padding:13px;font-size:15px}

  label{display:block;font-size:11px;color:var(--dim);margin-bottom:4px;
        letter-spacing:.4px;text-transform:uppercase}
  input,select,textarea{width:100%;background:var(--bg);color:var(--ink);
     border:1px solid var(--line);border-radius:4px;padding:8px 10px;
     font:13px/1.45 inherit}
  textarea{min-height:96px;resize:vertical}
  input:focus,select:focus,textarea:focus{outline:none;border-color:var(--accent)}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  .grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
  .grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
  @media(max-width:560px){.grid3,.grid4{grid-template-columns:1fr 1fr}}

  .readout{background:var(--panel2);border:1px solid var(--line);border-radius:4px;
     padding:11px 13px;font:12px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;
     color:var(--dim);white-space:pre-wrap}
  .meter{height:6px;background:var(--bg);border-radius:3px;overflow:hidden;
     border:1px solid var(--line);margin-top:7px}
  .meter i{display:block;height:100%;background:var(--accent);width:0;
     transition:width .1s linear}

  .candidate{border:1px solid var(--line);border-left:3px solid var(--accent);
     background:var(--panel2);border-radius:4px;padding:11px 13px;margin-top:10px}
  .candidate .q{font-size:13px;margin-bottom:9px}
  .candidate .src{font-size:11px;color:var(--faint);margin-bottom:9px;
     font-family:ui-monospace,Menlo,monospace}
  .choices{display:flex;gap:7px;flex-wrap:wrap}
  .choices button{padding:5px 12px;font-size:12px}
  .choices button.on{background:var(--accent);border-color:var(--accent);
     color:#0d1117;font-weight:600}
  .choices button.on.neg{background:var(--faint);border-color:var(--faint);color:#fff}

  .chips{display:flex;gap:7px;flex-wrap:wrap;margin-top:9px;min-height:26px}
  .chip{background:var(--panel2);border:1px solid var(--line);border-radius:12px;
     padding:3px 11px;font-size:12px;display:flex;align-items:center;gap:7px}
  .chip.deny{border-style:dashed;color:var(--dim)}
  .chip b{font-weight:500}
  .chip span{cursor:pointer;color:var(--faint);font-size:14px;line-height:1}
  .chip span:hover{color:var(--code)}

  .band{display:inline-block;padding:4px 12px;border-radius:3px;font-weight:700;
     letter-spacing:1px;font-size:13px}
  .b1{background:#12324d;color:var(--watch)} .b2{background:#3d3115;color:var(--look)}
  .b3{background:#452414;color:var(--pull)} .b4{background:#4a1517;color:#ff8085}

  .verdict{display:flex;align-items:center;gap:16px;flex-wrap:wrap;
     padding-bottom:14px;border-bottom:1px solid var(--line);margin-bottom:14px}
  .verdict .score{font-size:38px;font-weight:700;line-height:1}
  .verdict .score small{font-size:14px;color:var(--faint);font-weight:400}
  .verdict .conf{font-size:12px;color:var(--dim)}
  .hint{font-size:11px;color:var(--faint);margin-top:6px;line-height:1.5}
  .rulebox{background:#2a1618;border:1px solid #5c2626;border-radius:4px;
     padding:10px 12px;margin-top:12px;font-size:12px;color:#ffc9c9}
  .muted{color:var(--dim);font-size:12px}
  .status{font-size:12px;color:var(--dim);min-height:18px}
  .status.live{color:var(--ok)}
  .status.err{color:#ff8085}
  footer{max-width:1320px;margin:0 auto;padding:0 20px 40px;color:var(--faint);
     font-size:11px;line-height:1.7}
</style>
</head>
<body>

<header>
  <h1>PatientTriage.ai</h1>
  <span class="tag">LIVE INTAKE</span>
  <span class="spacer"></span>
  <span class="tag" id="clock">t = 0 min</span>
</header>

<div class="warn">
  Prototype on synthetic thresholds. No clinical validation. Camera frames,
  audio and transcript stay in this browser tab and are never written to disk
  &mdash; only the confirmed flags below are sent to the engine.
</div>

<main>

<!-- ================= 1. DOORWAY SCAN ================= -->
<section>
  <h2><span class="step">1</span>Doorway scan</h2>
  <div class="body">
    <div class="videowrap">
      <video id="cam" autoplay playsinline muted></video>
      <div class="guide" id="guide"></div>
    </div>
    <canvas id="work" style="display:none"></canvas>

    <div class="row" style="margin-top:12px">
      <button id="camStart">Start camera</button>
      <button id="camShot" disabled>Capture frame</button>
      <button id="camOff" disabled>Stop</button>
    </div>
    <div class="status" id="camStatus" style="margin-top:8px">Camera not started.</div>

    <div class="readout" id="camRead" style="margin-top:12px">no frame analysed yet</div>

    <div id="camQuestions"></div>

    <p class="hint">
      The symmetry index compares the two halves of the dashed region. It is a
      crude luminance measure, sensitive to lighting and head angle, and it is
      never scored directly. It raises a candidate that you confirm below.
    </p>
  </div>
</section>

<!-- ================= 2. VOICE ================= -->
<section>
  <h2><span class="step">2</span>Voice channel</h2>
  <div class="body">
    <div class="row">
      <button id="micStart">Start listening</button>
      <button id="micStop" class="rec" disabled>Stop</button>
    </div>
    <div class="meter"><i id="level"></i></div>
    <div class="status" id="micStatus" style="margin-top:8px">Microphone not started.</div>

    <div style="margin-top:14px">
      <label>Transcript &mdash; spoken, editable</label>
      <textarea id="transcript" placeholder="Press Start listening and ask the patient what brought them in. If speech recognition is unavailable in this browser, type here instead."></textarea>
    </div>

    <div class="readout" id="micRead" style="margin-top:12px">no speech analysed yet</div>

    <div id="micQuestions"></div>
  </div>
</section>

<!-- ================= 3. SYMPTOMS ================= -->
<section>
  <h2><span class="step">3</span>What the patient reports</h2>
  <div class="body">
    <label>Additional notes typed by staff</label>
    <textarea id="typed" placeholder="Anything the patient said that is not in the transcript."></textarea>

    <div class="row" style="margin-top:12px">
      <button id="readText">Read symptoms from text</button>
      <button id="clearSx">Clear</button>
    </div>

    <div style="margin-top:14px">
      <label>Recognised &mdash; click a term to remove it</label>
      <div class="chips" id="sxChips"><span class="muted">nothing recognised yet</span></div>
    </div>
    <div style="margin-top:12px">
      <label>Explicitly denied</label>
      <div class="chips" id="denyChips"><span class="muted">none</span></div>
    </div>

    <div class="grid2" style="margin-top:14px">
      <div>
        <label>Add a term manually</label>
        <select id="sxAdd"><option value="">select&hellip;</option></select>
      </div>
      <div>
        <label>Pain score (0&ndash;10)</label>
        <input id="pain" type="number" min="0" max="10" placeholder="blank = not asked">
      </div>
    </div>

    <p class="hint">
      A denial is kept, not discarded. If the patient denies breathlessness and
      the objective findings disagree, the engine records the conflict and
      raises risk. It never lowers risk on a denial.
    </p>
  </div>
</section>

<!-- ================= 4. OBJECTIVE ================= -->
<section>
  <h2><span class="step">4</span>Objective observations</h2>
  <div class="body">
    <div class="grid3">
      <div><label>Age (years)</label><input id="age" type="number" value="52" min="0" max="120" step="0.1"></div>
      <div><label>Sex</label>
        <select id="sex"><option>female</option><option>male</option><option>unspecified</option></select></div>
      <div><label>History on file</label>
        <select id="hist">
          <option value="zero">zero &mdash; first visit</option>
          <option value="partial">partial</option>
          <option value="rich">rich</option>
        </select></div>
    </div>

    <div class="grid3" style="margin-top:12px">
      <div><label>Heart rate</label><input id="heart_rate" type="number" placeholder="blank = not measured"></div>
      <div><label>Resp rate</label><input id="respiratory_rate" type="number" placeholder="blank"></div>
      <div><label>SpO&#8322; %</label><input id="spo2" type="number" placeholder="blank"></div>
      <div><label>Temp &deg;C</label><input id="temperature_c" type="number" step="0.1" placeholder="blank"></div>
      <div><label>Systolic BP</label><input id="systolic_bp" type="number" placeholder="blank"></div>
      <div><label>Diastolic BP</label><input id="diastolic_bp" type="number" placeholder="blank"></div>
    </div>

    <div class="grid2" style="margin-top:12px">
      <div><label>Consciousness</label>
        <select id="consciousness">
          <option value="alert">alert</option>
          <option value="responds_to_voice">responds to voice</option>
          <option value="responds_to_pain">responds to pain</option>
          <option value="unresponsive">unresponsive</option>
          <option value="unknown">unknown</option>
        </select></div>
      <div><label>Pallor or cyanosis</label>
        <select id="skin_pallor_or_cyanosis">
          <option value="unknown">unknown</option><option value="no">no</option><option value="yes">yes</option>
        </select></div>
      <div><label>Gait abnormal</label>
        <select id="gait_abnormal">
          <option value="unknown">unknown</option><option value="no">no</option><option value="yes">yes</option>
        </select></div>
      <div><label>Minutes since arrival</label><input id="arrival_minute" type="number" value="0" min="0"></div>
    </div>

    <div class="grid2" style="margin-top:12px">
      <div><label>Medications on file (comma separated)</label>
        <input id="medications" placeholder="e.g. apixaban, bisoprolol"></div>
      <div><label>Known conditions (comma separated)</label>
        <input id="conditions" placeholder="e.g. atrial fibrillation"></div>
    </div>

    <p class="hint">
      Leave a vital blank if it was not measured. Blank means unknown and lowers
      confidence. It never reads as normal.
    </p>
  </div>
</section>

<!-- ================= 5. ASSESS ================= -->
<section class="full">
  <h2><span class="step">5</span>Assessment</h2>
  <div class="body">
    <button id="go" class="primary wide">Assess patient</button>
    <div class="status" id="goStatus" style="margin-top:10px"></div>
    <div id="result" style="margin-top:18px"></div>
  </div>
</section>

</main>

<footer>
  PatientTriage.ai &mdash; Accenture Innovation Challenge 2026, Team Slayers.
  Decision support, not a decision maker. Every threshold is a simulated
  demonstration value. Nothing in this prototype has been clinically validated
  and it must not be used for any real triage.
</footer>

<script>
const S = __SETTINGS__;
const $ = id => document.getElementById(id);

/* ---------------------------------------------------------------
   State. The page holds flags, never clinical conclusions.
   Anything unanswered stays "unknown" all the way to the engine.
--------------------------------------------------------------- */
const flags = {
  facial_capture_status: "not_attempted",
  asymmetry_observed: "unknown", droop_observed: "unknown",
  visible_distress: "unknown", baseline_known: "unknown",
  baseline_asymmetry_present: "unknown", baseline_condition: "unknown",
  change_reported_as_new: "unknown",
  speech_abnormality: "unknown", unilateral_weakness: "unknown",
  voice_capture_status: "not_attempted", slurred_speech: "unknown",
  breathlessness_between_words: "unknown",
  unable_to_speak_full_sentence: "unknown",
  can_communicate: "unknown"
};
let symptoms = [], denied = [];

/* =============================================================
   1. CAMERA
   ============================================================= */
let stream = null;
const cam = $("cam"), work = $("work");

function placeGuide(){
  const r = S.camera.analysis_region || {};
  const g = $("guide");
  g.style.left   = ((r.x_fraction||.22)*100) + "%";
  g.style.top    = ((r.y_fraction||.14)*100) + "%";
  g.style.width  = ((r.width_fraction||.56)*100) + "%";
  g.style.height = ((r.height_fraction||.62)*100) + "%";
}
placeGuide(); window.addEventListener("resize", placeGuide);

$("camStart").onclick = async () => {
  try{
    stream = await navigator.mediaDevices.getUserMedia({video:{width:640}});
    cam.srcObject = stream;
    $("camStart").disabled = true; $("camShot").disabled = false; $("camOff").disabled = false;
    setStatus("camStatus","Camera live. Frames are analysed in this tab and discarded.","live");
  }catch(e){
    setStatus("camStatus","Camera unavailable: " + e.message +
      ". The rest of intake still works; the facial channel will report as failed.","err");
    flags.facial_capture_status = "failed";
    renderCameraQuestions(null);
  }
};

$("camOff").onclick = () => {
  if(stream){ stream.getTracks().forEach(t=>t.stop()); stream = null; }
  $("camStart").disabled = false; $("camShot").disabled = true; $("camOff").disabled = true;
  setStatus("camStatus","Camera stopped.","");
};

$("camShot").onclick = () => {
  const w = S.camera.frame_width || 480;
  const h = Math.round(w * (cam.videoHeight/cam.videoWidth || 0.75));
  work.width = w; work.height = h;
  const ctx = work.getContext("2d");
  ctx.drawImage(cam, 0, 0, w, h);

  const r = S.camera.analysis_region || {};
  const rx = Math.round(w*(r.x_fraction||.22)), ry = Math.round(h*(r.y_fraction||.14));
  const rw = Math.round(w*(r.width_fraction||.56)), rh = Math.round(h*(r.height_fraction||.62));
  const data = ctx.getImageData(rx, ry, rw, rh).data;

  /* Luminance grid, left half vs mirrored right half. */
  const COLS = 12, ROWS = 16;
  const cellW = Math.floor(rw/COLS), cellH = Math.floor(rh/ROWS);
  const grid = [], lum = [];
  for(let cy=0; cy<ROWS; cy++){
    const row = [];
    for(let cx=0; cx<COLS; cx++){
      let sum = 0, n = 0;
      for(let y=cy*cellH; y<(cy+1)*cellH; y+=2){
        for(let x=cx*cellW; x<(cx+1)*cellW; x+=2){
          const i = (y*rw + x)*4;
          sum += 0.299*data[i] + 0.587*data[i+1] + 0.114*data[i+2];
          n++;
        }
      }
      const v = n ? sum/n : 0; row.push(v); lum.push(v);
    }
    grid.push(row);
  }
  const mean = lum.reduce((a,b)=>a+b,0)/lum.length;

  if(mean < (S.camera.min_brightness||28) || mean > (S.camera.max_brightness||232)){
    $("camRead").textContent =
      "frame rejected\n  mean luminance " + mean.toFixed(0) +
      " is outside the usable range\n  adjust lighting and capture again";
    flags.facial_capture_status = "failed";
    renderCameraQuestions(null);
    return;
  }

  let diff = 0, pairs = 0;
  for(let cy=0; cy<ROWS; cy++){
    for(let cx=0; cx<Math.floor(COLS/2); cx++){
      const L = grid[cy][cx], R = grid[cy][COLS-1-cx];
      diff += Math.abs(L-R); pairs++;
    }
  }
  const index = pairs ? (diff/pairs)/(mean||1) : 0;
  const t = S.camera.symmetry_index_thresholds || {};
  const weak = t.asymmetry_candidate ?? 0.16, strong = t.strong_candidate ?? 0.26;
  const level = index >= strong ? "strong" : index >= weak ? "possible" : "none";

  $("camRead").textContent =
    "frame analysed\n" +
    "  mean luminance   " + mean.toFixed(0) + "\n" +
    "  symmetry index   " + index.toFixed(3) +
    "   (candidate at " + weak + ", strong at " + strong + ")\n" +
    "  candidate        " + (level === "none" ? "no asymmetry detected" :
                             level + " asymmetry") + "\n" +
    "  NOT a finding. Confirm below before anything is scored.";

  flags.facial_capture_status = "ok";
  renderCameraQuestions(level);
};

/* The confirmation panel. This is where the demo earns its argument. */
function renderCameraQuestions(level){
  const box = $("camQuestions");
  if(flags.facial_capture_status === "failed"){
    box.innerHTML = '<div class="candidate"><div class="q">Facial channel failed.</div>' +
      '<div class="src">capture_status = failed</div>' +
      '<div class="muted">The engine continues on vitals, symptoms, history and ' +
      'observation, and raises uncertainty because a modality is missing. ' +
      'A sensor failure is not a clinical finding.</div></div>';
    return;
  }
  const suggested = level && level !== "none" ? "yes" : "no";
  box.innerHTML = "";
  box.appendChild(question(
    "Is the face asymmetric or drooping right now?",
    "camera candidate: " + (level==="none" ? "no asymmetry" : level + " asymmetry"),
    ["yes","no","unknown"], "asymmetry_observed", suggested,
    v => { flags.droop_observed = v; }
  ));
  box.appendChild(question(
    "Is this different from how the patient normally looks?",
    "no sensor can answer this — it decides stroke versus ordinary appearance",
    ["new","normal for them","cannot say"],
    "__baseline__", null,
    null, v => {
      if(v === "new"){
        flags.baseline_known="yes"; flags.baseline_asymmetry_present="no";
        flags.change_reported_as_new="yes"; flags.baseline_condition="none";
      }else if(v === "normal for them"){
        flags.baseline_known="yes"; flags.baseline_asymmetry_present="yes";
        flags.change_reported_as_new="no";
      }else{
        flags.baseline_known="unknown"; flags.baseline_asymmetry_present="unknown";
        flags.change_reported_as_new="unknown"; flags.baseline_condition="unknown";
      }
    }
  ));
  box.appendChild(selectQuestion(
    "If this is their normal appearance, what is the documented reason?",
    "recorded for provenance only — it never changes the score",
    [["unknown","not documented"],["none","no baseline difference"],
     ["congenital","congenital"],["post_stroke","previous stroke"],
     ["burn_or_acid","burn or chemical injury"],["surgical","surgery"],
     ["trauma","previous trauma"],["chronic_palsy","chronic palsy"]],
    "baseline_condition"
  ));
  box.appendChild(question(
    "New one-sided limb weakness?",
    "asked because it converts an isolated facial finding into a cluster",
    ["yes","no","unknown"], "unilateral_weakness", null
  ));
  box.appendChild(question(
    "Patient visibly distressed?", "observed", ["yes","no","unknown"],
    "visible_distress", null
  ));
}

/* =============================================================
   2. MICROPHONE
   ============================================================= */
let audioCtx=null, analyser=null, micStream=null, timer=null, recog=null;
let samples=[], listening=false;

$("micStart").onclick = async () => {
  try{
    micStream = await navigator.mediaDevices.getUserMedia({audio:true});
    audioCtx = new (window.AudioContext||window.webkitAudioContext)();
    const src = audioCtx.createMediaStreamSource(micStream);
    analyser = audioCtx.createAnalyser(); analyser.fftSize = 2048;
    src.connect(analyser);
    samples = []; listening = true;
    const buf = new Float32Array(analyser.fftSize);
    timer = setInterval(() => {
      analyser.getFloatTimeDomainData(buf);
      let sum=0; for(let i=0;i<buf.length;i++) sum += buf[i]*buf[i];
      const rms = Math.sqrt(sum/buf.length);
      samples.push(rms);
      $("level").style.width = Math.min(100, rms*900) + "%";
    }, S.audio.sample_ms || 60);

    startRecognition();
    $("micStart").disabled = true; $("micStop").disabled = false;
    setStatus("micStatus", recog
      ? "Listening. Speech recognition active; audio stays in this tab."
      : "Listening. This browser has no speech recognition — type the transcript instead.",
      "live");
  }catch(e){
    setStatus("micStatus","Microphone unavailable: " + e.message +
      ". Type the transcript instead; the voice channel will report as failed.","err");
    flags.voice_capture_status = "failed";
    renderMicQuestions(null);
  }
};

$("micStop").onclick = () => {
  listening = false;
  if(timer){ clearInterval(timer); timer=null; }
  if(recog){ try{ recog.stop(); }catch(e){} }
  if(micStream){ micStream.getTracks().forEach(t=>t.stop()); micStream=null; }
  if(audioCtx){ audioCtx.close(); audioCtx=null; }
  $("level").style.width = "0";
  $("micStart").disabled = false; $("micStop").disabled = true;
  setStatus("micStatus","Stopped.","");
  analyseSpeech();
};

function startRecognition(){
  const R = window.SpeechRecognition || window.webkitSpeechRecognition;
  if(!R){ recog = null; return; }
  recog = new R();
  recog.continuous = true; recog.interimResults = true; recog.lang = "en-IN";
  let settled = "";
  recog.onresult = e => {
    let interim = "";
    for(let i=e.resultIndex; i<e.results.length; i++){
      const txt = e.results[i][0].transcript;
      if(e.results[i].isFinal) settled += txt + " "; else interim += txt;
    }
    $("transcript").value = (settled + interim).trim();
  };
  recog.onerror = () => {};
  recog.onend = () => { if(listening){ try{ recog.start(); }catch(e){} } };
  try{ recog.start(); }catch(e){ recog = null; }
}

/* Speech-pause pattern. Not a respiratory measurement. */
function analyseSpeech(){
  const ms = S.audio.sample_ms || 60;
  const floor = S.audio.silence_rms ?? 0.012;
  const pauseMs = S.audio.pause_ms_for_break ?? 320;
  const needBreaks = S.audio.breaks_for_breathless ?? 3;
  const maxPhrase = S.audio.max_phrase_ms_for_full_sentence ?? 2600;

  if(samples.length < 8){
    $("micRead").textContent = "not enough audio captured to analyse";
    flags.voice_capture_status = samples.length ? "ok" : "not_attempted";
    renderMicQuestions(null); return;
  }
  let breaks=0, run=0, phrase=0, longest=0, voiced=0;
  for(const rms of samples){
    if(rms > floor){
      voiced++; phrase += ms; run = 0;
      if(phrase > longest) longest = phrase;
    }else{
      run += ms;
      if(run === pauseMs || (run > pauseMs && run - ms < pauseMs)){
        if(phrase > 200) breaks++;
      }
      if(run > pauseMs) phrase = 0;
    }
  }
  const secs = (samples.length*ms/1000).toFixed(1);
  const broken = breaks >= needBreaks;
  const short = longest > 0 && longest < maxPhrase;

  $("micRead").textContent =
    "speech analysed\n" +
    "  duration          " + secs + " s\n" +
    "  voiced fraction   " + (voiced/samples.length).toFixed(2) + "\n" +
    "  mid-speech breaks " + breaks + "   (candidate at " + needBreaks + ")\n" +
    "  longest phrase    " + longest + " ms  (short below " + maxPhrase + ")\n" +
    "  candidate         " + (broken || short ? "possible breathlessness" : "no breathlessness pattern") + "\n" +
    "  Pattern only. Confirm below.";

  flags.voice_capture_status = "ok";
  flags.can_communicate = voiced > 3 ? "yes" : "unknown";
  renderMicQuestions({broken, short});
}

function renderMicQuestions(sig){
  const box = $("micQuestions");
  if(flags.voice_capture_status === "failed"){
    box.innerHTML = '<div class="candidate"><div class="q">Voice channel failed.</div>' +
      '<div class="src">capture_status = failed</div>' +
      '<div class="muted">Scoring continues without it and uncertainty rises.</div></div>';
    return;
  }
  box.innerHTML = "";
  box.appendChild(question(
    "Breathless between words?",
    sig ? ("audio candidate: " + (sig.broken ? "yes, " : "no, ") + "mid-speech breaks")
        : "no audio candidate",
    ["yes","no","unknown"], "breathlessness_between_words",
    sig ? (sig.broken ? "yes" : "no") : null
  ));
  box.appendChild(question(
    "Unable to complete a full sentence?",
    sig ? ("audio candidate: " + (sig.short ? "yes, short phrases" : "no, phrases sustained"))
        : "no audio candidate",
    ["yes","no","unknown"], "unable_to_speak_full_sentence",
    sig ? (sig.short ? "yes" : "no") : null
  ));
  box.appendChild(question(
    "Speech slurred or dysarthric?",
    "listen to the patient — acoustic analysis here is not reliable enough to suggest",
    ["yes","no","unknown"], "slurred_speech", null,
    v => { flags.speech_abnormality = v; }
  ));
}

/* =============================================================
   Shared question widgets
   ============================================================= */
function question(text, source, options, key, suggested, mirror, custom){
  const el = document.createElement("div");
  el.className = "candidate";
  el.innerHTML = '<div class="q">' + text + '</div><div class="src">' + source + '</div>';
  const row = document.createElement("div"); row.className = "choices";
  options.forEach(opt => {
    const b = document.createElement("button");
    b.textContent = opt + (suggested === opt ? "  (suggested)" : "");
    b.onclick = () => {
      row.querySelectorAll("button").forEach(x => x.classList.remove("on","neg"));
      b.classList.add("on"); if(opt==="no"||opt==="normal for them") b.classList.add("neg");
      if(custom) custom(opt);
      else { flags[key] = opt; if(mirror) mirror(opt); }
    };
    row.appendChild(b);
  });
  el.appendChild(row);
  return el;
}

function selectQuestion(text, source, pairs, key){
  const el = document.createElement("div");
  el.className = "candidate";
  el.innerHTML = '<div class="q">' + text + '</div><div class="src">' + source + '</div>';
  const sel = document.createElement("select");
  pairs.forEach(([v,l]) => {
    const o = document.createElement("option"); o.value = v; o.textContent = l; sel.appendChild(o);
  });
  sel.onchange = () => { flags[key] = sel.value; };
  el.appendChild(sel);
  return el;
}

/* =============================================================
   3. SYMPTOMS
   ============================================================= */
(function fillVocab(){
  const sel = $("sxAdd");
  (S.vocabulary||[]).forEach(term => {
    const o = document.createElement("option"); o.value=term; o.textContent=term; sel.appendChild(o);
  });
  sel.onchange = () => {
    if(sel.value && !symptoms.includes(sel.value)){ symptoms.push(sel.value); drawChips(); }
    sel.value = "";
  };
})();

$("readText").onclick = async () => {
  const text = ($("transcript").value + " " + $("typed").value).trim();
  if(!text){ setStatus("goStatus","Nothing to read yet.","err"); return; }
  try{
    const r = await fetch("/read", {method:"POST", headers:{"Content-Type":"application/json"},
                                    body: JSON.stringify({text})});
    const d = await r.json();
    d.reported.forEach(t => { if(!symptoms.includes(t)) symptoms.push(t); });
    d.denied.forEach(t => { if(!denied.includes(t)) denied.push(t); });
    if(d.pain_score !== null && d.pain_score !== undefined && !$("pain").value)
      $("pain").value = d.pain_score;
    drawChips();
    setStatus("goStatus","Read " + d.reported.length + " reported and " +
              d.denied.length + " denied term(s) from the text.","live");
  }catch(e){ setStatus("goStatus","Could not reach the engine: " + e.message,"err"); }
};

$("clearSx").onclick = () => { symptoms=[]; denied=[]; drawChips(); };

function drawChips(){
  const a = $("sxChips"), b = $("denyChips");
  a.innerHTML = symptoms.length ? "" : '<span class="muted">nothing recognised yet</span>';
  symptoms.forEach(t => a.appendChild(chip(t, false, () => {
    symptoms = symptoms.filter(x=>x!==t); if(!denied.includes(t)) denied.push(t); drawChips();
  })));
  b.innerHTML = denied.length ? "" : '<span class="muted">none</span>';
  denied.forEach(t => b.appendChild(chip(t, true, () => {
    denied = denied.filter(x=>x!==t); drawChips();
  })));
}

function chip(text, isDeny, onX){
  const c = document.createElement("div");
  c.className = "chip" + (isDeny ? " deny" : "");
  const b = document.createElement("b"); b.textContent = text;
  const x = document.createElement("span"); x.textContent = "×";
  x.title = isDeny ? "remove entirely" : "move to denied";
  x.onclick = onX;
  c.appendChild(b); c.appendChild(x);
  return c;
}

/* =============================================================
   5. ASSESS
   ============================================================= */
$("arrival_minute").oninput = () => {
  $("clock").textContent = "t = " + ($("arrival_minute").value||0) + " min";
};

$("go").onclick = async () => {
  const num = id => { const v = $(id).value.trim(); return v === "" ? null : Number(v); };
  const list = id => $(id).value.split(",").map(s=>s.trim()).filter(Boolean);

  const payload = Object.assign({}, flags, {
    patient_id: "LIVE-001",
    age_years: Number($("age").value || 40),
    sex: $("sex").value,
    arrival_minute: Number($("arrival_minute").value || 0),
    history_tier: $("hist").value,
    transcript: $("transcript").value,
    typed_symptoms: $("typed").value,
    added_symptoms: symptoms,
    denied_symptoms: denied,
    pain_score: num("pain"),
    heart_rate: num("heart_rate"),
    respiratory_rate: num("respiratory_rate"),
    spo2: num("spo2"),
    temperature_c: num("temperature_c"),
    systolic_bp: num("systolic_bp"),
    diastolic_bp: num("diastolic_bp"),
    consciousness: $("consciousness").value,
    skin_pallor_or_cyanosis: $("skin_pallor_or_cyanosis").value,
    gait_abnormal: $("gait_abnormal").value,
    medications: list("medications"),
    conditions: list("conditions")
  });

  setStatus("goStatus","Assessing…","");
  try{
    const r = await fetch("/assess", {method:"POST", headers:{"Content-Type":"application/json"},
                                      body: JSON.stringify(payload)});
    const d = await r.json();
    if(d.error){ setStatus("goStatus","Rejected: " + d.error,"err"); return; }
    setStatus("goStatus","","");
    drawResult(d);
    $("result").scrollIntoView({behavior:"smooth", block:"nearest"});
  }catch(e){ setStatus("goStatus","Could not reach the engine: " + e.message,"err"); }
};

function drawResult(d){
  const cls = "b" + (d.band_code ? d.band_code.replace("L","") : "1");
  let h = '<div class="verdict">' +
    '<div class="score">' + d.risk_score + '<small>/100</small></div>' +
    '<div><span class="band ' + cls + '">' + d.band_code + " " + d.band_word + '</span>' +
    '<div class="conf" style="margin-top:6px">' + (d.band_meaning||"") + '</div></div>' +
    '<div class="conf">confidence <b>' + d.confidence_pct + '%</b><br>' +
    'plausible ' + (d.plausible_bands.join(", ") || d.band_code) + '<br>' +
    'data complete ' + d.data_completeness + '%</div>' +
    '<div class="conf">proposed by score <b>' + (d.proposed_band||"—") + '</b><br>' +
    'final <b>' + d.band_code + '</b><br>' + d.changed_by + '</div></div>';

  if(d.safety_rules && d.safety_rules.length){
    h += '<div class="rulebox"><b>Safety rules fired</b><br>' +
         d.safety_rules.join("<br>") +
         '<br><span style="color:#d0a0a0">A hard rule can raise a band above what the ' +
         'score alone would give. It can never lower one.</span></div>';
  }
  if(d.held){
    h += '<div class="rulebox" style="background:#1b2733;border-color:#2d4a5c;color:#a8cee8">' +
         '<b>Band held</b> — the score fell to ' + d.risk_score + ' and proposed ' +
         (d.proposed_band||"a lower band") + ', but the band stays at ' + d.band_code +
         '. ' + esc(d.change_reason) +
         '<br><span style="color:#7fa8c4">Nothing automated can lower a band. ' +
         'Only a named clinician can, with a logged reason.</span></div>';
  }
  if(d.escalated){
    h += '<div class="rulebox" style="background:#1d2f1d;border-color:#2d5a2d;color:#b7ebb7">' +
         '<b>Escalated</b> from ' + (d.previous_band||"—") + ' to ' + d.band_code +
         '. ' + (d.change_reason||"") + '</div>';
  }

  h += '<div class="grid2" style="margin-top:16px">' +
       '<div><label>Why this score</label><div class="readout">' +
       esc(d.panel_score) + '</div></div>' +
       '<div><label>Why this confidence</label><div class="readout">' +
       esc(d.panel_confidence) + '</div></div></div>';

  if(d.missing_fields && d.missing_fields.length){
    h += '<div style="margin-top:14px"><label>Not measured</label>' +
         '<div class="readout">' + d.missing_fields.join(", ") +
         '\nMissing data lowers confidence. It never lowers risk.</div></div>';
  }
  if(d.history && d.history.length > 1){
    h += '<div style="margin-top:14px"><label>This session</label><div class="readout">' +
      d.history.map(x => "t=" + String(x.at_minute).padStart(3) + "   risk " +
        String(Math.round(x.risk_score)).padStart(3) + "   " + x.band_word +
        "   " + x.changed_by).join("\n") + '</div></div>';
  }
  h += '<p class="hint">' + d.disclaimer + '</p>';
  $("result").innerHTML = h;
}

function esc(s){ return (s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;"); }
function setStatus(id, msg, cls){
  const el = $(id); el.textContent = msg; el.className = "status " + (cls||"");
}
</script>
</body>
</html>
"""
