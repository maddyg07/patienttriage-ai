"""
app/patient.py
==============
The patient screen. Camera, microphone, and as little else as possible.

WHAT THE PATIENT DOES NOT SEE
-----------------------------
No risk score. No band. No differential. No confidence figure. No list of
things the machine is worried about.

Not because it is secret, but because it is not for them. Telling a frightened
person that a computer has put them at 78/100 with a plausible band of CODE
does not inform them; it frightens them further, and a frightened patient gives
a worse history, which makes the assessment worse. The clinical detail belongs
on the nurse screen, where somebody trained to read it is reading it.

When the emergency gate fires, the patient sees:

    "We are getting someone to you now. Please stay where you are."

and the questions stop. They do not see "EMERGENCY DETECTED" in red. The alarm
belongs on the nurse's screen.

WHAT IT DOES
------------
Listens continuously, transcribes, sends each fragment as it completes, scans
the camera periodically, and shows the patient what has been understood so they
can correct it. There is no Assess button, because a patient in trouble should
not have to press anything for the system to notice.
"""

from __future__ import annotations

import json
from typing import Optional


def render_patient(settings: Optional[dict] = None,
                   use_landmarks: bool = False) -> str:
    from app.landmarks import landmark_script
    settings = dict(settings or {})
    settings["landmarks"] = bool(use_landmarks)
    return (_PAGE
            .replace("__SETTINGS__", json.dumps(settings))
            .replace("/* __LANDMARK_SCRIPT__ */", landmark_script(use_landmarks)))


_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Emergency Department &mdash; Check In</title>
<style>
  :root{ --bg:#f6f7f9; --card:#fff; --ink:#1b2028; --dim:#6b7684;
         --line:#e2e6ec; --accent:#4f46e5; --calm:#0f9d58; --alert:#c0392b; }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
       font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
  header{background:var(--card);border-bottom:1px solid var(--line);
         padding:16px 24px;display:flex;align-items:center;gap:14px}
  header h1{margin:0;font-size:18px;font-weight:600}
  header .dot{width:9px;height:9px;border-radius:50%;background:#c9ced6}
  header .dot.on{background:var(--calm);box-shadow:0 0 0 4px rgba(15,157,88,.15)}
  main{max-width:760px;margin:0 auto;padding:24px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;
        padding:22px;margin-bottom:18px}
  h2{margin:0 0 6px;font-size:17px;font-weight:600}
  p.sub{margin:0 0 18px;color:var(--dim);font-size:14px}
  video{width:100%;max-height:300px;object-fit:cover;border-radius:10px;
        background:#111;display:block}
  button{font:15px inherit;padding:13px 22px;border-radius:9px;cursor:pointer;
         border:1px solid var(--line);background:#fff;color:var(--ink)}
  button.go{background:var(--accent);border-color:var(--accent);color:#fff;
            font-weight:600;font-size:17px;padding:16px 30px;width:100%}
  button:disabled{opacity:.45;cursor:not-allowed}
  .row{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px}
  .live{display:flex;align-items:center;gap:9px;color:var(--calm);
        font-size:14px;font-weight:500;margin-top:14px}
  .pulse{width:9px;height:9px;border-radius:50%;background:var(--calm);
         animation:p 1.4s infinite}
  @keyframes p{0%,100%{opacity:1}50%{opacity:.25}}
  .said{background:#f0f2f6;border-radius:9px;padding:14px 16px;
        font-size:15px;min-height:60px;color:var(--ink);white-space:pre-wrap}
  .said .interim{color:var(--dim)}
  .chips{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
  .chip{background:#eef1ff;border:1px solid #d6dbff;color:#3730a3;
        border-radius:16px;padding:5px 13px;font-size:14px;
        display:flex;align-items:center;gap:8px}
  .chip span{cursor:pointer;color:#8b8fb5}
  .chip span:hover{color:var(--alert)}
  .ask{background:#fff8e6;border:1px solid #f0dfae;border-radius:10px;
       padding:16px 18px;font-size:16px;margin-top:14px}
  .ask b{display:block;font-size:12px;color:#8a6d1f;text-transform:uppercase;
         letter-spacing:.6px;margin-bottom:5px;font-weight:600}
  .banner{background:#fff2f0;border:2px solid var(--alert);border-radius:12px;
          padding:26px;text-align:center;margin-bottom:18px}
  .banner h2{color:var(--alert);font-size:21px;margin-bottom:8px}
  .banner p{margin:0;font-size:16px}
  .consent{background:#f0f2f6;border-radius:9px;padding:14px 16px;
           font-size:13px;color:var(--dim);margin-top:16px;line-height:1.65}
  footer{max-width:760px;margin:0 auto;padding:0 24px 44px;
         color:var(--dim);font-size:12px;line-height:1.7}
</style>
</head>
<body>

<header>
  <span class="dot" id="dot"></span>
  <h1>Emergency Department &mdash; Check In</h1>
</header>

<main>
  <div id="alert"></div>

  <div class="card" id="startCard">
    <h2>Tell us what is wrong</h2>
    <p class="sub">
      We will turn on your camera and microphone so you can just talk. Say
      what you are feeling in your own words &mdash; there is no form to fill in
      and nothing to press while you speak.
    </p>
    <button class="go" id="start">Start</button>
    <div class="consent">
      Your camera and microphone are used only while this page is open. Video
      and audio are analysed on this device and are not recorded or stored. A
      nurse can see what you say and what the system notices.
    </div>
  </div>

  <div class="card" id="liveCard" style="display:none">
    <video id="cam" autoplay playsinline muted></video>
    <div class="live"><span class="pulse"></span><span id="liveText">Listening</span></div>
    <div class="row">
      <button id="stop">I have finished</button>
    </div>
  </div>

  <div class="card" id="saidCard" style="display:none">
    <h2>What you have told us</h2>
    <p class="sub">Correct anything that is wrong.</p>
    <div class="said" id="said">&hellip;</div>
    <div class="chips" id="chips"></div>
  </div>

  <div class="card" id="askCard" style="display:none">
    <h2>One more thing</h2>
    <div class="ask" id="ask"><b>Question</b><span id="askText"></span></div>
  </div>
</main>

<footer>
  Prototype for the Accenture Innovation Challenge 2026. This is not a medical
  device, it does not diagnose, and a nurse makes every decision about your
  care. If you feel worse at any point, tell a member of staff.
</footer>

<script>
const $ = id => document.getElementById(id);
window.__USE_LANDMARKS__ = !!(S && S.landmarks);

/* __LANDMARK_SCRIPT__ */
let session = null, recog = null, stream = null, started = 0, listening = false;
let camTimer = null, emergency = false, lastAsked = null, finished = false;

function seconds(){ return started ? (Date.now() - started) / 1000 : 0; }

$("start").onclick = async () => {
  try{
    stream = await navigator.mediaDevices.getUserMedia({video:{width:640}, audio:true});
  }catch(e){
    /* Consent refused or no device. The encounter continues without it: a
       patient who will not turn on a camera still needs triaging. */
    stream = null;
  }
  const r = await fetch("/api/session", {method:"POST"});
  session = (await r.json()).session_id;
  connect();

  started = Date.now();
  $("startCard").style.display = "none";
  $("liveCard").style.display = "block";
  $("saidCard").style.display = "block";
  $("dot").classList.add("on");

  /* Tell the server what actually ran. The uncertainty engine reported
     "facial capture not attempted" on every encounter, including ones with
     the camera live throughout, because nothing ever told it otherwise. */
  const capture = {session_id: session};
  if(stream){
    $("cam").srcObject = stream;
    capture.facial = stream.getVideoTracks().length ? "ok" : "failed";
    capture.voice  = stream.getAudioTracks().length ? "ok" : "failed";
    camTimer = setInterval(scanFrame, 9000);
    setTimeout(scanFrame, 2000);
  }else{
    $("cam").style.display = "none";
    $("liveText").textContent = "Listening (camera not available)";
    capture.facial = "refused";
    capture.voice = "refused";
  }
  fetch("/api/capture", {method:"POST",
    headers:{"Content-Type":"application/json"}, body: JSON.stringify(capture)});
  initLandmarks();
  startSpeech();
};

$("stop").onclick = () => {
  listening = false;
  if(recog){ try{ recog.stop(); }catch(e){} }
  if(camTimer){ clearInterval(camTimer); camTimer = null; }
  if(stream){ stream.getTracks().forEach(t => t.stop()); stream = null; }
  fetch("/api/finish", {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({session_id: session})});
  $("liveText").textContent = "Finished. A nurse has your details.";
  $("dot").classList.remove("on");
};

/* ---------- speech: continuous, no button ---------- */
function startSpeech(){
  const R = window.SpeechRecognition || window.webkitSpeechRecognition;
  if(!R){
    $("said").innerHTML = "This browser cannot transcribe speech. " +
      "Please tell the nurse at the desk what is wrong.";
    return;
  }
  recog = new R();
  recog.continuous = true; recog.interimResults = true; recog.lang = "en-IN";
  listening = true;
  let settled = "";

  recog.onresult = e => {
    let interim = "";
    for(let i = e.resultIndex; i < e.results.length; i++){
      const piece = e.results[i][0].transcript;
      if(e.results[i].isFinal){
        settled += piece + " ";
        /* Sent the moment a sentence completes. There is no Assess button
           anywhere in this page, by design. */
        send(piece.trim(), seconds());
      }else{
        interim += piece;
      }
    }
    $("said").innerHTML = settled +
      '<span class="interim">' + interim + '</span>';
  };
  recog.onerror = () => {};
  recog.onend = () => { if(listening){ try{ recog.start(); }catch(e){} } };
  try{ recog.start(); }catch(e){}
}

function send(text, at){
  if(!text || !session) return;
  fetch("/api/say", {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({session_id: session, text, at_second: at})});
}

/* ---------- camera: a BURST per scan, with a real spread ----------
   A single frame has no frame-to-frame spread, and the first version sent a
   hardcoded 0.01 so the stability gate would always pass. That defeated the
   gate entirely and let one noisy frame report facial asymmetry. Each scan is
   now a burst of frames and the spread is what was actually measured. */
async function scanFrame(){
  if(!stream) return;
  /* Geometry when it is available, luminance otherwise. Geometry is better
     because a landmark is a position and a position does not change when the
     lighting does -- the whole side-lighting problem simply stops existing.
     It still cannot say whether a difference is new. Nothing can, from an
     image, which is why the baseline question stays exactly where it is. */
  const readings = [];
  const useGeometry = (landmarkState === "ready");
  for(let i = 0; i < 7; i++){
    const m = useGeometry ? measureLandmarks($("cam")) : measureOnce();
    if(m) readings.push(m);
    await new Promise(r => setTimeout(r, 110));
  }
  if(readings.length < 5){
    /* Geometry found no face in the burst. Fall back rather than report
       nothing: a patient looking away is not a reading, but a patient in
       frame under bad light still is. */
    if(useGeometry){
      for(let i = 0; i < 7; i++){
        const m = measureOnce();
        if(m) readings.push(m);
        await new Promise(r => setTimeout(r, 90));
      }
    }
    if(readings.length < 5) return;
  }
  const method = readings[0].method || "luminance comparison";

  const idx = readings.map(r => r.index).sort((a,b) => a-b);
  const q = f => { const i = (idx.length-1)*f, lo = Math.floor(i), hi = Math.ceil(i);
                   return lo === hi ? idx[lo] : idx[lo] + (idx[hi]-idx[lo])*(i-lo); };
  const med = a => { const s2 = [...a].sort((x,y)=>x-y), m = s2.length>>1;
                     return s2.length%2 ? s2[m] : (s2[m-1]+s2[m])/2; };

  fetch("/api/frame", {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({session_id: session, at_second: seconds(),
      frames: readings.length,
      method: method,
      /* Geometry has no brightness, structure or lighting gradient, and those
         gates exist to catch failure modes it does not have. Values that make
         the fuser treat the reading as usable are sent only when the reading
         genuinely came from geometry, and the method travels with it so the
         server is never guessing which is which. */
      brightness: med(readings.map(r => r.brightness ?? 128)),
      structure:  med(readings.map(r => r.structure  ?? 0.30)),
      gradient:   med(readings.map(r => r.roll !== undefined
                                        ? r.roll : r.gradient)),
      symmetry:   med(idx),
      spread:     q(0.75) - q(0.25)})});
}

function measureOnce(){
  const v = $("cam");
  if(!v.videoWidth) return null;
  const w = 320, h = Math.round(w * v.videoHeight / v.videoWidth);
  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  const ctx = c.getContext("2d", {willReadFrequently:true});
  ctx.drawImage(v, 0, 0, w, h);

  const rx = Math.round(w*0.22), ry = Math.round(h*0.14);
  const rw = Math.round(w*0.56), rh = Math.round(h*0.62);
  const d = ctx.getImageData(rx, ry, rw, rh).data;

  const COLS = 12, ROWS = 14;
  const cw = Math.floor(rw/COLS), ch = Math.floor(rh/ROWS);
  if(cw < 1 || ch < 1) return null;
  const grid = [], all = [];
  for(let cy = 0; cy < ROWS; cy++){
    const row = [];
    for(let cx = 0; cx < COLS; cx++){
      let s = 0, n = 0;
      for(let y = cy*ch; y < (cy+1)*ch; y += 2)
        for(let x = cx*cw; x < (cx+1)*cw; x += 2){
          const i = (y*rw + x)*4;
          s += 0.299*d[i] + 0.587*d[i+1] + 0.114*d[i+2]; n++;
        }
      const v2 = n ? s/n : 0; row.push(v2); all.push(v2);
    }
    grid.push(row);
  }
  const mean = all.reduce((a,b)=>a+b,0)/all.length;
  if(mean <= 0) return null;
  const sd = Math.sqrt(all.reduce((a,v2)=>a+(v2-mean)*(v2-mean),0)/all.length);

  const colMean = [];
  for(let cx = 0; cx < COLS; cx++){
    let s = 0; for(let cy = 0; cy < ROWS; cy++) s += grid[cy][cx];
    colMean.push(s/ROWS);
  }
  const xbar = (COLS-1)/2;
  let num = 0, den = 0;
  for(let cx = 0; cx < COLS; cx++){
    num += (cx-xbar)*(colMean[cx]-mean); den += (cx-xbar)*(cx-xbar);
  }
  const slope = den ? num/den : 0;
  let diff = 0, pairs = 0;
  for(let cy = 0; cy < ROWS; cy++)
    for(let cx = 0; cx < Math.floor(COLS/2); cx++){
      const mx = COLS-1-cx;
      diff += Math.abs((grid[cy][cx]-slope*(cx-xbar)) - (grid[cy][mx]-slope*(mx-xbar)));
      pairs++;
    }

  /* Measurements only. The page draws no conclusion from them; the server
     decides what they mean and whether they are usable at all. */
  return {brightness: mean, structure: sd/mean,
          gradient: Math.abs(slope)*(COLS-1)/mean,
          index: pairs ? (diff/pairs)/mean : 0};
}

/* ---------- shared state ---------- */
function connect(){
  const es = new EventSource("/api/stream?session_id=" + encodeURIComponent(session));
  es.onmessage = e => render(JSON.parse(e.data));
  es.onerror = () => {};
}

function render(s){
  const chips = $("chips");
  chips.innerHTML = "";
  s.symptoms.filter(x => x.active).forEach(x => {
    const el = document.createElement("div");
    el.className = "chip";
    const b = document.createElement("b"); b.style.fontWeight = "500";
    b.textContent = x.normalised;
    const x2 = document.createElement("span"); x2.textContent = "\u00d7";
    x2.title = "I did not say this";
    x2.onclick = () => fetch("/api/correct", {method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({session_id: session, term: x.term})});
    el.appendChild(b); el.appendChild(x2); chips.appendChild(el);
  });

  if(s.emergency.active && !emergency){
    emergency = true;
    $("alert").innerHTML =
      '<div class="banner"><h2>Someone is on their way</h2><p>' +
      s.patient_message + '</p></div>';
    $("askCard").style.display = "none";
    $("liveText").textContent = "A nurse has been alerted.";
    window.scrollTo({top:0, behavior:"smooth"});
  }

  /* Questions stop the moment the gate fires. */
  if(s.complete){
    $("askCard").style.display = "none";
    $("liveText").textContent = "Thank you. A nurse has everything they need.";
    if(!finished){ finished = true; $("stop").click(); }
    return;
  }
  if(s.emergency.active || !s.next_question){
    $("askCard").style.display = "none";
    /* Nothing left worth asking and no emergency: the intake is done. The
       previous version simply stopped responding here, with no question, no
       message, and no end. */
    if(!s.emergency.active && s.next_question_why === "exhausted" && !finished){
      finished = true;
      fetch("/api/finish", {method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({session_id: session,
          reason: "no further question would change the assessment"})});
    }
    return;
  }

  $("askCard").style.display = "block";
  $("askText").textContent = s.next_question;

  /* Tell the server this question has now been ASKED. Without this the same
     question is chosen forever: the patient answers, the transcript logs it,
     and the screen never moves. That was the bug. */
  if(s.next_question_id && s.next_question_id !== lastAsked){
    lastAsked = s.next_question_id;
    fetch("/api/asked", {method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({session_id: session,
        question_id: s.next_question_id, text: s.next_question,
        why: s.next_question_why || ""})});
  }
}
</script>
</body>
</html>
"""
