"""
assemble.py — Build master.mp4 from media + narration + captions + music.

Steps:
  1. Concat beat videos → silent visual track
  2. Mix narration over background music (ducked)
  3. Burn ASS captions (karaoke word-highlight)
  4. Mux → master.mp4 (H.264, yuv420p, +faststart)
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config


def _run_ffmpeg(*args, check=True):
    cmd = ["ffmpeg", "-y", *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"FFmpeg error:\n{result.stderr[-3000:]}")
    return result


def _run_ffprobe(path: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def _concat_videos(beat_paths: list, out_path: Path):
    """Write an ffmpeg concat list and concatenate all beat videos."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        for p in beat_paths:
            f.write(f"file '{Path(p).resolve()}'\n")
        concat_file = f.name

    _run_ffmpeg(
        "-f", "concat", "-safe", "0", "-i", concat_file,
        "-c", "copy",
        str(out_path),
    )
    Path(concat_file).unlink(missing_ok=True)


def _find_music(music_dir: Path, prompt: str) -> Path | None:
    if not music_dir.exists():
        return None
    candidates = list(music_dir.glob("*.mp3")) + list(music_dir.glob("*.wav")) + list(music_dir.glob("*.m4a"))
    if not candidates:
        return None
    # Simple heuristic: pick track whose filename overlaps with prompt keywords
    prompt_words = set(prompt.lower().split())
    best = None
    best_score = -1
    for c in candidates:
        score = sum(1 for w in c.stem.lower().split("_") if w in prompt_words)
        if score > best_score:
            best_score, best = score, c
    return best or candidates[0]


# ── ASS subtitle generation ───────────────────────────────────────────────────

_ASS_HEADER = """\
[Script Info]
ScriptType: v4.00+
Collisions: Normal
PlayResX: {W}
PlayResY: {H}
Timer: 100.0000

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{style_line}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

_STYLE_PRESETS = {
    "bold-pop": (
        "Style: Default,Impact,72,&H00FFFFFF,&H00FFFF00,&H00000000,&H80000000,"
        "1,0,0,0,100,100,0,0,1,4,2,2,20,20,50,1"
    ),
    "clean": (
        "Style: Default,Arial,56,&H00FFFFFF,&H00CCCCCC,&H00000000,&H60000000,"
        "0,0,0,0,100,100,1,0,1,2,1,2,30,30,40,1"
    ),
    "minimal": (
        "Style: Default,Arial,44,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,"
        "0,0,0,0,100,100,0,0,1,1,0,2,40,40,30,1"
    ),
}


def _ts(seconds: float) -> str:
    """Convert seconds to ASS timestamp h:mm:ss.cc"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    cs = int((s % 1) * 100)
    return f"{h}:{m:02d}:{int(s):02d}.{cs:02d}"


def _build_ass(captions: list, style: str, w: int, h: int) -> str:
    """
    Build an ASS subtitle string with karaoke-style word highlighting.
    Each 'line' is a group of words close in time; active word is coloured.
    """
    style_line = _STYLE_PRESETS.get(style, _STYLE_PRESETS["bold-pop"])
    header = _ASS_HEADER.format(W=w, H=h, style_line=style_line)

    # Group words into lines (~8 words or natural pause > 0.5s)
    lines = []
    current = []
    for word in captions:
        if current and (word["start"] - current[-1]["end"] > 0.5 or len(current) >= 8):
            lines.append(current)
            current = []
        current.append(word)
    if current:
        lines.append(current)

    events = []
    for line in lines:
        line_start = line[0]["start"]
        line_end = line[-1]["end"]

        # Build karaoke tag string for the line
        text_parts = []
        for w_item in line:
            dur_cs = max(1, int((w_item["end"] - w_item["start"]) * 100))
            text_parts.append(f"{{\\k{dur_cs}}}{w_item['word']} ")

        text = "".join(text_parts).strip()
        reset_tag = "{\\K0}"
        events.append(
            f"Dialogue: 0,{_ts(line_start)},{_ts(line_end)},Default,,0,0,0,,{reset_tag}{text}"
        )

    return header + "\n".join(events) + "\n"


# ── Stage entry point ────────────────────────────────────────────────────────

def run(job):
    outdir = job.outdir
    w_h_map = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080)}
    aspect = job.opts.get("aspect", "16:9")
    w, h = w_h_map.get(aspect, (1920, 1080))
    style = job.opts.get("captions_style", "bold-pop")
    use_music = job.opts.get("music", "on") == "on"

    # 1. Concat beat videos
    beat_paths = [b.media_path for b in job.beats if b.media_path]
    if not beat_paths:
        raise RuntimeError("No beat media paths — did visuals stage run?")

    silent_track = outdir / "silent_track.mp4"
    print("  concatenating beat videos…")
    _concat_videos(beat_paths, silent_track)

    # 2. Build audio mix
    narration_wav = Path(job.artifacts.get("narration_wav", outdir / "narration.wav"))
    audio_mix = outdir / "audio_mix.wav"

    if use_music:
        music_dir = Path(job.opts.get("music_dir") or config.MUSIC_DIR)
        music_file = _find_music(music_dir, job.prompt)
    else:
        music_file = None

    if music_file:
        print(f"  mixing music: {music_file.name}…")
        # Duck music under narration using sidechaincompress
        video_dur_info = _run_ffprobe(silent_track)
        total_dur = float(video_dur_info["format"]["duration"])

        _run_ffmpeg(
            "-i", str(narration_wav),
            "-stream_loop", "-1", "-i", str(music_file),
            "-filter_complex",
            (
                "[1:a]volume=0.3[music];"
                "[0:a][music]amix=inputs=2:duration=first:dropout_transition=3[aout]"
            ),
            "-map", "[aout]",
            "-t", str(total_dur),
            "-c:a", "pcm_s16le",
            str(audio_mix),
        )
    else:
        audio_mix = narration_wav

    # 3. Build ASS captions
    captions_path = Path(job.artifacts.get("captions_json", outdir / "captions.json"))
    ass_path = outdir / "captions.ass"

    if captions_path.exists():
        captions = json.loads(captions_path.read_text())
        ass_content = _build_ass(captions, style, w, h)
        ass_path.write_text(ass_content, encoding="utf-8")
        vf_caption = f"ass={ass_path}"
    else:
        print("  [warn] no captions.json found; skipping captions")
        vf_caption = None

    # 4. Mux → master.mp4
    master_path = outdir / "master.mp4"
    print("  muxing master.mp4…")

    vf_filter = "setsar=1"
    if vf_caption:
        vf_filter = f"{vf_filter},{vf_caption}"

    _run_ffmpeg(
        "-i", str(silent_track),
        "-i", str(audio_mix),
        "-map", "0:v",
        "-map", "1:a",
        "-vf", vf_filter,
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-shortest",
        str(master_path),
    )

    job.artifacts["master_mp4"] = str(master_path)
    size_mb = master_path.stat().st_size / 1e6
    print(f"  master.mp4 → {master_path} ({size_mb:.1f} MB)")
