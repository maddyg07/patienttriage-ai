"""
app/nurse.py
============
The clinical oversight screen. Dense on purpose.

WHY IT LOOKS NOTHING LIKE THE PATIENT SCREEN
--------------------------------------------
Different reader, different job. The patient screen hides the score because a
number frightens somebody who cannot interpret it. This screen leads with the
score, the confidence, the plausible band set, every trigger with the phrase
that caused it, and every contradiction between what was said and what was
observed, because the person reading it can interpret all of that and needs to.

WHAT THE NURSE CAN CHANGE
-------------------------
Everything the machine decided. Severity up, severity down, remove a symptom
the extraction invented, add one it missed, confirm or dismiss an observation,
dismiss an emergency trigger with a reason, rewrite the notes.

Two things they cannot do, and both are deliberate. They cannot delete the
event log, and they cannot make an override untraceable: every change writes a
row with the nurse's name, the before, the after and the reason. That is what
makes the override a clinical act rather than an edit.

The AI recommendation and the nurse's decision are both kept, side by side. If
this were ever measured against outcomes, the disagreements are where the
learning is, and a system that overwrites the machine's answer with the human's
has thrown that away.
"""

from __future__ import annotations

import json
from typing import Optional


def render_nurse(vocabulary: Optional[list] = None) -> str:
    return _PAGE.replace("__VOCAB__", json.dumps(sorted(vocabulary or [])))


_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PatientTriage.ai &mdash; Nurse Console</title>
<style>
  :root{ --bg:#0d1117; --panel:#151b23; --panel2:#1c2530; --line:#2a3441;
         --ink:#e6edf3; --dim:#8b98a5; --faint:#5c6773;
         --normal:#3fb950; --mon:#4a9eda; --con:#d9a441; --high:#e07b39;
         --emg:#e5484d; --accent:#a371f7; }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
       font:13px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
  header{background:var(--panel);border-bottom:1px solid var(--line);
         padding:11px 20px;display:flex;align-items:center;gap:16px;
         position:sticky;top:0;z-index:30}
  header h1{margin:0;font-size:15px;font-weight:600}
  .pill{font-size:11px;padding:3px 10px;border-radius:11px;
        border:1px solid var(--line);color:var(--dim);letter-spacing:.4px}
  .pill.normal{border-color:var(--normal);color:var(--normal)}
  .pill.monitoring{border-color:var(--mon);color:var(--mon)}
  .pill.concerning{border-color:var(--con);color:var(--con)}
  .pill\.high{border-color:var(--high)}
  .pill.emergency{background:var(--emg);border-color:var(--emg);color:#fff;
                  font-weight:700;animation:flash 1.1s infinite}
  @keyframes flash{0%,100%{opacity:1}50%{opacity:.55}}
  .spacer{flex:1}
  main{display:grid;grid-template-columns:1.15fr 1fr;gap:14px;
       padding:14px;max-width:1680px;margin:0 auto;align-items:start}
  @media(max-width:1100px){main{grid-template-columns:1fr}}
  section{background:var(--panel);border:1px solid var(--line);border-radius:6px;
          margin-bottom:14px}
  section>h2{margin:0;padding:10px 15px;font-size:11px;font-weight:600;
     letter-spacing:1.1px;text-transform:uppercase;color:var(--dim);
     border-bottom:1px solid var(--line);display:flex;gap:10px;align-items:center}
  .body{padding:14px}
  .full{grid-column:1/-1}

  .emg-banner{background:#2a1214;border:2px solid var(--emg);border-radius:6px;
              padding:16px 18px;margin-bottom:14px}
  .emg-banner h3{margin:0 0 4px;color:#ff8085;font-size:17px;letter-spacing:.6px}
  .trig{background:#1c2530;border-left:3px solid var(--emg);border-radius:4px;
        padding:9px 12px;margin-top:8px;font-size:12px}
  .trig .q{color:#ffc9c9;font-family:ui-monospace,Menlo,monospace;margin:4px 0}
  .trig.dead{border-left-color:var(--faint);opacity:.5}

  .verdict{display:flex;gap:20px;align-items:center;flex-wrap:wrap}
  .verdict .n{font-size:34px;font-weight:700;line-height:1}
  .verdict .n small{font-size:13px;color:var(--faint);font-weight:400}
  .band{padding:4px 12px;border-radius:3px;font-weight:700;letter-spacing:1px}
  .b1{background:#12324d;color:var(--mon)} .b2{background:#3d3115;color:var(--con)}
  .b3{background:#452414;color:var(--high)} .b4{background:#4a1517;color:#ff8085}
  .meta{font-size:12px;color:var(--dim)}

  table{width:100%;border-collapse:collapse;font-size:12px}
  th{text-align:left;color:var(--faint);font-weight:500;padding:6px 8px;
     border-bottom:1px solid var(--line);font-size:11px;text-transform:uppercase;
     letter-spacing:.5px}
  td{padding:7px 8px;border-bottom:1px solid #1e2630;vertical-align:top}
  tr.gone td{opacity:.36;text-decoration:line-through}
  td .said{color:var(--faint);font-size:11px;font-family:ui-monospace,Menlo,monospace}
  .ovr{color:var(--accent);font-size:10px;letter-spacing:.4px}

  button{background:var(--panel2);color:var(--ink);border:1px solid var(--line);
         padding:4px 9px;border-radius:4px;cursor:pointer;font:12px inherit}
  button:hover{border-color:var(--accent)}
  button.sm{padding:2px 7px;font-size:11px}
  button.danger:hover{border-color:var(--emg);color:#ff8085}
  button.ack{background:var(--emg);border-color:var(--emg);color:#fff;
             font-weight:600;padding:8px 18px}
  input,select,textarea{background:var(--bg);color:var(--ink);
     border:1px solid var(--line);border-radius:4px;padding:5px 8px;font:12px inherit}
  input.sev{width:52px;text-align:center}
  textarea{width:100%;min-height:420px;font:12px/1.6 ui-monospace,Menlo,monospace;
           resize:vertical}

  .tl{max-height:340px;overflow-y:auto;font:11.5px/1.7 ui-monospace,Menlo,monospace}
  .tl .r{display:flex;gap:9px;padding:2px 0;border-bottom:1px solid #1a212a}
  .tl .t{color:var(--faint);flex:none;width:66px}
  .tl .k{flex:none;width:120px;color:var(--dim)}
  .tl .r.emergency_trigger .k,.tl .r.emergency .k{color:#ff8085}
  .tl .r.override .k{color:var(--accent)}
  .tl .r.contradiction .k,.tl .r.conflict .k{color:var(--con)}
  .tl .r.speech .k{color:var(--mon)}

  .obs{background:var(--panel2);border:1px solid var(--line);border-radius:4px;
       padding:9px 11px;margin-top:8px;font-size:12px}
  .obs.conf{border-left:3px solid var(--normal)}
  .obs.dismissed{opacity:.45}
  .conflict{background:#2a2414;border:1px solid #5c4a26;border-radius:4px;
            padding:10px 12px;margin-top:8px;font-size:12px;color:#f0dfae}
  .muted{color:var(--dim)} .warn{color:var(--con)}
  .quote{font-family:ui-monospace,Menlo,monospace;color:var(--faint);font-size:11px}
  .readout{background:var(--panel2);border:1px solid var(--line);border-radius:4px;
     padding:10px 12px;font:11.5px/1.6 ui-monospace,Menlo,monospace;
     color:var(--dim);white-space:pre-wrap;max-height:280px;overflow-y:auto}
  .empty{color:var(--faint);font-size:12px;padding:6px 0}
</style>
</head>
<body>

<header>
  <h1>PatientTriage.ai</h1>
  <span class="pill" id="statusPill">no session</span>
  <span class="pill" id="sessionPill">&mdash;</span>
  <span class="spacer"></span>
  <span class="pill" id="providerPill">&mdash;</span>
  <span class="pill" id="livePill">connecting</span>
</header>

<main>
  <div class="full" id="emgSlot"></div>

  <div>
    <section>
      <h2>Assessment</h2>
      <div class="body" id="verdict"><span class="empty">Waiting for a patient.</span></div>
    </section>

    <section>
      <h2>Symptoms <span class="spacer"></span>
          <span class="muted" style="font-size:10px;text-transform:none">
            severity is editable; removals are logged</span></h2>
      <div class="body">
        <table id="sxTable"><thead><tr>
          <th>finding</th><th>severity</th><th>detail</th><th>heard</th><th></th>
        </tr></thead><tbody id="sxBody"></tbody></table>
        <div style="margin-top:11px;display:flex;gap:8px;flex-wrap:wrap">
          <select id="addTerm"><option value="">add a finding&hellip;</option></select>
          <input class="sev" id="addSev" type="number" min="0" max="10" placeholder="sev">
          <button id="addBtn">Add</button>
        </div>
      </div>
    </section>

    <section>
      <h2>Multimodal observations</h2>
      <div class="body" id="obs"><span class="empty">None recorded.</span></div>
    </section>
  </div>

  <div>
    <section>
      <h2>Timeline</h2>
      <div class="body"><div class="tl" id="tl"></div></div>
    </section>

    <section>
      <h2>Transcript</h2>
      <div class="body"><div class="readout" id="tx">&mdash;</div></div>
    </section>

    <section>
      <h2>Why this score</h2>
      <div class="body"><div class="readout" id="why">&mdash;</div></div>
    </section>
  </div>

  <section class="full">
    <h2>Clinical notes <span class="spacer"></span>
        <span class="muted" style="font-size:10px;text-transform:none">
          AI-generated draft &mdash; edit before filing</span></h2>
    <div class="body">
      <textarea id="notes"></textarea>
      <div style="margin-top:10px;display:flex;gap:9px;align-items:center">
        <button id="saveNotes">Save notes</button>
        <button id="regen">Regenerate from timeline</button>
        <span class="muted" id="noteStatus"></span>
      </div>
    </div>
  </section>
</main>

<script>
const VOCAB = __VOCAB__;
const $ = id => document.getElementById(id);
let sessionId = null, state = null, notesDirty = false, ackedEmg = false;

(function fill(){
  const s = $("addTerm");
  VOCAB.forEach(t => { const o = document.createElement("option");
    o.value = t; o.textContent = t; s.appendChild(o); });
})();

$("notes").addEventListener("input", () => { notesDirty = true;
  $("noteStatus").textContent = "unsaved"; });

async function boot(){
  const r = await fetch("/api/sessions");
  const list = (await r.json()).sessions;
  if(!list.length){ setTimeout(boot, 1500); return; }
  sessionId = list[list.length - 1];
  $("sessionPill").textContent = sessionId;
  const es = new EventSource("/api/stream?session_id=" + encodeURIComponent(sessionId));
  es.onmessage = e => { $("livePill").textContent = "live";
    render(JSON.parse(e.data)); };
  es.onerror = () => { $("livePill").textContent = "reconnecting"; };
}
boot();

function post(path, body){
  return fetch(path, {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify(Object.assign({session_id: sessionId}, body))});
}

function render(s){
  state = s;
  $("statusPill").textContent = s.status.toUpperCase();
  $("statusPill").className = "pill " + s.status.replace(" ", "");
  $("providerPill").textContent = s.provider + (s.degraded ? " · DEGRADED" : "");

  drawEmergency(s);
  drawVerdict(s);
  drawSymptoms(s);
  drawObservations(s);
  drawTimeline(s);
  drawTranscript(s);
  $("why").textContent = (s.assessment && s.assessment.panel_score) || "—";
  if(!notesDirty) $("notes").value = s.notes || "";
}

function drawEmergency(s){
  const slot = $("emgSlot");
  if(!s.emergency.active && !s.emergency.triggers.length){ slot.innerHTML = ""; return; }
  const e = s.emergency;
  let h = '<div class="emg-banner"><h3>' +
    (e.active ? "EMERGENCY — ROUTINE QUESTIONING STOPPED" : "EMERGENCY STOOD DOWN") +
    '</h3><div class="muted">declared ' + (e.declared_at || "—") +
    (e.acknowledged_by ? "  ·  acknowledged by " + e.acknowledged_by +
      " at " + e.acknowledged_at : "  ·  NOT ACKNOWLEDGED") + '</div>';
  e.triggers.forEach(t => {
    h += '<div class="trig' + (t.active ? '' : ' dead') + '"><b>' + t.trigger_id +
         '</b> <span class="muted">(' + t.layer + ', ' + t.at_clock + ')</span><br>' +
         t.why;
    if(t.evidence) h += '<div class="q">&ldquo;' + esc(t.evidence) + '&rdquo;</div>';
    if(t.dismissed_by){
      h += '<div class="muted">dismissed by ' + t.dismissed_by + ': ' +
           esc(t.dismiss_reason) + '</div>';
    }else{
      h += '<div style="margin-top:6px"><button class="sm danger" ' +
           'onclick="dismissTrigger(\'' + t.trigger_id + '\')">Dismiss this trigger</button></div>';
    }
    h += '</div>';
  });
  if(e.active && !e.acknowledged_by){
    h += '<div style="margin-top:13px"><button class="ack" onclick="ack()">' +
         'Acknowledge — I am attending</button></div>';
  }
  h += '</div>';
  slot.innerHTML = h;
}

function ack(){ post("/api/nurse/acknowledge", {nurse: "N. Sharma"}); }
function dismissTrigger(id){
  const reason = prompt("Why is this not an emergency? (recorded)");
  if(reason === null) return;
  post("/api/nurse/dismiss", {trigger_id: id, reason, nurse: "N. Sharma"});
}

function drawVerdict(s){
  const a = s.assessment;
  if(!a || !a.band_code){ $("verdict").innerHTML =
    '<span class="empty">No assessment yet.</span>'; return; }
  const cls = "b" + a.band_code.replace("L", "");
  $("verdict").innerHTML =
    '<div class="verdict"><div class="n">' + a.risk_score + '<small>/100</small></div>' +
    '<div><span class="band ' + cls + '">' + a.band_code + " " + a.band_word +
    '</span><div class="meta" style="margin-top:5px">' + (a.band_meaning || "") +
    '</div></div><div class="meta">confidence <b>' + a.confidence_pct + '%</b><br>' +
    'plausible ' + (a.plausible_bands || []).join(", ") + '<br>data ' +
    a.data_completeness + '%</div><div class="meta">proposed <b>' +
    (a.proposed_band || "—") + '</b><br>final <b>' + a.band_code + '</b><br>' +
    a.changed_by + '</div></div>' +
    ((a.safety_rules || []).length
      ? '<div class="conflict" style="margin-top:11px"><b>Safety rules</b><br>' +
        a.safety_rules.join("<br>") + '</div>' : "") +
    ((a.uncertainty_drivers || []).length
      ? '<div class="meta" style="margin-top:10px"><b>Uncertainty</b><br>' +
        a.uncertainty_drivers.map(d => "· " + esc(d)).join("<br>") + '</div>' : "");
}

function drawSymptoms(s){
  const b = $("sxBody");
  b.innerHTML = "";
  if(!s.symptoms.length){
    b.innerHTML = '<tr><td colspan="5" class="empty">Nothing extracted yet.</td></tr>';
    return;
  }
  s.symptoms.forEach(x => {
    const tr = document.createElement("tr");
    if(!x.active) tr.className = "gone";
    const detail = [x.onset, x.progression, x.laterality,
                    x.duration_hours != null ? x.duration_hours + " h" : "",
                    x.uncertain ? "patient unsure" : ""].filter(Boolean).join(", ");
    tr.innerHTML =
      '<td><b>' + x.normalised + '</b>' +
        (x.overridden ? '<div class="ovr">NURSE ADJUSTED</div>' : '') +
        (x.added_by ? '<div class="ovr">ADDED BY ' + x.added_by.toUpperCase() + '</div>' : '') +
        '<div class="said">conf ' + x.confidence + ' · ' + x.source + '</div></td>' +
      '<td><input class="sev" type="number" min="0" max="10" value="' +
        (x.severity == null ? "" : x.severity) + '" ' +
        'onchange="setSev(\'' + x.term + '\', this.value)"></td>' +
      '<td class="muted">' + esc(detail || "—") + '</td>' +
      '<td class="said">' + esc((x.said || "").slice(0, 72)) + '</td>' +
      '<td>' + (x.active
        ? '<button class="sm danger" onclick="removeSx(\'' + x.term + '\')">remove</button>'
        : '<button class="sm" onclick="restoreSx(\'' + x.term + '\')">restore</button>') +
      '</td>';
    b.appendChild(tr);
  });
  if(s.denials && s.denials.length){
    const tr = document.createElement("tr");
    tr.innerHTML = '<td colspan="5" class="muted" style="padding-top:10px">' +
      '<b>Explicitly denied</b> (recorded, never subtracted): ' +
      s.denials.map(d => d.normalised).join(", ") + '</td>';
    b.appendChild(tr);
  }
}

function setSev(term, v){
  post("/api/nurse/severity", {term, severity: v === "" ? null : Number(v),
                               nurse: "N. Sharma"});
}
function removeSx(term){
  const reason = prompt("Why is this not correct? (recorded)");
  if(reason === null) return;
  post("/api/nurse/remove", {term, reason, nurse: "N. Sharma"});
}
function restoreSx(term){ post("/api/nurse/add", {term, nurse: "N. Sharma"}); }
$("addBtn").onclick = () => {
  const t = $("addTerm").value; if(!t) return;
  const sev = $("addSev").value;
  post("/api/nurse/add", {term: t, severity: sev === "" ? null : Number(sev),
                          nurse: "N. Sharma"});
  $("addTerm").value = ""; $("addSev").value = "";
};

function drawObservations(s){
  const box = $("obs");
  const all = (s.visual_observations || []).concat(s.audio_observations || []);
  const flags = s.review_flags || [];
  if(!all.length && !flags.length){
    box.innerHTML = '<span class="empty">None recorded.</span>'; return;
  }
  let h = "";
  flags.forEach(f => {
    h += '<div class="conflict' + (f.status !== "unreviewed" ? ' dismissed' : '') +
      '"><b>&#9888; Review recommended</b> &middot; ' + f.at_clock + '<br>' +
      esc(f.why) + '<br><span class="quote">patient said: &ldquo;' +
      esc(f.statement) + '&rdquo;</span><br>' +
      '<span class="muted">' +
      (f.visual ? '&#128065; visual observation disagrees. ' : '') +
      (f.audio ? '&#127908; voice observation disagrees. ' : '') +
      'Not escalated to emergency: both signals are kept, risk was not lowered ' +
      'on the statement, and questioning continues.</span>';
    if(f.nurse_note) h += '<br><span class="muted">nurse: ' + esc(f.nurse_note) + '</span>';
    if(f.status === "unreviewed"){
      h += '<div style="margin-top:6px;display:flex;gap:6px">' +
        '<button class="sm" onclick="reviewFlag(\'' + f.flag_id + '\',\'reviewed\')">reviewed</button>' +
        '<button class="sm danger" onclick="reviewFlag(\'' + f.flag_id + '\',\'dismissed\')">dismiss</button></div>';
    }
    h += '</div>';
  });
  all.forEach(o => {
    h += '<div class="obs ' + (o.status === "confirmed" ? "conf" :
         o.status === "dismissed" ? "dismissed" : "") + '">' +
      '<b>[' + o.id + ']</b> ' + o.at_clock + ' &middot; ' + esc(o.description) +
      '<div class="muted">recorded as an observation, not a diagnosis' +
      (o.nurse_note ? ' — ' + esc(o.nurse_note) : '') + '</div>';
    if(o.status === "unreviewed"){
      h += '<div style="margin-top:6px;display:flex;gap:6px">' +
        '<button class="sm" onclick="reviewObs(\'' + o.id + '\',\'confirmed\')">confirm</button>' +
        '<button class="sm danger" onclick="reviewObs(\'' + o.id + '\',\'dismissed\')">dismiss</button>' +
        '<button class="sm" onclick="noteObs(\'' + o.id + '\')">add note</button></div>';
    }
    h += '</div>';
  });
  box.innerHTML = h;
}

function reviewFlag(id, status){
  post("/api/nurse/flag", {flag_id: id, status, nurse: "N. Sharma"});
}
function reviewObs(id, status){
  post("/api/nurse/observation", {observation_id: id, status, nurse: "N. Sharma"});
}
function noteObs(id){
  const note = prompt("Note on this observation");
  if(note === null) return;
  post("/api/nurse/observation", {observation_id: id, status: "confirmed",
                                  note, nurse: "N. Sharma"});
}

function drawTimeline(s){
  const el = $("tl");
  const stick = el.scrollTop + el.clientHeight >= el.scrollHeight - 30;
  el.innerHTML = (s.timeline || []).map(e =>
    '<div class="r ' + e.kind + '"><span class="t">' + e.at_clock + '</span>' +
    '<span class="k">' + e.kind + '</span><span>' + esc(e.text) +
    (e.actor !== "system" ? ' <span class="muted">[' + e.actor + ']</span>' : '') +
    '</span></div>').join("");
  if(stick) el.scrollTop = el.scrollHeight;
}

function drawTranscript(s){
  $("tx").textContent = (s.transcript || []).map(
    t => t.at_clock + "  " + t.text).join("\n") || "—";
}

$("saveNotes").onclick = async () => {
  await post("/api/nurse/notes", {text: $("notes").value, nurse: "N. Sharma"});
  notesDirty = false; $("noteStatus").textContent = "saved";
};
$("regen").onclick = async () => {
  await post("/api/nurse/notes", {text: "", nurse: "N. Sharma"});
  notesDirty = false; $("noteStatus").textContent = "regenerated from the timeline";
};

function esc(s){ return (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;"); }
</script>
</body>
</html>
"""
