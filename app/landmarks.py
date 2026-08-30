"""
app/landmarks.py
================
Optional face-landmark geometry for the doorway scan, and the reasoning about
why the obvious dataset answer is the wrong one.

WHY NOT A PUBLIC FACIAL-EXPRESSION DATASET
------------------------------------------
The obvious move is FER2013, AffectNet or CK+. It is the wrong move, for four
reasons in descending order of how much they matter.

1. THEY CLASSIFY THE WRONG THING. Those datasets label seven basic emotions:
   happy, sad, angry, surprised, fearful, disgusted, neutral. A model trained
   on them can tell you a face looks "sad". It cannot tell you a face is
   drooping on one side, and it has never seen a clinical presentation of
   anything. Wiring one in would let this console print "AI detected distress"
   backed by a classifier trained on actors posing expressions to camera. That
   is a worse failure than a crude honest measurement, because it looks like
   evidence.

2. POSED, NOT CLINICAL. CK+ and much of AffectNet are posed or web-scraped
   expressions of healthy people. Pain, respiratory distress and neurological
   deficit are not in the label space at any point, so accuracy on the
   benchmark says nothing whatever about accuracy on a patient.

3. LICENSING. AffectNet is research-only and non-commercial. CK+ requires a
   signed agreement. FER2013 carries Kaggle competition terms. The brief asks
   for legally usable, appropriately licensed data, and a prototype that is
   being architected toward a commercial product cannot be built on any of
   those without a licence nobody in this team holds.

4. IT WOULD END THE ZERO-DEPENDENCY PROPERTY for a capability that does not
   answer the question that matters. Even a perfect distress classifier still
   cannot say whether a facial difference arrived this morning or at birth,
   and that single question decides stroke versus ordinary appearance. It is
   answered by asking, which this console already does.

THE HONEST IMPROVEMENT, WHICH IS THIS FILE
------------------------------------------
MediaPipe Face Landmarker. Apache 2.0, runs in the browser as WASM, and --
this is the point -- involves NO TRAINING DATA ON OUR PART and no dataset
licence. It returns 478 3D facial landmarks. Landmarks are geometry, not a
judgement, so a measurement derived from them is a measurement rather than a
classifier's opinion dressed as one.

That turns the luminance symmetry index into something considerably better:
the mouth-corner height difference, the eye-aperture difference and the
eyebrow-height difference, each mirrored about the face's own midline, in
units of the patient's own interocular distance so it does not vary with how
far they sit from the camera. Illumination stops mattering, because geometry
is not luminance.

It still cannot tell you whether the difference is new. Nothing can, from an
image. The baseline question stays exactly where it is.

OPT-IN, AND IT FAILS SOFT
-------------------------
Loading it needs a CDN, which breaks the offline property this repository has
had since Phase 1 and introduces a way for a live demo to fail in a room with
bad wifi. So it is off by default and enabled with --landmarks. If the load
fails, times out, or no face is found, the console silently falls back to the
luminance measurement and says which one produced the reading.
"""

from __future__ import annotations

MEDIAPIPE_CDN = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14"

# Landmark indices in the MediaPipe Face Landmarker 478-point mesh.
# Left and right are the IMAGE's left and right, not the patient's.
LANDMARKS = {
    "mouth_left": 61, "mouth_right": 291,
    "eye_left_top": 159, "eye_left_bottom": 145,
    "eye_right_top": 386, "eye_right_bottom": 374,
    "brow_left": 105, "brow_right": 334,
    "eye_left_outer": 33, "eye_right_outer": 263,
    "nose_tip": 1, "chin": 152,
}


LANDMARK_SCRIPT = r"""
/* ---------------------------------------------------------------
   Optional face-landmark geometry.

   Loaded only when the server was started with --landmarks. Everything below
   degrades to the luminance measurement on any failure, and the reading always
   carries which method produced it so a nurse is never shown a geometric
   figure that was actually a brightness comparison.
--------------------------------------------------------------- */
let landmarker = null, landmarkState = "off";

async function initLandmarks(){
  if(!window.__USE_LANDMARKS__){ landmarkState = "off"; return; }
  landmarkState = "loading";
  try{
    const vision = await import("__CDN__/vision_bundle.mjs");
    const files = await vision.FilesetResolver.forVisionTasks("__CDN__/wasm");
    landmarker = await vision.FaceLandmarker.createFromOptions(files, {
      baseOptions: {
        modelAssetPath: "https://storage.googleapis.com/mediapipe-models/" +
          "face_landmarker/face_landmarker/float16/1/face_landmarker.task"
      },
      runningMode: "VIDEO", numFaces: 1
    });
    landmarkState = "ready";
  }catch(e){
    /* No network, blocked CDN, unsupported browser. Not an error worth
       showing a patient: the luminance path still works. */
    landmarker = null; landmarkState = "unavailable";
  }
}

/* Geometric asymmetry, normalised to the patient's own interocular distance
   so it does not change with how far they are sitting from the camera. */
function measureLandmarks(video){
  if(!landmarker || !video.videoWidth) return null;
  let result;
  try{ result = landmarker.detectForVideo(video, performance.now()); }
  catch(e){ return null; }
  const faces = result && result.faceLandmarks;
  if(!faces || !faces.length) return null;
  const p = faces[0];
  const L = __LANDMARKS__;
  const at = k => p[L[k]];
  if(!at("mouth_left") || !at("eye_right_outer")) return null;

  const dx = at("eye_left_outer").x - at("eye_right_outer").x;
  const dy = at("eye_left_outer").y - at("eye_right_outer").y;
  const scale = Math.hypot(dx, dy);
  if(scale < 0.02) return null;          /* face too small to measure */

  const mouthDrop = Math.abs(at("mouth_left").y - at("mouth_right").y) / scale;
  const eyeL = Math.abs(at("eye_left_top").y - at("eye_left_bottom").y);
  const eyeR = Math.abs(at("eye_right_top").y - at("eye_right_bottom").y);
  const eyeGap = Math.abs(eyeL - eyeR) / scale;
  const browGap = Math.abs(at("brow_left").y - at("brow_right").y) / scale;

  /* Head roll. A tilted head produces every one of the differences above
     without any facial asymmetry at all, which is the geometric equivalent of
     the side-lighting problem the luminance path had. */
  const roll = Math.abs(Math.atan2(dy, dx));

  return {
    index: (mouthDrop * 0.5 + eyeGap * 0.3 + browGap * 0.2),
    mouthDrop, eyeGap, browGap, roll,
    method: "landmark geometry"
  };
}
"""


def landmark_script(enabled: bool) -> str:
    """The browser-side landmark code, or a stub that always declines."""
    if not enabled:
        return ("let landmarker = null, landmarkState = 'off';\n"
                "async function initLandmarks(){}\n"
                "function measureLandmarks(){ return null; }\n")
    import json
    return (LANDMARK_SCRIPT
            .replace("__CDN__", MEDIAPIPE_CDN)
            .replace("__LANDMARKS__", json.dumps(LANDMARKS)))
