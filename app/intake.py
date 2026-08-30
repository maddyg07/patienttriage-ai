"""
app/intake.py
=============
Phase 17. The live intake console: a doorway scan, a voice channel, a typed
channel, and the operator confirmation step between the sensors and the engine.

WHY THE BROWSER
---------------
Round 1 promised a doorway scan and a voice stream. Delivering that in Python
means OpenCV or MediaPipe plus an audio stack: several hundred megabytes, a
compiler on some machines, and a live demo that can fail in the room because a
wheel did not build. This repository has installed nothing since Phase 1 and
that is worth more than a logo on a slide.

Every browser ships a camera API, a microphone API, an audio analyser and a
speech recogniser. Served from 127.0.0.1 they need no certificate and no
install. So the capture layer is HTML and the engine stays where it was.

WHAT THIS FILE IS ALLOWED TO DO
-------------------------------
MEASURE, and format. The page produces numbers a browser is genuinely good at
producing -- luminance grids, amplitude envelopes -- and posts them to /fuse.
Everything that decides what those numbers MEAN is in core/capture_fusion.py,
where a test can read it. The page holds no threshold, no band and no score.

WHAT CHANGED AFTER THE FIRST LIVE TEST, AND WHY
-----------------------------------------------
Three failures, all found within minutes of somebody real sitting in front of
it, and all of them the same failure wearing different clothes.

  * A SYMMETRIC FACE READ AS STRONG ASYMMETRY. The first version compared two
    halves of a rectangle in a single frame. Side lighting makes one half
    darker, and so does turning your head. The fix is not a higher threshold,
    which only trades this error for the opposite one. It is to fit and remove
    the left-to-right luminance ramp before comparing anything, to require the
    reading to hold still across several frames, and to refuse the measurement
    outright when the lighting gradient is too strong to separate a shadow from
    a droop.

  * A LATE START READ AS BREATHLESSNESS. The microphone counted every gap,
    including the four seconds before the person began talking. Leading and
    trailing silence are now trimmed before anything is measured, breaks are
    counted per ten seconds OF SPEECH rather than in total, and a single
    sustained phrase is treated as evidence against being unable to finish a
    sentence.

  * SPOKEN SYMPTOMS WERE NOT COUNTED. They were, in fact -- the engine reads
    the transcript on submission -- but nothing on screen said so, which is the
    same thing from where the operator is sitting. The transcript is now read
    continuously as it fills, terms appear as chips the moment they are
    recognised, and every recognised term carries the channel it arrived on.

THE CHANNELS ANSWER TO EACH OTHER
---------------------------------
The deeper problem behind the first two is that each sensor was deciding alone.
A camera cannot tell a shadow from a droop. A microphone cannot tell a pause
from respiratory distress. Neither gets better in isolation.

So neither speaks alone any more. The two measurement sets go to /fuse
together, and a facial candidate with entirely fluent, well-sustained speech is
reported as UNCORROBORATED and carries no suggestion -- because a droop a
camera can see usually travels with dysarthria, and a picture that does not
cohere is exactly when a system should ask rather than assert.
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
    --ok:#3fb950; --accent:#a371f7; --warnc:#d9a441;
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
        padding:7px 20px;font-size:12px}
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
  section > h2 .spacer{flex:1}
  section > h2 .pill{font-size:10px;padding:2px 8px;border-radius:9px;
     border:1px solid var(--line);color:var(--faint);letter-spacing:.4px}
  .body{padding:16px}
  .full{grid-column:1/-1}

  video{width:100%;border-radius:4px;background:#000;display:block}
  .videowrap{position:relative}
  .videowrap .guide{position:absolute;border:1px dashed rgba(163,113,247,.65);
     pointer-events:none;border-radius:3px}
  .row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}

  button{background:var(--panel2);color:var(--ink);border:1px solid var(--line);
     padding:8px 14px;border-radius:4px;cursor:pointer;font-size:13px;
     font-family:inherit;transition:border-color .12s}
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
  textarea{min-height:92px;resize:vertical}
  input:focus,select:focus,textarea:focus{outline:none;border-color:var(--accent)}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  .grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
  @media(max-width:560px){.grid3{grid-template-columns:1fr 1fr}}

  .readout{background:var(--panel2);border:1px solid var(--line);border-radius:4px;
     padding:11px 13px;font:12px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;
     color:var(--dim);white-space:pre-wrap}
  .meter{height:6px;background:var(--bg);border-radius:3px;overflow:hidden;
     border:1px solid var(--line);margin-top:7px}
  .meter i{display:block;height:100%;background:var(--accent);width:0;
     transition:width .08s linear}

  .candidate{border:1px solid var(--line);border-left:3px solid var(--accent);
     background:var(--panel2);border-radius:4px;padding:11px 13px;margin-top:10px}
  .candidate.unreliable{border-left-color:var(--faint)}
  .candidate.uncorroborated{border-left-color:var(--warnc)}
  .candidate .q{font-size:13px;margin-bottom:7px}
  .candidate .src{font-size:11px;color:var(--faint);margin-bottom:8px;
     font-family:ui-monospace,Menlo,monospace;line-height:1.55}
  .candidate .corr{font-size:11px;color:var(--warnc);margin:8px 0;line-height:1.55}
  .choices{display:flex;gap:7px;flex-wrap:wrap}
  .choices button{padding:5px 12px;font-size:12px}
  .choices button.on{background:var(--accent);border-color:var(--accent);
     color:#0d1117;font-weight:600}
  .choices button.on.neg{background:var(--faint);border-color:var(--faint);color:#fff}

  .fusebar{border:1px solid var(--line);background:var(--panel2);border-radius:4px;
     padding:10px 13px;margin-bottom:6px;font-size:12px;color:var(--dim)}
  .fusebar b{color:var(--ink)}

  .chips{display:flex;gap:7px;flex-wrap:wrap;margin-top:9px;min-height:26px}
  .chip{background:var(--panel2);border:1px solid var(--line);border-radius:12px;
     padding:3px 10px;font-size:12px;display:flex;align-items:center;gap:7px}
  .chip.deny{border-style:dashed;color:var(--dim)}
  .chip em{font-style:normal;font-size:9px;color:var(--faint);
     letter-spacing:.5px;text-transform:uppercase}
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
  .hint{font-size:11px;color:var(--faint);margin-top:8px;line-height:1.55}
  .rulebox{background:#2a1618;border:1px solid #5c2626;border-radius:4px;
     padding:10px 12px;margin-top:12px;font-size:12px;color:#ffc9c9}
  .muted{color:var(--dim);font-size:12px}
  .status{font-size:12px;color:var(--dim);min-height:18px}
  .status.live{color:var(--ok)} .status.err{color:#ff8085}
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
  audio and transcript stay in this browser tab and are never written to disk.
  Only confirmed flags reach the engine.
</div>

<main>

<!-- ============== 1. DOORWAY SCAN ============== -->
<section>
  <h2><span class="step">1</span>Doorway scan
      <span class="spacer"></span><span class="pill" id="camPill">idle</span></h2>
  <div class="body">
    <div class="videowrap">
      <video id="cam" autoplay playsinline muted></video>
      <div class="guide" id="guide"></div>
    </div>
    <canvas id="work" style="display:none"></canvas>

    <div class="row" style="margin-top:12px">
      <button id="camStart">Start camera</button>
      <button id="camShot" disabled>Scan &mdash; hold still</button>
      <button id="camOff" disabled>Stop</button>
    </div>
    <div class="status" id="camStatus" style="margin-top:8px">Camera not started.</div>
    <div class="readout" id="camRead" style="margin-top:12px">no scan yet</div>

    <p class="hint">
      A scan takes several frames over about a second. The left-to-right
      lighting ramp is fitted and removed before the two halves are compared,
      because a shadow and a droop look identical to a raw comparison. If the
      reading moves between frames, or the lighting gradient is too strong, the
      measurement is rejected rather than interpreted.
    </p>
  </div>
</section>

<!-- ============== 2. VOICE ============== -->
<section>
  <h2><span class="step">2</span>Voice channel
      <span class="spacer"></span><span class="pill" id="micPill">idle</span></h2>
  <div class="body">
    <div class="row">
      <button id="micStart">Start listening</button>
      <button id="micStop" class="rec" disabled>Stop</button>
    </div>
    <div class="meter"><i id="level"></i></div>
    <div class="status" id="micStatus" style="margin-top:8px">Microphone not started.</div>

    <div style="margin-top:14px">
      <label>Transcript &mdash; spoken, editable, read as it fills</label>
      <textarea id="transcript" placeholder="Press Start listening and ask what brought the patient in. Symptoms appear below as they are recognised. If this browser has no speech recognition, type here instead — it is read the same way."></textarea>
    </div>

    <div class="readout" id="micRead" style="margin-top:12px">no speech analysed yet</div>

    <p class="hint">
      Leading and trailing silence are trimmed before anything is measured, and
      breaks are counted per ten seconds of speech. Taking a moment to start
      talking is not a respiratory sign.
    </p>
  </div>
</section>

<!-- ============== 3. WHAT THE SENSORS PROPOSE ============== -->
<section class="full">
  <h2><span class="step">3</span>What the sensors propose &mdash; confirm or correct each one</h2>
  <div class="body">
    <div class="fusebar" id="fusebar">
      No sensor readings yet. Scan and listen, or answer these from what you can
      see and hear &mdash; the engine treats an operator answer and a confirmed
      sensor candidate identically.
    </div>
    <div class="grid2">
      <div id="camQuestions"></div>
      <div id="micQuestions"></div>
    </div>
  </div>
</section>

<!-- ============== 4. SYMPTOMS ============== -->
<section>
  <h2><span class="step">4</span>What the patient reports
      <span class="spacer"></span><span class="pill" id="sxPill">0 terms</span></h2>
  <div class="body">
    <label>Additional notes typed by staff &mdash; also read live</label>
    <textarea id="typed" placeholder="Anything the patient said that is not in the transcript."></textarea>

    <div style="margin-top:14px">
      <label>Recognised &mdash; click a term to move it to denied</label>
      <div class="chips" id="sxChips"><span class="muted">nothing recognised yet</span></div>
    </div>
    <div style="margin-top:12px">
      <label>Explicitly denied &mdash; kept, never discarded</label>
      <div class="chips" id="denyChips"><span class="muted">none</span></div>
    </div>

    <div class="grid2" style="margin-top:14px">
      <div><label>Add a term manually</label>
        <select id="sxAdd"><option value="">select&hellip;</option></select></div>
      <div><label>Pain score (0&ndash;10)</label>
        <input id="pain" type="number" min="0" max="10" placeholder="blank = not asked"></div>
    </div>

    <div class="row" style="margin-top:12px">
      <button id="clearSx">Clear all terms</button>
    </div>

    <p class="hint">
      A denial is kept. If the patient denies breathlessness and the objective
      findings disagree, the engine records the conflict and raises risk. It
      never lowers risk on a denial.
    </p>
  </div>
</section>

<!-- ============== 5. OBJECTIVE ============== -->
<section>
  <h2><span class="step">5</span>Objective observations</h2>
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
      <div><label>Medications on file</label>
        <input id="medications" placeholder="apixaban, bisoprolol"></div>
      <div><label>Known conditions</label>
        <input id="conditions" placeholder="atrial fibrillation"></div>
    </div>

    <p class="hint">
      Leave a vital blank if it was not measured. Blank means unknown and lowers
      confidence. It never reads as normal.
    </p>
  </div>
</section>

<!-- ============== 6. ASSESS ============== -->
<section class="full">
  <h2><span class="step">6</span>Assessment</h2>
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
  demonstration value. Nothing here has been clinically validated and it must
  not be used for any real triage.
</footer>

<script>
const S = __SETTINGS__;
const $ = id => document.getElementById(id);

/* State. Flags only. Anything unanswered stays "unknown" to the engine. */
const flags = {
  facial_capture_status:"not_attempted",
  asymmetry_observed:"unknown", droop_observed:"unknown",
  visible_distress:"unknown", baseline_known:"unknown",
  baseline_asymmetry_present:"unknown", baseline_condition:"unknown",
  change_reported_as_new:"unknown",
  speech_abnormality:"unknown", unilateral_weakness:"unknown",
  voice_capture_status:"not_attempted", slurred_speech:"unknown",
  breathlessness_between_words:"unknown",
  unable_to_speak_full_sentence:"unknown", can_communicate:"unknown"
};
let symptoms = [];   /* {term, channel} */
let denied   = [];   /* {term, channel} */
let camStats = null, audStats = null;

/* ============ small maths helpers ============ */
const median = a => { if(!a.length) return 0; const s=[...a].sort((x,y)=>x-y);
  const m=s.length>>1; return s.length%2 ? s[m] : (s[m-1]+s[m])/2; };
const quantile = (a,q) => { if(!a.length) return 0; const s=[...a].sort((x,y)=>x-y);
  const i=(s.length-1)*q, lo=Math.floor(i), hi=Math.ceil(i);
  return lo===hi ? s[lo] : s[lo]+(s[hi]-s[lo])*(i-lo); };

/* =========================================================
   1. CAMERA — multi-frame, gradient-corrected
   ========================================================= */
let stream=null;
const cam=$("cam"), work=$("work");

function placeGuide(){
  const r=S.camera.analysis_region||{}, g=$("guide");
  g.style.left=((r.x_fraction||.22)*100)+"%";
  g.style.top=((r.y_fraction||.14)*100)+"%";
  g.style.width=((r.width_fraction||.56)*100)+"%";
  g.style.height=((r.height_fraction||.62)*100)+"%";
}
placeGuide(); window.addEventListener("resize", placeGuide);

$("camStart").onclick = async () => {
  try{
    stream = await navigator.mediaDevices.getUserMedia({video:{width:640}});
    cam.srcObject = stream;
    $("camStart").disabled=true; $("camShot").disabled=false; $("camOff").disabled=false;
    $("camPill").textContent="live";
    setStatus("camStatus","Camera live. Frames are analysed in this tab and discarded.","live");
  }catch(e){
    setStatus("camStatus","Camera unavailable: "+e.message+
      ". Everything else still works; the facial channel reports as failed.","err");
    flags.facial_capture_status="failed"; camStats=null; $("camPill").textContent="failed";
    refreshCandidates();
  }
};

$("camOff").onclick = () => {
  if(stream){ stream.getTracks().forEach(t=>t.stop()); stream=null; }
  $("camStart").disabled=false; $("camShot").disabled=true; $("camOff").disabled=true;
  $("camPill").textContent="stopped";
  setStatus("camStatus","Camera stopped.","");
};

/* One frame -> {index, gradient, structure, brightness} on the DETRENDED grid. */
function measureFrame(){
  const w = S.camera.frame_width||480;
  const h = Math.round(w*(cam.videoHeight/cam.videoWidth||0.75));
  work.width=w; work.height=h;
  const ctx = work.getContext("2d", {willReadFrequently:true});
  ctx.drawImage(cam,0,0,w,h);

  const r = S.camera.analysis_region||{};
  const rx=Math.round(w*(r.x_fraction||.22)), ry=Math.round(h*(r.y_fraction||.14));
  const rw=Math.round(w*(r.width_fraction||.56)), rh=Math.round(h*(r.height_fraction||.62));
  if(rw<8||rh<8) return null;
  const data = ctx.getImageData(rx,ry,rw,rh).data;

  const COLS=14, ROWS=18;
  const cw=Math.floor(rw/COLS), ch=Math.floor(rh/ROWS);
  if(cw<1||ch<1) return null;

  const grid=[], all=[];
  for(let cy=0; cy<ROWS; cy++){
    const row=[];
    for(let cx=0; cx<COLS; cx++){
      let sum=0,n=0;
      for(let y=cy*ch; y<(cy+1)*ch; y+=2){
        for(let x=cx*cw; x<(cx+1)*cw; x+=2){
          const i=(y*rw+x)*4;
          sum += 0.299*data[i]+0.587*data[i+1]+0.114*data[i+2]; n++;
        }
      }
      const v = n?sum/n:0; row.push(v); all.push(v);
    }
    grid.push(row);
  }
  const mean = all.reduce((a,b)=>a+b,0)/all.length;
  if(mean<=0) return null;

  /* Structure: how much variation is in the region at all. A blank wall is
     nearly flat; a face is not. Guards against measuring the background. */
  const variance = all.reduce((a,v)=>a+(v-mean)*(v-mean),0)/all.length;
  const structure = Math.sqrt(variance)/mean;

  /* Fit the left-to-right luminance ramp and remove it. Side lighting is a
     smooth ramp across the whole region; a droop is a localised difference.
     Without this step every reading is inflated by ordinary room lighting. */
  const colMean=[];
  for(let cx=0; cx<COLS; cx++){
    let s=0; for(let cy=0; cy<ROWS; cy++) s+=grid[cy][cx];
    colMean.push(s/ROWS);
  }
  const xbar=(COLS-1)/2;
  let num=0, den=0;
  for(let cx=0; cx<COLS; cx++){ num+=(cx-xbar)*(colMean[cx]-mean); den+=(cx-xbar)*(cx-xbar); }
  const slope = den?num/den:0;
  const gradient = Math.abs(slope)*(COLS-1)/mean;

  /* Mirrored comparison on the residual. */
  let diff=0, pairs=0;
  const half=Math.floor(COLS/2);
  for(let cy=0; cy<ROWS; cy++){
    for(let cx=0; cx<half; cx++){
      const mx=COLS-1-cx;
      const L=grid[cy][cx]-slope*(cx-xbar);
      const R=grid[cy][mx]-slope*(mx-xbar);
      diff += Math.abs(L-R); pairs++;
    }
  }
  const index = pairs ? (diff/pairs)/mean : 0;
  return {index, gradient, structure, brightness:mean};
}

$("camShot").onclick = async () => {
  $("camShot").disabled=true;
  $("camPill").textContent="scanning";
  setStatus("camStatus","Scanning — hold still…","");
  const frames=[];
  for(let i=0;i<9;i++){
    const m = measureFrame();
    if(m) frames.push(m);
    await new Promise(r=>setTimeout(r,130));
  }
  $("camShot").disabled=false; $("camPill").textContent="live";
  setStatus("camStatus","Camera live.","live");

  if(!frames.length){
    $("camRead").textContent="no usable frame captured";
    flags.facial_capture_status="failed"; camStats=null; refreshCandidates(); return;
  }
  const idx = frames.map(f=>f.index);
  camStats = {
    index: median(idx),
    spread: quantile(idx,0.75)-quantile(idx,0.25),
    gradient: median(frames.map(f=>f.gradient)),
    structure: median(frames.map(f=>f.structure)),
    brightness: median(frames.map(f=>f.brightness)),
    frames: frames.length
  };
  flags.facial_capture_status="ok";

  $("camRead").textContent =
    "scan complete — "+frames.length+" frames\n"+
    "  brightness        "+camStats.brightness.toFixed(0)+"\n"+
    "  structure         "+camStats.structure.toFixed(3)+"   (is there a face here at all)\n"+
    "  lighting gradient "+camStats.gradient.toFixed(3)+"   (side lighting, removed before comparing)\n"+
    "  symmetry index    "+camStats.index.toFixed(3)+"   (after gradient correction)\n"+
    "  frame-to-frame    "+camStats.spread.toFixed(3)+"   (a real difference does not flicker)\n"+
    "  These are measurements. What they MEAN is decided server-side and shown below.";
  refreshCandidates();
};

/* =========================================================
   2. MICROPHONE — trimmed, adaptive floor
   ========================================================= */
let audioCtx=null, analyser=null, micStream=null, timer=null, recog=null;
let samples=[], listening=false;

$("micStart").onclick = async () => {
  try{
    micStream = await navigator.mediaDevices.getUserMedia({audio:true});
    audioCtx = new (window.AudioContext||window.webkitAudioContext)();
    const src = audioCtx.createMediaStreamSource(micStream);
    analyser = audioCtx.createAnalyser(); analyser.fftSize=2048;
    src.connect(analyser);
    samples=[]; listening=true;
    const buf = new Float32Array(analyser.fftSize);
    timer = setInterval(()=>{
      analyser.getFloatTimeDomainData(buf);
      let s=0; for(let i=0;i<buf.length;i++) s+=buf[i]*buf[i];
      const rms=Math.sqrt(s/buf.length);
      samples.push(rms);
      $("level").style.width = Math.min(100, rms*900)+"%";
    }, S.audio.sample_ms||60);

    startRecognition();
    $("micStart").disabled=true; $("micStop").disabled=false;
    $("micPill").textContent="listening";
    setStatus("micStatus", recog
      ? "Listening. Speech recognition active. Take your time starting — leading silence is trimmed."
      : "Listening. This browser has no speech recognition; type the transcript instead.","live");
  }catch(e){
    setStatus("micStatus","Microphone unavailable: "+e.message+
      ". Type the transcript instead; the voice channel reports as failed.","err");
    flags.voice_capture_status="failed"; audStats=null; $("micPill").textContent="failed";
    refreshCandidates();
  }
};

$("micStop").onclick = () => {
  listening=false;
  if(timer){ clearInterval(timer); timer=null; }
  if(recog){ try{ recog.stop(); }catch(e){} }
  if(micStream){ micStream.getTracks().forEach(t=>t.stop()); micStream=null; }
  if(audioCtx){ audioCtx.close(); audioCtx=null; }
  $("level").style.width="0";
  $("micStart").disabled=false; $("micStop").disabled=true;
  $("micPill").textContent="stopped";
  setStatus("micStatus","Stopped.","");
  analyseSpeech();
  readText();
};

function startRecognition(){
  const R = window.SpeechRecognition || window.webkitSpeechRecognition;
  if(!R){ recog=null; return; }
  recog = new R();
  recog.continuous=true; recog.interimResults=true; recog.lang="en-IN";
  let settled="";
  recog.onresult = e => {
    let interim="";
    for(let i=e.resultIndex;i<e.results.length;i++){
      const t=e.results[i][0].transcript;
      if(e.results[i].isFinal) settled += t+" "; else interim += t;
    }
    $("transcript").value = (settled+interim).trim();
    /* Read as it fills. The first version waited for a button nobody pressed,
       so spoken symptoms looked like they were being ignored. */
    scheduleRead("voice");
  };
  recog.onerror = () => {};
  recog.onend = () => { if(listening){ try{ recog.start(); }catch(e){} } };
  try{ recog.start(); }catch(e){ recog=null; }
}

function analyseSpeech(){
  const ms = S.audio.sample_ms||60;
  if(samples.length<10){
    $("micRead").textContent="not enough audio captured to analyse";
    flags.voice_capture_status = samples.length?"ok":"not_attempted";
    audStats=null; refreshCandidates(); return;
  }
  /* Adaptive noise floor from the quietest fifth of the recording, rather
     than a fixed constant that is wrong in every second room. */
  const floorBase = quantile(samples,0.20);
  const peak = quantile(samples,0.95);
  const floor = Math.max(floorBase*2.2, 0.006);
  const snr = floorBase>0 ? peak/floorBase : 0;

  /* Trim leading and trailing silence. A late start is not a clinical sign. */
  let first=-1, last=-1;
  for(let i=0;i<samples.length;i++){ if(samples[i]>floor){ if(first<0) first=i; last=i; } }
  if(first<0 || last-first < 6){
    $("micRead").textContent =
      "speech analysed\n  no sustained speech detected above the noise floor\n"+
      "  nothing is inferred from this";
    flags.voice_capture_status="ok";
    audStats={speech_seconds:0, snr:snr, breaks_per_10s:0,
              median_phrase_ms:0, longest_phrase_ms:0};
    refreshCandidates(); return;
  }
  const win = samples.slice(first, last+1);
  const trimmedLead = (first*ms/1000).toFixed(1);

  const pauseMs = S.audio.pause_ms_for_break||320;
  const phrases=[]; let breaks=0, run=0, phrase=0, voiced=0;
  for(const rms of win){
    if(rms>floor){
      if(run>=pauseMs && phrase>0){ phrases.push(phrase); breaks++; phrase=0; }
      voiced++; phrase+=ms; run=0;
    }else{ run+=ms; }
  }
  if(phrase>0) phrases.push(phrase);

  const speechSec = voiced*ms/1000;
  audStats = {
    speech_seconds: speechSec,
    snr: snr,
    breaks_per_10s: speechSec>0 ? breaks/(speechSec/10) : 0,
    median_phrase_ms: median(phrases),
    longest_phrase_ms: phrases.length?Math.max(...phrases):0
  };
  flags.voice_capture_status="ok";
  flags.can_communicate = speechSec>1.5 ? "yes" : "unknown";

  $("micRead").textContent =
    "speech analysed\n"+
    "  trimmed lead-in   "+trimmedLead+" s   (silence before you started, ignored)\n"+
    "  speech            "+speechSec.toFixed(1)+" s\n"+
    "  signal to noise   "+snr.toFixed(1)+"\n"+
    "  breaks per 10s    "+audStats.breaks_per_10s.toFixed(1)+"   (of speech, not of recording)\n"+
    "  phrase length     median "+audStats.median_phrase_ms.toFixed(0)+
      " ms, longest "+audStats.longest_phrase_ms.toFixed(0)+" ms\n"+
    "  Measurements only. Interpretation happens server-side, below.";
  refreshCandidates();
}

/* =========================================================
   3. FUSION — the sensors answer to each other
   ========================================================= */
async function refreshCandidates(){
  let fused = null;
  if(camStats || audStats){
    const cam = camStats ? Object.assign({}, camStats, {
      visible_distress: flags.visible_distress==="yes" ? true :
                        flags.visible_distress==="no" ? false : null }) : null;
    try{
      const r = await fetch("/fuse",{method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({camera:cam, audio:audStats})});
      fused = await r.json();
    }catch(e){ fused=null; }
  }
  drawFuseBar(fused);
  drawCameraQuestions(fused);
  drawMicQuestions(fused);
}

function drawFuseBar(f){
  const bar=$("fusebar");
  if(!f){
    bar.innerHTML = "No sensor readings yet. Answer these from what you can see "+
      "and hear — the engine treats an operator answer and a confirmed sensor "+
      "candidate identically.";
    return;
  }
  let h = "<b>Cross-modal check: "+f.agreement+"</b>";
  if(f.notes && f.notes.length) h += "<br>" + f.notes.join("<br>");
  bar.innerHTML = h;
}

function drawCameraQuestions(f){
  const box=$("camQuestions");
  box.innerHTML="";
  if(flags.facial_capture_status==="failed"){
    box.appendChild(note("Facial channel failed",
      "capture_status = failed",
      "The engine continues on vitals, symptoms, history and observation, and "+
      "raises uncertainty because a modality is missing. A sensor failure is "+
      "not a clinical finding."));
    return;
  }
  const c = f && f.facial;
  box.appendChild(question(
    "Is the face asymmetric or drooping right now?",
    c ? c.reasons.join("\n") : "no camera reading — answer from what you see",
    ["yes","no","unknown"], "asymmetry_observed",
    c ? c.suggestion : null,
    v => { flags.droop_observed = v; },
    null, c ? c.corroboration : "", c ? c.strength : null
  ));
  box.appendChild(question(
    "Is this different from how the patient normally looks?",
    "no sensor can answer this — it decides stroke versus ordinary appearance",
    ["new","normal for them","cannot say"], "__baseline__", null, null,
    v => {
      if(v==="new"){ flags.baseline_known="yes"; flags.baseline_asymmetry_present="no";
        flags.change_reported_as_new="yes"; flags.baseline_condition="none"; }
      else if(v==="normal for them"){ flags.baseline_known="yes";
        flags.baseline_asymmetry_present="yes"; flags.change_reported_as_new="no"; }
      else { flags.baseline_known="unknown"; flags.baseline_asymmetry_present="unknown";
        flags.change_reported_as_new="unknown"; flags.baseline_condition="unknown"; }
    }
  ));
  box.appendChild(selectQuestion(
    "If this is their normal appearance, what is the documented reason?",
    "provenance only — it never changes the score",
    [["unknown","not documented"],["none","no baseline difference"],
     ["congenital","congenital"],["post_stroke","previous stroke"],
     ["burn_or_acid","burn or chemical injury"],["surgical","surgery"],
     ["trauma","previous trauma"],["chronic_palsy","chronic palsy"]],
    "baseline_condition"));
  box.appendChild(question(
    "New one-sided limb weakness?",
    "converts an isolated facial finding into a cluster",
    ["yes","no","unknown"], "unilateral_weakness", null));
  box.appendChild(question(
    "Patient visibly distressed?",
    "observed — also corroborates the breathing pattern",
    ["yes","no","unknown"], "visible_distress", null,
    () => { refreshCandidates(); }));
}

function drawMicQuestions(f){
  const box=$("micQuestions");
  box.innerHTML="";
  if(flags.voice_capture_status==="failed"){
    box.appendChild(note("Voice channel failed","capture_status = failed",
      "Scoring continues without it and uncertainty rises."));
    return;
  }
  const b = f && f.breathlessness, s = f && f.sentence;
  box.appendChild(question(
    "Breathless between words?",
    b ? b.reasons.join("\n") : "no audio reading — answer from what you hear",
    ["yes","no","unknown"], "breathlessness_between_words",
    b ? b.suggestion : null, null, null,
    b ? b.corroboration : "", b ? b.strength : null));
  box.appendChild(question(
    "Unable to complete a full sentence?",
    s ? s.reasons.join("\n") : "no audio reading — answer from what you hear",
    ["yes","no","unknown"], "unable_to_speak_full_sentence",
    s ? s.suggestion : null, null, null, "", s ? s.strength : null));
  box.appendChild(question(
    "Speech slurred or dysarthric?",
    "listen to the patient — no acoustic suggestion is offered, because "+
    "amplitude analysis cannot tell dysarthria from an accent",
    ["yes","no","unknown"], "slurred_speech", null,
    v => { flags.speech_abnormality = v; refreshCandidates(); }));
}

/* ============ widgets ============ */
function question(text, source, options, key, suggested, mirror, custom, corr, strength){
  const el=document.createElement("div");
  el.className="candidate" + (strength==="unreliable" ? " unreliable" : "") +
               (corr && corr.startsWith("Not corroborated") ? " uncorroborated" : "");
  el.innerHTML = '<div class="q">'+text+'</div><div class="src">'+esc(source)+'</div>';
  if(corr) el.innerHTML += '<div class="corr">'+esc(corr)+'</div>';
  const row=document.createElement("div"); row.className="choices";
  options.forEach(opt=>{
    const b=document.createElement("button");
    b.textContent = opt + (suggested===opt ? "  (suggested)" : "");
    if(flags[key]===opt && key!=="__baseline__"){ b.classList.add("on"); }
    b.onclick = () => {
      row.querySelectorAll("button").forEach(x=>x.classList.remove("on","neg"));
      b.classList.add("on");
      if(opt==="no"||opt==="normal for them") b.classList.add("neg");
      if(custom) custom(opt); else { flags[key]=opt; if(mirror) mirror(opt); }
    };
    row.appendChild(b);
  });
  el.appendChild(row);
  return el;
}

function selectQuestion(text, source, pairs, key){
  const el=document.createElement("div"); el.className="candidate";
  el.innerHTML='<div class="q">'+text+'</div><div class="src">'+source+'</div>';
  const sel=document.createElement("select");
  pairs.forEach(([v,l])=>{ const o=document.createElement("option");
    o.value=v; o.textContent=l; if(flags[key]===v) o.selected=true; sel.appendChild(o); });
  sel.onchange=()=>{ flags[key]=sel.value; };
  el.appendChild(sel); return el;
}

function note(title, src, body){
  const el=document.createElement("div"); el.className="candidate unreliable";
  el.innerHTML='<div class="q">'+title+'</div><div class="src">'+src+
    '</div><div class="muted">'+body+'</div>';
  return el;
}

/* =========================================================
   4. SYMPTOMS — read live from both channels
   ========================================================= */
(function fillVocab(){
  const sel=$("sxAdd");
  (S.vocabulary||[]).forEach(t=>{ const o=document.createElement("option");
    o.value=t; o.textContent=t; sel.appendChild(o); });
  sel.onchange=()=>{
    if(sel.value && !symptoms.some(s=>s.term===sel.value)){
      symptoms.push({term:sel.value, channel:"manual"}); drawChips(); }
    sel.value="";
  };
})();

let readTimer=null, lastRead="";
function scheduleRead(channel){
  clearTimeout(readTimer);
  readTimer = setTimeout(()=>readText(channel), 700);
}
$("transcript").addEventListener("input", ()=>scheduleRead("voice"));
$("typed").addEventListener("input", ()=>scheduleRead("typed"));

async function readText(channel){
  const t=$("transcript").value.trim(), y=$("typed").value.trim();
  const text=(t+" "+y).trim();
  if(!text || text===lastRead) return;
  lastRead=text;
  try{
    const r=await fetch("/read",{method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({text, transcript:t, typed:y})});
    const d=await r.json();
    const ch = c => c || channel || "text";
    (d.reported||[]).forEach(item=>{
      const term = item.term || item;
      const src  = item.channel || ch();
      if(!symptoms.some(s=>s.term===term) && !denied.some(s=>s.term===term))
        symptoms.push({term, channel:src});
    });
    (d.denied||[]).forEach(item=>{
      const term = item.term || item;
      const src  = item.channel || ch();
      symptoms = symptoms.filter(s=>s.term!==term);
      if(!denied.some(s=>s.term===term)) denied.push({term, channel:src});
    });
    if(d.pain_score!==null && d.pain_score!==undefined && !$("pain").value)
      $("pain").value = d.pain_score;
    drawChips();
  }catch(e){ /* the page stays usable without the reader */ }
}

$("clearSx").onclick=()=>{ symptoms=[]; denied=[]; lastRead=""; drawChips(); };

function drawChips(){
  const a=$("sxChips"), b=$("denyChips");
  a.innerHTML = symptoms.length ? "" : '<span class="muted">nothing recognised yet</span>';
  symptoms.forEach(s=>a.appendChild(chip(s,false,()=>{
    symptoms=symptoms.filter(x=>x.term!==s.term);
    if(!denied.some(x=>x.term===s.term)) denied.push({term:s.term, channel:"corrected"});
    drawChips();
  })));
  b.innerHTML = denied.length ? "" : '<span class="muted">none</span>';
  denied.forEach(s=>b.appendChild(chip(s,true,()=>{
    denied=denied.filter(x=>x.term!==s.term); drawChips();
  })));
  $("sxPill").textContent = symptoms.length + " term" +
    (symptoms.length===1?"":"s") + (denied.length ? ", "+denied.length+" denied" : "");
}

function chip(s,isDeny,onX){
  const c=document.createElement("div");
  c.className="chip"+(isDeny?" deny":"");
  const b=document.createElement("b"); b.textContent=s.term;
  const e=document.createElement("em"); e.textContent=s.channel;
  const x=document.createElement("span"); x.textContent="×";
  x.title=isDeny?"remove entirely":"move to denied";
  x.onclick=onX;
  c.appendChild(b); c.appendChild(e); c.appendChild(x);
  return c;
}

/* =========================================================
   6. ASSESS
   ========================================================= */
$("arrival_minute").oninput=()=>{
  $("clock").textContent="t = "+($("arrival_minute").value||0)+" min"; };

$("go").onclick = async () => {
  await readText();
  const num=id=>{ const v=$(id).value.trim(); return v===""?null:Number(v); };
  const list=id=>$(id).value.split(",").map(s=>s.trim()).filter(Boolean);

  const payload = Object.assign({}, flags, {
    patient_id:"LIVE-001",
    age_years:Number($("age").value||40),
    sex:$("sex").value,
    arrival_minute:Number($("arrival_minute").value||0),
    history_tier:$("hist").value,
    transcript:$("transcript").value,
    typed_symptoms:$("typed").value,
    added_symptoms:symptoms.map(s=>s.term),
    denied_symptoms:denied.map(s=>s.term),
    symptom_channels:symptoms.reduce((m,s)=>(m[s.term]=s.channel,m),{}),
    pain_score:num("pain"),
    heart_rate:num("heart_rate"), respiratory_rate:num("respiratory_rate"),
    spo2:num("spo2"), temperature_c:num("temperature_c"),
    systolic_bp:num("systolic_bp"), diastolic_bp:num("diastolic_bp"),
    consciousness:$("consciousness").value,
    skin_pallor_or_cyanosis:$("skin_pallor_or_cyanosis").value,
    gait_abnormal:$("gait_abnormal").value,
    medications:list("medications"), conditions:list("conditions")
  });

  setStatus("goStatus","Assessing…","");
  try{
    const r=await fetch("/assess",{method:"POST",
      headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
    const d=await r.json();
    if(d.error){ setStatus("goStatus","Rejected: "+d.error,"err"); return; }
    setStatus("goStatus","","");
    drawResult(d);
    $("result").scrollIntoView({behavior:"smooth", block:"nearest"});
  }catch(e){ setStatus("goStatus","Could not reach the engine: "+e.message,"err"); }
};

function drawResult(d){
  const cls="b"+(d.band_code?d.band_code.replace("L",""):"1");
  let h='<div class="verdict">'+
    '<div class="score">'+d.risk_score+'<small>/100</small></div>'+
    '<div><span class="band '+cls+'">'+d.band_code+" "+d.band_word+'</span>'+
    '<div class="conf" style="margin-top:6px">'+(d.band_meaning||"")+'</div></div>'+
    '<div class="conf">confidence <b>'+d.confidence_pct+'%</b><br>'+
    'plausible '+(d.plausible_bands.join(", ")||d.band_code)+'<br>'+
    'data complete '+d.data_completeness+'%</div>'+
    '<div class="conf">proposed by score <b>'+(d.proposed_band||"—")+'</b><br>'+
    'final <b>'+d.band_code+'</b><br>'+d.changed_by+'</div></div>';

  /* What the engine actually counted. Its absence was why spoken symptoms
     looked ignored even when they were not. */
  h += '<div class="grid2"><div><label>Symptoms counted</label><div class="readout">'+
       (d.symptoms.length ? d.symptoms.join("\n") : "none recognised")+'</div></div>'+
       '<div><label>Denials recorded — never subtract</label><div class="readout">'+
       (d.denies.length ? d.denies.join("\n") : "none")+'</div></div></div>';

  if(d.safety_rules && d.safety_rules.length){
    h+='<div class="rulebox"><b>Safety rules fired</b><br>'+d.safety_rules.join("<br>")+
       '<br><span style="color:#d0a0a0">A hard rule can raise a band above what '+
       'the score alone gives. It can never lower one.</span></div>';
  }
  if(d.held){
    h+='<div class="rulebox" style="background:#1b2733;border-color:#2d4a5c;color:#a8cee8">'+
       '<b>Band held</b> — the score fell to '+d.risk_score+' and proposed '+
       (d.proposed_band||"lower")+', but the band stays at '+d.band_code+'. '+
       esc(d.change_reason)+'<br><span style="color:#7fa8c4">Nothing automated '+
       'can lower a band. Only a named clinician can, with a logged reason.</span></div>';
  }
  if(d.escalated){
    h+='<div class="rulebox" style="background:#1d2f1d;border-color:#2d5a2d;color:#b7ebb7">'+
       '<b>Escalated</b> from '+(d.previous_band||"—")+' to '+d.band_code+'. '+
       esc(d.change_reason)+'</div>';
  }

  h+='<div class="grid2" style="margin-top:16px">'+
     '<div><label>Why this score</label><div class="readout">'+esc(d.panel_score)+'</div></div>'+
     '<div><label>Why this confidence</label><div class="readout">'+
     esc(d.panel_confidence)+'</div></div></div>';

  if(d.missing_fields && d.missing_fields.length){
    h+='<div style="margin-top:14px"><label>Not measured</label><div class="readout">'+
       d.missing_fields.join(", ")+
       '\nMissing data lowers confidence. It never lowers risk.</div></div>';
  }
  if(d.history && d.history.length>1){
    h+='<div style="margin-top:14px"><label>This session</label><div class="readout">'+
      d.history.map(x=>"t="+String(x.at_minute).padStart(3)+"   risk "+
        String(Math.round(x.risk_score)).padStart(3)+"   "+x.band_word+
        "   "+x.changed_by).join("\n")+'</div></div>';
  }
  h+='<p class="hint">'+d.disclaimer+'</p>';
  $("result").innerHTML=h;
}

function esc(s){ return (s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;"); }
function setStatus(id,msg,cls){ const el=$(id); el.textContent=msg;
  el.className="status "+(cls||""); }

drawChips();
refreshCandidates();
</script>
</body>
</html>
"""
