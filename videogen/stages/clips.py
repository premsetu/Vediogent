"""
clips.py — Cut N short vertical clips from master.mp4.

1. Feed captions.json transcript to LLM → pick N segments with strong hooks.
2. For each segment: cut from master.mp4, re-crop to 9:16, re-burn larger captions.
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

_CLIP_STYLE = (
    "Style: Default,Impact,80,&H00FFFFFF,&H00FFFF00,&H00000000,&H80000000,"
    "1,0,0,0,100,100,0,0,1,5,2,2,20,20,60,1"
)

_ASS_CLIP_HEADER = """\
[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
Timer: 100.0000

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{style}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _run_ffmpeg(*args, check=True):
    cmd = ["ffmpeg", "-y", *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"FFmpeg error:\n{result.stderr[-2000:]}")
    return result


def _ts(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    cs = int((s % 1) * 100)
    return f"{h}:{m:02d}:{int(s):02d}.{cs:02d}"


def _pick_segments_llm(captions: list, n: int, clip_len: int, prompt_ctx: str) -> list:
    """Ask LLM for N good clip segments. Returns list of {start, end, title}."""
    transcript = "\n".join(f"[{w['start']:.1f}s] {w['word']}" for w in captions[::3])

    instruction = (
        f"You are a video editor. Here is a word-level transcript with timestamps:\n\n"
        f"{transcript}\n\n"
        f"Global style: {prompt_ctx or 'engaging'}\n\n"
        f"Pick {n} self-contained segments of approximately {clip_len} seconds each "
        f"that would make compelling short-form vertical clips with strong hooks. "
        f"Each segment should start with an attention-grabbing moment.\n\n"
        f"Respond ONLY with a JSON array, no markdown, no explanation:\n"
        f'[{{"start": 0.0, "end": 30.0, "title": "Hook title"}}, ...]'
    )

    backend = config.LLM_BACKEND
    raw = ""
    try:
        if backend == "claude":
            import anthropic
            client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
            msg = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                messages=[{"role": "user", "content": instruction}],
            )
            raw = msg.content[0].text.strip()
        elif backend == "openai":
            from openai import OpenAI
            client = OpenAI(api_key=config.OPENAI_API_KEY)
            resp = client.chat.completions.create(
                model="gpt-4o-mini", max_tokens=512,
                messages=[{"role": "user", "content": instruction}],
            )
            raw = resp.choices[0].message.content.strip()
        else:
            payload = json.dumps({"model": "llama3", "prompt": instruction, "stream": False})
            import urllib.request
            req = urllib.request.Request(
                "http://localhost:11434/api/generate",
                data=payload.encode(), headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = json.loads(r.read()).get("response", "")
    except Exception as e:
        print(f"  [warn] LLM segment pick failed: {e}; falling back to even splits")
        return _fallback_segments(captions, n, clip_len)

    # Extract JSON array from response
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        print("  [warn] LLM returned no JSON array; using fallback")
        return _fallback_segments(captions, n, clip_len)

    try:
        segments = json.loads(m.group())
        return segments[:n]
    except json.JSONDecodeError:
        return _fallback_segments(captions, n, clip_len)


def _fallback_segments(captions: list, n: int, clip_len: int) -> list:
    """Evenly space N clips across the transcript."""
    if not captions:
        return []
    total = captions[-1]["end"]
    step = total / max(n, 1)
    segs = []
    for i in range(n):
        start = i * step
        end = min(start + clip_len, total)
        segs.append({"start": round(start, 1), "end": round(end, 1), "title": f"Clip {i + 1}"})
    return segs


def _build_clip_ass(captions: list, seg_start: float, seg_end: float) -> str:
    words = [w for w in captions if w["start"] >= seg_start and w["end"] <= seg_end]
    # Shift timestamps to clip-relative
    for w in words:
        w = {**w, "start": w["start"] - seg_start, "end": w["end"] - seg_start}

    lines = []
    current = []
    for word in words:
        adj = {**word, "start": word["start"] - seg_start, "end": word["end"] - seg_start}
        if current and (adj["start"] - current[-1]["end"] > 0.5 or len(current) >= 6):
            lines.append(current)
            current = []
        current.append(adj)
    if current:
        lines.append(current)

    events = []
    for line in lines:
        s = line[0]["start"]
        e = line[-1]["end"]
        parts = []
        for w in line:
            dur_cs = max(1, int((w["end"] - w["start"]) * 100))
            parts.append(f"{{\\k{dur_cs}}}{w['word']} ")
        text = "".join(parts).strip()
        events.append(f"Dialogue: 0,{_ts(s)},{_ts(e)},Default,,0,0,0,,{text}")

    return _ASS_CLIP_HEADER.format(style=_CLIP_STYLE) + "\n".join(events) + "\n"


def run(job):
    n_clips = job.opts.get("clips", 3)
    if not n_clips:
        print("  clips=0, skipping")
        return

    clip_len = job.opts.get("clip_len", 30)
    master_path = Path(job.artifacts.get("master_mp4", job.outdir / "master.mp4"))
    if not master_path.exists():
        raise FileNotFoundError(f"master.mp4 not found: {master_path}")

    captions_path = Path(job.artifacts.get("captions_json", job.outdir / "captions.json"))
    captions = json.loads(captions_path.read_text()) if captions_path.exists() else []

    clips_dir = job.outdir / "clips"
    clips_dir.mkdir(exist_ok=True)

    print(f"  asking LLM to pick {n_clips} clip segments…")
    segments = _pick_segments_llm(captions, n_clips, clip_len, job.prompt)

    clip_paths = []
    for i, seg in enumerate(segments, 1):
        seg_start = seg["start"]
        seg_end = seg["end"]
        title = seg.get("title", f"clip_{i:02d}")
        dur = seg_end - seg_start

        clip_out = clips_dir / f"clip_{i:02d}.mp4"
        temp_cut = clips_dir / f"_cut_{i:02d}.mp4"

        # Cut segment from master
        _run_ffmpeg(
            "-ss", str(seg_start), "-i", str(master_path),
            "-t", str(dur),
            "-c", "copy",
            str(temp_cut),
        )

        # Re-crop to 9:16 (center crop) + re-burn captions
        if captions:
            ass_content = _build_clip_ass(captions, seg_start, seg_end)
            ass_path = clips_dir / f"_clip_{i:02d}.ass"
            ass_path.write_text(ass_content, encoding="utf-8")
            vf = f"crop=ih*9/16:ih,scale=1080:1920,setsar=1,ass={ass_path}"
        else:
            vf = "crop=ih*9/16:ih,scale=1080:1920,setsar=1"

        _run_ffmpeg(
            "-i", str(temp_cut),
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            str(clip_out),
        )
        temp_cut.unlink(missing_ok=True)
        clip_paths.append(str(clip_out))
        print(f"  clip_{i:02d}.mp4 → {seg_start:.1f}s–{seg_end:.1f}s  \"{title}\"")

    job.artifacts["clips"] = clip_paths
    print(f"  {len(clip_paths)} clips saved to {clips_dir}")
