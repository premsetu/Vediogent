#!/usr/bin/env python3
"""
app.py — Flask web interface for Vediogent video generator.

Run:
  cd /home/user/Vediogent
  python webapp/app.py
"""
import json
import os
import sys
import threading
import time
import uuid
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from flask import Flask, Response, jsonify, render_template, request, send_file

app = Flask(__name__, template_folder="templates", static_folder="static")

# In-memory job store  {job_id: {status, log, output_path, opts}}
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()

STYLE_PRESETS = [
    {"id": "dark-epic",       "label": "Dark Epic",       "desc": "Deep navy + purple bloom"},
    {"id": "dark-cinematic",  "label": "Dark Cinematic",  "desc": "Pure black + gold accents"},
    {"id": "blue-tech",       "label": "Blue Tech",       "desc": "Dark navy + cyan neon"},
    {"id": "warm-gold",       "label": "Warm Gold",       "desc": "Dark amber + gold tones"},
    {"id": "minimal-light",   "label": "Minimal Light",   "desc": "White background + clean type"},
]

VOICE_OPTIONS = [
    {"id": "gtts:en",                "label": "Google TTS (English)"},
    {"id": "edge:en-US-GuyNeural",   "label": "Edge — Guy (US Male)"},
    {"id": "edge:en-US-JennyNeural", "label": "Edge — Jenny (US Female)"},
    {"id": "edge:en-GB-RyanNeural",  "label": "Edge — Ryan (UK Male)"},
    {"id": "edge:en-AU-WilliamNeural","label": "Edge — William (AU Male)"},
]


# ── Job runner ─────────────────────────────────────────────────────────────────

class _Tee:
    """Write to both a list (log capture) and stdout."""
    def __init__(self, log_list):
        self._log = log_list

    def write(self, text):
        if text.strip():
            self._log.append(text.rstrip())

    def flush(self):
        pass


def _run_generation(job_id: str, opts: dict):
    import make_video as mv   # import here so path is resolved

    job = JOBS[job_id]
    log = job["log"]
    tee = _Tee(log)

    # Monkey-patch print so the generation logs flow into our log list
    import builtins
    _orig_print = builtins.print

    def _patched_print(*args, **kwargs):
        kwargs.setdefault("file", tee)
        _orig_print(*args, **kwargs)
        _orig_print(*args)   # also to real stdout

    builtins.print = _patched_print

    try:
        job["status"] = "running"

        out_dir = Path("out") / f"job_{job_id[:8]}"
        beats_dir = out_dir / "beats"
        out_dir.mkdir(parents=True, exist_ok=True)
        beats_dir.mkdir(exist_ok=True)

        # Resolve style
        style = mv.style_from_prompt(opts.get("prompt", ""))
        # Override preset if user picked one explicitly
        preset_id = opts.get("style_preset", "")
        if preset_id and preset_id in mv.STYLES:
            style = dict(mv.STYLES[preset_id], name=preset_id)

        # YouTube analysis
        yt_analysis = {}
        ref_url = opts.get("ref_url", "").strip()
        if ref_url:
            try:
                yt_analysis = mv.analyze_youtube(ref_url, out_dir)
                style = mv.apply_yt_analysis(style, yt_analysis, opts.get("prompt", ""))
            except Exception as e:
                log.append(f"[warn] YouTube analysis failed: {e}")

        script_text = opts.get("script", "")
        beat_texts  = mv.parse_beats(script_text)
        voice_spec  = opts.get("voice", "gtts:en")

        # Narrate
        log.append(f"▶ narrate  ({len(beat_texts)} beats)")
        beats = []
        cumulative = 0.0
        beat_wavs  = []

        for i, text in enumerate(beat_texts):
            wav = beats_dir / f"beat_{i:03d}.wav"
            log.append(f"  [{i}] synthesising…")
            dur = mv.synthesize(text, voice_spec, wav)
            beats.append({"index": i, "text": text,
                          "start": cumulative, "end": cumulative + dur})
            cumulative += dur + mv.SILENCE_MS / 1000.0
            beat_wavs.append(wav)

        narration_wav = out_dir / "narration.wav"
        total_dur     = mv.concat_wavs(beat_wavs, mv.SILENCE_MS, narration_wav)
        log.append(f"  narration: {total_dur:.1f}s")

        # Visuals
        log.append("▶ visuals")
        beat_videos = []
        for b in beats:
            vid = beats_dir / f"beat_{b['index']:03d}.mp4"
            dur = b["end"] - b["start"]
            log.append(f"  [{b['index']}] rendering card ({dur:.1f}s)…")
            mv.make_beat_video(b["text"], dur, style, b["index"], vid)
            beat_videos.append(vid)

        silent_track = out_dir / "silent_track.mp4"
        mv.concat_videos(beat_videos, silent_track)

        # Captions
        log.append("▶ captions")
        ass_path = out_dir / "captions.ass"
        ass_path.write_text(mv.build_ass(beats, style), encoding="utf-8")

        # Assemble
        log.append("▶ assemble")
        master = out_dir / "master.mp4"
        mv._run([
            "ffmpeg", "-y",
            "-i", str(silent_track), "-i", str(narration_wav),
            "-map", "0:v", "-map", "1:a",
            "-vf", f"ass={ass_path}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "17",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            "-shortest", str(master),
        ])

        size_mb = master.stat().st_size / 1e6
        log.append(f"✅ Done — master.mp4  ({size_mb:.1f} MB, {total_dur:.0f}s)")
        job["status"]      = "done"
        job["output_path"] = str(master)
        job["size_mb"]     = round(size_mb, 1)
        job["duration"]    = round(total_dur, 1)

    except Exception as exc:
        import traceback
        log.append(f"❌ Error: {exc}")
        log.append(traceback.format_exc())
        job["status"] = "error"

    finally:
        builtins.print = _orig_print


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html",
                           style_presets=STYLE_PRESETS,
                           voice_options=VOICE_OPTIONS)


@app.route("/generate", methods=["POST"])
def generate():
    data = request.get_json(force=True)
    script = (data.get("script") or "").strip()
    if not script:
        return jsonify({"error": "Script is required"}), 400

    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "queued",
            "log": ["Queued…"],
            "output_path": None,
            "opts": data,
        }

    t = threading.Thread(target=_run_generation, args=(job_id, data), daemon=True)
    t.start()

    return jsonify({"job_id": job_id})


@app.route("/status/<job_id>")
def status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({
        "status":      job["status"],
        "log":         job["log"],
        "output_path": job.get("output_path"),
        "size_mb":     job.get("size_mb"),
        "duration":    job.get("duration"),
    })


@app.route("/video/<job_id>")
def video(job_id):
    job = JOBS.get(job_id)
    if not job or not job.get("output_path"):
        return "Not found", 404
    path = Path(job["output_path"])
    if not path.exists():
        return "Video file missing", 404
    return send_file(str(path), mimetype="video/mp4",
                     as_attachment=False, download_name="vediogent_output.mp4")


@app.route("/download/<job_id>")
def download(job_id):
    job = JOBS.get(job_id)
    if not job or not job.get("output_path"):
        return "Not found", 404
    path = Path(job["output_path"])
    if not path.exists():
        return "Video file missing", 404
    return send_file(str(path), mimetype="video/mp4",
                     as_attachment=True, download_name="vediogent_output.mp4")


@app.route("/jobs")
def list_jobs():
    with JOBS_LOCK:
        return jsonify([
            {"id": jid, "status": j["status"],
             "size_mb": j.get("size_mb"), "duration": j.get("duration")}
            for jid, j in list(JOBS.items())[-10:]
        ])


if __name__ == "__main__":
    os.chdir(Path(__file__).parent.parent)   # run from repo root
    print("🎬  Vediogent  →  http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
