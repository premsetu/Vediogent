#!/usr/bin/env python3
"""
make_video.py — Standalone cinematic video generator.

Produces a dark-background, text-card style cinematic video from the user's
script using gTTS narration and FFmpeg visuals. No external API keys needed.

Usage: python make_video.py
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

# ── Static FFmpeg ─────────────────────────────────────────────────────────────
import static_ffmpeg
static_ffmpeg.add_paths()

# ── gTTS ──────────────────────────────────────────────────────────────────────
from gtts import gTTS
from pydub import AudioSegment

# ─────────────────────────────────────────────────────────────────────────────
SCRIPT = """
You live a normal life.

You wake up, you work, you scroll, you sleep — and you do it all over again.
And there's nothing wrong with that. But have you ever stopped and asked yourself — why is that?

Why do some people just seem to know more? Live better? Move differently? While the rest of us stay... normal.

Here's the truth. The people who live exceptional lives aren't built differently. They just know things most people never learn. Things school never taught you. Things nobody around you ever sat down and explained.

And that's exactly why this channel exists.

To help with that, we're starting a series — where we cover the ten things you must know to step out of normal, and into the exceptional one percent.

And we're not staying in one lane. We're covering everything. Finance. Technology. Psychology. Health. Mindset. The world. Everything that makes your life better, and your knowledge exceptional.

Because being exceptional isn't about one skill. It's about understanding how the whole world actually works — and using that to your advantage.

So this is an invitation. Follow us. Be with us on this journey. Every video, we go one step deeper. One topic at a time. One thing closer to the kind of life you actually want.

Subscribe, turn on notifications, and let's begin. Because the gap between normal and exceptional? It's just knowledge. And we're about to close it.
"""

W, H = 1920, 1080
FPS = 30
SILENCE_MS = 200   # ms between beats
OUT_DIR = Path("out/cinematic_intro")


# ── Helpers ───────────────────────────────────────────────────────────────────

def run(cmd, check=True, **kw):
    result = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(str(c) for c in cmd)}\n{result.stderr[-2000:]}")
    return result


def parse_beats(script: str) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", script.strip())]
    return [p for p in paras if p]


def synthesize_beat(text: str, out_path: Path):
    """Generate TTS MP3 via gTTS, convert to WAV."""
    mp3_path = out_path.with_suffix(".mp3")
    tts = gTTS(text=text, lang="en", tld="com", slow=False)
    tts.save(str(mp3_path))

    # Convert MP3 → WAV (16-bit, 44100Hz) for consistent processing
    seg = AudioSegment.from_mp3(str(mp3_path))
    seg = seg.set_frame_rate(44100).set_channels(1).set_sample_width(2)
    seg.export(str(out_path), format="wav")
    mp3_path.unlink(missing_ok=True)
    return len(seg) / 1000.0   # duration in seconds


def get_duration(wav_path: Path) -> float:
    result = run(["ffprobe", "-v", "quiet", "-show_entries",
                  "format=duration", "-of", "csv=p=0", str(wav_path)])
    return float(result.stdout.strip())


def wrap_text(text: str, max_chars: int = 38) -> str:
    """Wrap text for on-screen display."""
    return "\n".join(textwrap.wrap(text, width=max_chars))


def escape_ffmpeg_text(s: str) -> str:
    """Escape special characters for FFmpeg drawtext."""
    return s.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:").replace("%", "\\%")


FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
BG_COLOR = (8, 12, 20)          # dark navy black
TEXT_COLOR = (255, 255, 255)     # white
ACCENT_COLOR = (180, 160, 255)   # soft purple accent for first line


def _render_beat_frame(beat_text: str, frame_path: Path):
    """Render a 1920x1080 PNG frame for a beat using Pillow."""
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    import math

    img = Image.new("RGB", (W, H), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Vignette: dark corners
    vignette = Image.new("L", (W, H), 0)
    v_draw = ImageDraw.Draw(vignette)
    for i in range(60):
        alpha = int(180 * (1 - i / 60))
        v_draw.rectangle([i, i, W - i, H - i], outline=alpha)
    vignette = vignette.filter(ImageFilter.GaussianBlur(40))
    vignette_rgb = Image.new("RGB", (W, H), (0, 0, 0))
    img = Image.composite(img, vignette_rgb, vignette)
    draw = ImageDraw.Draw(img)

    # Load fonts
    try:
        font_large = ImageFont.truetype(FONT, 72)
        font_small = ImageFont.truetype(FONT, 58)
    except Exception:
        font_large = ImageFont.load_default()
        font_small = font_large

    # Wrap and layout text
    lines = textwrap.wrap(beat_text, width=38)
    if not lines:
        img.save(str(frame_path))
        return

    # Choose font based on line count
    font = font_small if len(lines) > 3 else font_large
    line_h = 90 if len(lines) <= 3 else 75

    # Measure total block height
    total_h = len(lines) * line_h
    y = max(80, (H - total_h) // 2 - 20)

    # Draw each line
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        text_w = bbox[2] - bbox[0]
        x = (W - text_w) // 2

        # Shadow
        draw.text((x + 4, y + 4), line, font=font, fill=(0, 0, 0, 180))
        draw.text((x + 2, y + 2), line, font=font, fill=(0, 0, 0))

        # Text colour — first line gets accent treatment if short
        color = ACCENT_COLOR if i == 0 and len(lines) == 1 else TEXT_COLOR
        draw.text((x, y), line, font=font, fill=color)

        y += line_h

    img.save(str(frame_path))


def make_beat_video(beat_text: str, duration: float, out_path: Path, index: int):
    """
    Render a single beat as a 1920x1080 cinematic dark video card.
    Uses Pillow for text rendering → FFmpeg for video encoding with fade.
    """
    frame_path = out_path.with_suffix(".png")
    _render_beat_frame(beat_text, frame_path)

    fade_dur = max(0.3, min(0.5, duration * 0.12))
    fade_out_start = max(0.0, duration - fade_dur)

    run([
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", str(frame_path),
        "-t", str(duration),
        "-vf", (
            f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,"
            f"format=yuv420p,"
            f"fade=t=in:st=0:d={fade_dur},"
            f"fade=t=out:st={fade_out_start}:d={fade_dur}"
        ),
        "-r", str(FPS),
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-an",
        str(out_path),
    ])


def concat_wavs(wav_paths: list, silence_ms: int, out_path: Path):
    """Concatenate WAVs with silence between them."""
    combined = AudioSegment.empty()
    silence = AudioSegment.silent(duration=silence_ms)
    for i, p in enumerate(wav_paths):
        seg = AudioSegment.from_wav(str(p))
        combined += seg
        if i < len(wav_paths) - 1:
            combined += silence
    combined.export(str(out_path), format="wav")
    return len(combined) / 1000.0


def concat_videos(video_paths: list, out_path: Path):
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        for p in video_paths:
            f.write(f"file '{Path(p).resolve()}'\n")
        concat_file = f.name

    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file,
         "-c", "copy", str(out_path)])
    Path(concat_file).unlink(missing_ok=True)


def build_ass_captions(beats: list, style: str = "bold-pop") -> str:
    """
    Build ASS subtitle file from beat timing.
    Each beat's text is shown word-by-word using karaoke tags.
    """
    header = f"""\
[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
Timer: 100.0000

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,52,&H00FFFFFF,&H00FFFF00,&H00000000,&H90000000,1,0,0,0,100,100,0,0,1,3,2,2,40,40,80,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    def ts(sec):
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = sec % 60
        cs = int((s % 1) * 100)
        return f"{h}:{m:02d}:{int(s):02d}.{cs:02d}"

    events = []
    for beat in beats:
        words = beat["text"].split()
        if not words:
            continue
        beat_dur = beat["end"] - beat["start"]
        word_dur = beat_dur / len(words)

        # Group into lines of ≤6 words
        for chunk_start in range(0, len(words), 6):
            chunk = words[chunk_start: chunk_start + 6]
            line_start = beat["start"] + chunk_start * word_dur
            line_end = line_start + len(chunk) * word_dur

            # Build karaoke tags
            text = ""
            for w in chunk:
                dur_cs = max(1, int(word_dur * 100))
                text += f"{{\\k{dur_cs}}}{w} "
            text = text.strip()
            events.append(
                f"Dialogue: 0,{ts(line_start)},{ts(line_end)},"
                f"Default,,0,0,0,,{text}"
            )

    return header + "\n".join(events) + "\n"


def add_subtle_music(narration_wav: Path, out_path: Path, total_dur: float):
    """
    If no music file is available, just copy narration as the audio track.
    """
    # Just use the narration directly — can add music file later
    import shutil
    shutil.copy(str(narration_wav), str(out_path))


def mux_master(video_track: Path, audio_track: Path, ass_path: Path, out_path: Path):
    """Mux video + audio + burned captions → master.mp4"""
    run([
        "ffmpeg", "-y",
        "-i", str(video_track),
        "-i", str(audio_track),
        "-map", "0:v", "-map", "1:a",
        "-vf", f"ass={ass_path}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-shortest",
        str(out_path),
    ])


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    beats_dir = OUT_DIR / "beats"
    beats_dir.mkdir(exist_ok=True)

    beat_texts = parse_beats(SCRIPT)
    print(f"Script: {len(beat_texts)} beats")

    # Stage 1 — TTS per beat
    print("\n▶ narrate")
    beats = []
    cumulative = 0.0
    beat_wavs = []

    for i, text in enumerate(beat_texts):
        wav_path = beats_dir / f"beat_{i:03d}.wav"
        if not wav_path.exists():
            print(f"  [{i}] synthesizing: {text[:55]!r}…")
            dur = synthesize_beat(text, wav_path)
        else:
            dur = get_duration(wav_path)
            print(f"  [{i}] cached ({dur:.1f}s)")

        beats.append({"index": i, "text": text, "start": cumulative, "end": cumulative + dur})
        cumulative += dur + (SILENCE_MS / 1000)
        beat_wavs.append(wav_path)

    # Concat narration
    narration_wav = OUT_DIR / "narration.wav"
    print(f"  concatenating → narration.wav")
    total_dur = concat_wavs(beat_wavs, SILENCE_MS, narration_wav)
    print(f"  total duration: {total_dur:.1f}s")

    # Stage 2 — Visual cards per beat
    print("\n▶ visuals")
    beat_videos = []
    for b in beats:
        vid_path = beats_dir / f"beat_{b['index']:03d}.mp4"
        if not vid_path.exists():
            dur = b["end"] - b["start"]
            print(f"  [{b['index']}] rendering card ({dur:.1f}s)…")
            make_beat_video(b["text"], dur, vid_path, b["index"])
        else:
            print(f"  [{b['index']}] cached")
        beat_videos.append(vid_path)

    # Concat all beat videos
    print("  concatenating video cards…")
    silent_track = OUT_DIR / "silent_track.mp4"
    concat_videos(beat_videos, silent_track)

    # Stage 3 — Captions (ASS)
    print("\n▶ captions")
    ass_path = OUT_DIR / "captions.ass"
    ass_content = build_ass_captions(beats)
    ass_path.write_text(ass_content, encoding="utf-8")
    print(f"  captions.ass written ({len(beats)} beats)")

    # Stage 4 — Mux master.mp4
    print("\n▶ assemble")
    master_path = OUT_DIR / "master.mp4"
    mux_master(silent_track, narration_wav, ass_path, master_path)
    size_mb = master_path.stat().st_size / 1e6
    print(f"  master.mp4 → {master_path} ({size_mb:.1f} MB)")

    # Save manifest
    manifest = {
        "beats": beats,
        "total_duration": total_dur,
        "resolution": f"{W}x{H}",
        "fps": FPS,
        "style": "cinematic-dark-text-card",
    }
    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))

    print(f"\n✅ Done! Output: {OUT_DIR}/")
    print(f"   master.mp4  ({size_mb:.1f} MB, {total_dur:.0f}s)")


if __name__ == "__main__":
    main()
