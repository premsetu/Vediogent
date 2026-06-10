#!/usr/bin/env python3
"""
make_video.py v2 — Cinematic video generator with prompt + YouTube concept replication

Usage:
  python make_video.py --script script.txt
  python make_video.py --script script.txt --prompt "dark cinematic epic"
  python make_video.py --script script.txt --ref-url "https://youtu.be/VIDEO_ID"
  python make_video.py --script script.txt --prompt "energetic" --ref-url URL --voice edge:en-US-GuyNeural

Options:
  --script  TEXT or path to .txt/.md file
  --prompt  Style: "dark cinematic", "epic energetic", "minimal clean", "blue tech" …
  --ref-url YouTube URL — analyse transcript + thumbnail → replicate pacing & palette
  --voice   TTS engine: edge:VoiceName (default) | gtts:lang
  --output  Output directory  (default: out/video_<timestamp>)
  --force   Regenerate all clips even if cached
"""
import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.request
import urllib.parse
import wave
from pathlib import Path
from typing import Optional

# ── static FFmpeg ─────────────────────────────────────────────────────────────
import static_ffmpeg
static_ffmpeg.add_paths()

# ── imaging ───────────────────────────────────────────────────────────────────
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from pydub import AudioSegment
from gtts import gTTS
import numpy as np

# ── yt-dlp (stub broken secretstorage before import) ─────────────────────────
import types as _types
_ss = _types.ModuleType("secretstorage")
sys.modules.setdefault("secretstorage", _ss)
import yt_dlp  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
W, H = 1920, 1080
FPS  = 30
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SILENCE_MS = 180

# ── Visual style presets ──────────────────────────────────────────────────────
STYLES = {
    "dark-epic": {
        "bg":      (6,  8,  18),
        "bloom":   (28, 14, 55),
        "text":    (255, 255, 255),
        "accent":  (160, 110, 255),
        "caption_primary": "&H00FFFFFF",
        "caption_back":    "&H90000000",
        "vignette": 210,
    },
    "dark-cinematic": {
        "bg":      (5, 5, 5),
        "bloom":   (18, 12, 28),
        "text":    (238, 232, 212),
        "accent":  (210, 165, 70),
        "caption_primary": "&H00EEE8D4",
        "caption_back":    "&H90000000",
        "vignette": 220,
    },
    "blue-tech": {
        "bg":      (4, 12, 28),
        "bloom":   (0,  35, 75),
        "text":    (200, 230, 255),
        "accent":  (0,  180, 255),
        "caption_primary": "&H00C8E6FF",
        "caption_back":    "&H90000000",
        "vignette": 175,
    },
    "warm-gold": {
        "bg":      (10, 7, 3),
        "bloom":   (35, 22, 5),
        "text":    (255, 248, 225),
        "accent":  (220, 168, 38),
        "caption_primary": "&H00FFF8E1",
        "caption_back":    "&H90000000",
        "vignette": 200,
    },
    "minimal-light": {
        "bg":      (248, 248, 248),
        "bloom":   (220, 220, 235),
        "text":    (22, 22, 32),
        "accent":  (70, 70, 200),
        "caption_primary": "&H00161620",
        "caption_back":    "&H90FFFFFF",
        "vignette": 55,
    },
}

_PROMPT_MAP = {
    "dark": "dark-epic", "epic": "dark-epic", "dramatic": "dark-epic",
    "cinematic": "dark-cinematic", "film": "dark-cinematic", "movie": "dark-cinematic",
    "tech": "blue-tech", "technology": "blue-tech", "future": "blue-tech", "neon": "blue-tech",
    "gold": "warm-gold", "luxury": "warm-gold", "wealth": "warm-gold", "warm": "warm-gold",
    "minimal": "minimal-light", "clean": "minimal-light", "simple": "minimal-light",
    "white": "minimal-light",
}


def style_from_prompt(prompt: str) -> dict:
    if not prompt:
        return dict(STYLES["dark-epic"], name="dark-epic")
    pl = prompt.lower()
    for kw, preset in _PROMPT_MAP.items():
        if kw in pl:
            return dict(STYLES[preset], name=preset)
    return dict(STYLES["dark-epic"], name="dark-epic")


# ── YouTube analysis ──────────────────────────────────────────────────────────

def analyze_youtube(url: str, work_dir: Path) -> dict:
    """
    Download auto-captions + thumbnail from YouTube.
    Returns style parameters derived from pacing + color palette.
    """
    print(f"  fetching YouTube metadata…")
    ref_dir = work_dir / "ref"
    ref_dir.mkdir(parents=True, exist_ok=True)

    ydl_opts = {
        "writeautomaticsub": True,
        "subtitleslangs": ["en"],
        "writesubtitles": False,
        "writethumbnail": True,
        "skip_download": True,
        "subtitlesformat": "vtt",
        "outtmpl": str(ref_dir / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
    }

    info = {}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True) or {}
    except Exception as e:
        print(f"  [warn] yt-dlp error: {e}")

    result = {
        "title": info.get("title", ""),
        "description": info.get("description", "")[:500],
        "duration": info.get("duration", 0),
        "avg_segment_dur": 5.0,
        "pacing": "medium",
        "style_preset": "dark-epic",
        "bg_override": None,
        "accent_override": None,
    }

    # Parse VTT for pacing
    vtt_files = list(ref_dir.glob("*.vtt"))
    if vtt_files:
        pacing = _parse_vtt_pacing(vtt_files[0])
        result.update(pacing)

    # Analyse thumbnail for palette
    thumb_exts = ["*.jpg", "*.jpeg", "*.webp", "*.png"]
    thumbs = []
    for ext in thumb_exts:
        thumbs.extend(ref_dir.glob(ext))
    if thumbs:
        palette = _thumbnail_palette(thumbs[0])
        result.update(palette)

    print(f"  → pacing={result['pacing']}, style={result['style_preset']}, "
          f"title={result['title'][:40]!r}")
    return result


def _parse_vtt_pacing(vtt_path: Path) -> dict:
    text = vtt_path.read_text(encoding="utf-8", errors="ignore")
    ts_re = re.compile(r"(\d{2}:\d{2}:\d{2}[.,]\d{3}) --> (\d{2}:\d{2}:\d{2}[.,]\d{3})")

    def to_sec(s):
        s = s.replace(",", ".")
        h, m, sec = s.split(":")
        return int(h) * 3600 + int(m) * 60 + float(sec)

    durs = []
    for m in ts_re.finditer(text):
        d = to_sec(m.group(2)) - to_sec(m.group(1))
        if 0.5 < d < 20:
            durs.append(d)

    if not durs:
        return {}

    avg = sum(durs) / len(durs)
    return {
        "avg_segment_dur": round(avg, 1),
        "pacing": "fast" if avg < 3.5 else "slow" if avg > 9 else "medium",
    }


def _thumbnail_palette(thumb_path: Path) -> dict:
    try:
        img = Image.open(str(thumb_path)).convert("RGB").resize((120, 68))
        arr = np.array(img).reshape(-1, 3).astype(float)

        # K-means-like: quantise to 8-cube buckets, find most common
        quant = (arr // 32).astype(int)
        buckets: dict = {}
        for row in quant:
            k = tuple(row)
            buckets[k] = buckets.get(k, 0) + 1

        top = sorted(buckets.items(), key=lambda x: -x[1])[:6]
        # Convert bucket centres back to RGB
        palette = [tuple(int((v * 32) + 16) for v in k) for k, _ in top]

        brightness = float(arr.mean()) / 255.0

        # Heuristic style selection from palette brightness
        if brightness < 0.22:
            preset = "dark-cinematic"
        elif brightness < 0.40:
            preset = "dark-epic"
        elif brightness > 0.68:
            preset = "minimal-light"
        else:
            preset = "blue-tech"

        # Warm-tone override
        if palette:
            r, g, b = palette[0]
            if r > g * 1.35 and r > b * 1.6:
                preset = "warm-gold"

        # Use palette[0] as bg, palette[1] as bloom
        bg_ovr = palette[0] if palette else None
        bloom_ovr = palette[1] if len(palette) > 1 else None

        return {
            "style_preset": preset,
            "bg_override": bg_ovr,
            "accent_override": bloom_ovr,
            "brightness": round(brightness, 2),
        }
    except Exception as e:
        print(f"  [warn] thumbnail analysis failed: {e}")
        return {}


def apply_yt_analysis(style: dict, analysis: dict, prompt: str) -> dict:
    """Merge YouTube-derived colours/preset into the style dict."""
    style = dict(style)

    # Only override preset if prompt doesn't already specify a strong direction
    strong_prompt = any(k in prompt.lower() for k in _PROMPT_MAP)
    if not strong_prompt and analysis.get("style_preset"):
        base = STYLES.get(analysis["style_preset"], STYLES["dark-epic"])
        style.update(base)
        style["name"] = analysis["style_preset"]

    # Apply extracted colours
    if analysis.get("bg_override"):
        style["bg"]    = analysis["bg_override"]
    if analysis.get("accent_override"):
        style["bloom"] = analysis["accent_override"]

    return style


# ── Background generation ─────────────────────────────────────────────────────

def _make_gradient_img(style: dict, seed: int) -> Image.Image:
    """
    Generate a 2304x1296 gradient image (20 % larger than 1920x1080)
    so zoompan has room to drift without hitting edges.
    """
    iw, ih = 2304, 1296
    img = Image.new("RGB", (iw, ih), style["bg"])
    arr = np.zeros((ih, iw, 3), dtype=np.float32)

    bg = np.array(style["bg"],   dtype=np.float32)
    bl = np.array(style["bloom"], dtype=np.float32)

    # Radial gradient: bloom at centre, bg at edges
    cy, cx = ih / 2, iw / 2
    Y, X = np.mgrid[0:ih, 0:iw]
    dist = np.sqrt(((X - cx) / iw) ** 2 + ((Y - cy) / ih) ** 2)
    dist = np.clip(dist * 2.0, 0, 1)[..., np.newaxis]   # 0=centre, 1=edge

    arr = bg * dist + bl * (1 - dist)

    # Subtle noise texture for depth
    rng = np.random.default_rng(seed)
    noise = rng.integers(-6, 7, (ih, iw, 3), dtype=np.int16)
    arr = np.clip(arr + noise, 0, 255).astype(np.uint8)

    return Image.fromarray(arr, "RGB")


# ── Text frame rendering ──────────────────────────────────────────────────────

def _vignette(img: Image.Image, strength: int) -> Image.Image:
    mask = Image.new("L", img.size, 255)
    draw = ImageDraw.Draw(mask)
    steps = 90
    for i in range(steps):
        alpha = int(strength * (1 - i / steps) ** 1.8)
        draw.rectangle([i, i, img.width - i - 1, img.height - i - 1], outline=alpha)
    mask = mask.filter(ImageFilter.GaussianBlur(55))
    black = Image.new("RGB", img.size, (0, 0, 0))
    return Image.composite(black, img, mask)


def render_frame(text: str, style: dict, beat_idx: int, out_path: Path) -> Path:
    """
    Render 1920x1080 frame: gradient bg cropped from 2304x1296 + vignette + text.
    Saves PNG, returns path.
    """
    bg_img = _make_gradient_img(style, seed=beat_idx)
    # Centre-crop to 1920x1080 for the static frame
    left = (2304 - W) // 2
    top  = (1296 - H) // 2
    frame = bg_img.crop((left, top, left + W, top + H))
    frame = _vignette(frame, style.get("vignette", 200))

    draw = ImageDraw.Draw(frame)

    # Font sizes
    try:
        f84 = ImageFont.truetype(FONT_BOLD, 84)
        f72 = ImageFont.truetype(FONT_BOLD, 72)
        f60 = ImageFont.truetype(FONT_BOLD, 60)
        f50 = ImageFont.truetype(FONT_BOLD, 50)
    except Exception:
        f84 = f72 = f60 = f50 = ImageFont.load_default()

    words = text.split()
    is_punchy = len(words) <= 10
    max_chars = 28 if is_punchy else 40
    lines = textwrap.wrap(text, width=max_chars)

    if len(lines) == 1:
        font, lh = f84, 108
    elif len(lines) <= 2:
        font, lh = f72, 95
    elif len(lines) <= 4:
        font, lh = f60, 80
    else:
        font, lh = f50, 68

    total_h = len(lines) * lh
    y = max(90, (H - total_h) // 2 - 20)

    # Accent rule above text (not on first beat)
    if beat_idx > 0 and is_punchy:
        ax, aw = W // 2 - 50, 100
        draw.rectangle([ax, y - 28, ax + aw, y - 23], fill=style["accent"])

    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        tw = bbox[2] - bbox[0]
        x  = (W - tw) // 2

        # Drop shadow (3 layers)
        for ox, oy in [(5, 5), (3, 3), (2, 2)]:
            draw.text((x + ox, y + oy), line, font=font, fill=(0, 0, 0))

        color = style["accent"] if (i == 0 and len(lines) == 1) else style["text"]
        draw.text((x, y), line, font=font, fill=color)
        y += lh

    frame.save(str(out_path))
    return out_path


def render_text_layer(text: str, style: dict, beat_idx: int, out_path: Path) -> Path:
    """
    Render a TRANSPARENT 1920x1080 RGBA layer containing only the text + shadow.
    Overlaying this on the moving gradient keeps the background motion visible
    (the old opaque-frame approach hid the animation behind a static card).
    """
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    try:
        f84 = ImageFont.truetype(FONT_BOLD, 84)
        f72 = ImageFont.truetype(FONT_BOLD, 72)
        f60 = ImageFont.truetype(FONT_BOLD, 60)
        f50 = ImageFont.truetype(FONT_BOLD, 50)
    except Exception:
        f84 = f72 = f60 = f50 = ImageFont.load_default()

    is_punchy = len(text.split()) <= 10
    max_chars = 28 if is_punchy else 40
    lines = textwrap.wrap(text, width=max_chars)
    if not lines:
        layer.save(str(out_path))
        return out_path

    if len(lines) == 1:
        font, lh = f84, 108
    elif len(lines) <= 2:
        font, lh = f72, 95
    elif len(lines) <= 4:
        font, lh = f60, 80
    else:
        font, lh = f50, 68

    total_h = len(lines) * lh
    y = max(90, (H - total_h) // 2 - 20)

    # Accent rule above text on punchy non-opening beats
    if beat_idx > 0 and is_punchy:
        ax, aw = W // 2 - 50, 100
        ac = style["accent"]
        draw.rectangle([ax, y - 28, ax + aw, y - 23],
                       fill=(ac[0], ac[1], ac[2], 255))

    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        tw = bbox[2] - bbox[0]
        x = (W - tw) // 2
        # Layered drop shadow for legibility over moving bg
        for ox, oy, a in [(6, 6, 200), (4, 4, 220), (2, 2, 255)]:
            draw.text((x + ox, y + oy), line, font=font, fill=(0, 0, 0, a))
        c = style["accent"] if (i == 0 and len(lines) == 1) else style["text"]
        draw.text((x, y), line, font=font, fill=(c[0], c[1], c[2], 255))
        y += lh

    layer.save(str(out_path))
    return out_path


# ── Animation engine ─────────────────────────────────────────────────────────

_INTENSITY = {"subtle": 0.5, "medium": 1.0, "strong": 1.9}


def _bg_motion_filter(motion: str, duration: float, beat_idx: int, mult: float) -> str:
    """
    Build a zoompan-based filter chain turning input [0:v] (large gradient)
    into [bg] at WxH with the requested camera motion.
    """
    n = int(duration * FPS) + 2
    cx = "iw/2-(iw/zoom/2)"
    cy = "ih/2-(ih/zoom/2)"

    if motion == "static":
        z, x, y = "1.001", cx, cy

    elif motion == "zoom-in":
        z = f"1.0+{0.13 * mult:.4f}*(on/{n})"
        x, y = cx, cy

    elif motion == "zoom-out":
        z = f"{1.0 + 0.13 * mult:.4f}-{0.13 * mult:.4f}*(on/{n})"
        x, y = cx, cy

    elif motion == "pan":
        z = "1.07"
        amp = W * 0.05 * mult
        x = f"iw/2-(iw/zoom/2)+{amp:.1f}*((on/{n})-0.5)*2"
        y = cy

    elif motion == "pulse":
        amp = 0.045 * mult
        z = f"1.05+{amp:.4f}*sin(6.2832*on/({FPS}*4))"
        x, y = cx, cy

    else:  # "drift" (default)
        pz = 9 + (beat_idx % 3) * 2
        px = 11 + (beat_idx % 4)
        py = 8 + (beat_idx % 3)
        az = (0.025 + 0.01 * (beat_idx % 3)) * mult
        ax = (18 + 5 * (beat_idx % 3)) * mult
        ay = (10 + 4 * (beat_idx % 3)) * mult
        z = f"1.05+{az:.4f}*sin(6.2832*on/({FPS}*{pz}))"
        x = f"iw/2-(iw/zoom/2)+{ax:.1f}*sin(6.2832*on/({FPS}*{px}))"
        y = f"ih/2-(ih/zoom/2)+{ay:.1f}*sin(6.2832*on/({FPS}*{py}))"

    return (f"[0:v]scale=2304:1296,format=yuv420p,"
            f"zoompan=z='{z}':x='{x}':y='{y}':d={n}:s={W}x{H}:fps={FPS}[bg]")


def _text_anim(text_anim: str, duration: float, fade_d: float, mult: float):
    """
    Return (filter_for_txt_label, overlay_xy) for the text layer animation.
    The text layer input is [1:v]; output label [txt].
    """
    fade_out_s = max(0.1, duration - fade_d)
    dist = int(90 * mult)

    base_fade = (f"fade=t=in:st=0:d={fade_d}:alpha=1,"
                 f"fade=t=out:st={fade_out_s}:d={fade_d}:alpha=1")
    txt_filter = f"[1:v]scale={W}:{H},format=yuva420p,{base_fade}[txt]"

    if text_anim == "slide-up":
        xy = f"x=0:y='if(lt(t,{fade_d}),{dist}*(1-t/{fade_d}),0)'"
    elif text_anim == "slide-down":
        xy = f"x=0:y='if(lt(t,{fade_d}),-{dist}*(1-t/{fade_d}),0)'"
    elif text_anim == "slide-left":
        xy = f"y=0:x='if(lt(t,{fade_d}),{dist}*(1-t/{fade_d}),0)'"
    elif text_anim == "slide-right":
        xy = f"y=0:x='if(lt(t,{fade_d}),-{dist}*(1-t/{fade_d}),0)'"
    else:  # "fade" / "none"
        xy = "x=0:y=0"

    return txt_filter, xy


def make_beat_video(text: str, duration: float, style: dict,
                    beat_idx: int, out_path: Path, anim: dict = None):
    """
    Produce a video card for one beat:
      • Large gradient BG → camera motion (bg_motion)
      • Transparent text layer animated on top (text_anim)
    anim = {text_anim, bg_motion, intensity}
    """
    anim = anim or {}
    text_anim = anim.get("text_anim", "fade")
    bg_motion = anim.get("bg_motion", "drift")
    intensity = anim.get("intensity", "medium")
    mult = _INTENSITY.get(intensity, 1.0)

    text_png = out_path.with_name(out_path.stem + "_txt.png")
    bg_png   = out_path.with_name(out_path.stem + "_bg.png")

    # 1. Transparent text layer (animation visible over moving bg)
    render_text_layer(text, style, beat_idx, text_png)

    # 2. Large gradient for camera motion
    _make_gradient_img(style, seed=beat_idx).save(str(bg_png))

    fade_d = max(0.3, min(0.5, duration * 0.14))

    bg_fc = _bg_motion_filter(bg_motion, duration, beat_idx, mult)
    txt_fc, overlay_xy = _text_anim(text_anim, duration, fade_d, mult)

    fc = f"{bg_fc};{txt_fc};[bg][txt]overlay={overlay_xy}[out]"

    _run([
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(bg_png),     # [0]: large bg
        "-loop", "1", "-i", str(text_png),   # [1]: text layer
        "-t", str(duration),
        "-filter_complex", fc,
        "-map", "[out]",
        "-r", str(FPS), "-t", str(duration),
        "-c:v", "libx264", "-preset", "fast", "-crf", "17",
        "-pix_fmt", "yuv420p", "-an",
        str(out_path),
    ])

    text_png.unlink(missing_ok=True)
    bg_png.unlink(missing_ok=True)


# ── Transitions between beats ─────────────────────────────────────────────────

_XFADE_MAP = {
    "fade": "fade", "dissolve": "dissolve",
    "slide": "slideleft", "wipe": "wiperight", "smooth": "smoothleft",
}


def assemble_transitions(beat_videos: list, durations: list,
                         transition: str, out: Path, overlap: float = 0.4):
    """
    Concatenate beat clips. If transition != 'none', cross-blend adjacent
    clips with xfade. Beat clips are expected to be rendered slightly longer
    (by `overlap`) on all but the last so the timeline stays aligned.
    """
    if transition == "none" or len(beat_videos) < 2:
        concat_videos(beat_videos, out)
        return

    xf = _XFADE_MAP.get(transition, "fade")

    inputs = []
    for p in beat_videos:
        inputs += ["-i", str(p)]

    # Build xfade chain with cumulative offsets
    chain = []
    prev = "0:v"
    cumulative = 0.0
    for i in range(1, len(beat_videos)):
        cumulative += durations[i - 1] - overlap
        out_lbl = f"vx{i}"
        chain.append(
            f"[{prev}][{i}:v]xfade=transition={xf}:"
            f"duration={overlap}:offset={cumulative:.3f}[{out_lbl}]"
        )
        prev = out_lbl

    fc = ";".join(chain)

    _run([
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", fc,
        "-map", f"[{prev}]",
        "-r", str(FPS),
        "-c:v", "libx264", "-preset", "fast", "-crf", "17",
        "-pix_fmt", "yuv420p", "-an",
        str(out),
    ])


# ── TTS ───────────────────────────────────────────────────────────────────────

async def _edge_tts(text: str, voice: str, mp3_path: Path):
    import edge_tts
    comm = edge_tts.Communicate(text, voice)
    await comm.save(str(mp3_path))


def synthesize(text: str, voice_spec: str, out_wav: Path) -> float:
    backend, _, vid = voice_spec.partition(":")

    if backend == "edge":
        voice = vid or "en-US-GuyNeural"
        mp3 = out_wav.with_suffix(".mp3")
        try:
            asyncio.run(_edge_tts(text, voice, mp3))
            seg = AudioSegment.from_mp3(str(mp3)).set_frame_rate(44100).set_channels(1)
            seg.export(str(out_wav), format="wav")
            mp3.unlink(missing_ok=True)
            return len(seg) / 1000.0
        except Exception as e:
            print(f"  [warn] edge-tts failed ({e}), falling back to gTTS")

    # gTTS fallback — always use lang code, not a voice ID
    lang = vid if backend == "gtts" else "en"
    mp3 = out_wav.with_suffix(".mp3")
    gTTS(text=text, lang=lang, slow=False).save(str(mp3))
    seg = AudioSegment.from_mp3(str(mp3)).set_frame_rate(44100).set_channels(1)
    seg.export(str(out_wav), format="wav")
    mp3.unlink(missing_ok=True)
    return len(seg) / 1000.0


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def concat_wavs(paths: list, silence_ms: int, out: Path) -> float:
    combined = AudioSegment.empty()
    sil = AudioSegment.silent(duration=silence_ms)
    for i, p in enumerate(paths):
        combined += AudioSegment.from_wav(str(p))
        if i < len(paths) - 1:
            combined += sil
    combined.export(str(out), format="wav")
    return len(combined) / 1000.0


def concat_videos(paths: list, out: Path):
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        for p in paths:
            f.write(f"file '{Path(p).resolve()}'\n")
        cfile = f.name
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", cfile,
          "-c", "copy", str(out)])
    Path(cfile).unlink(missing_ok=True)


# ── ASS Captions ──────────────────────────────────────────────────────────────

def build_ass(beats: list, style: dict) -> str:
    pri  = style.get("caption_primary", "&H00FFFFFF")
    back = style.get("caption_back",    "&H90000000")

    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {W}\nPlayResY: {H}\nTimer: 100.0000\n\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,"
        "OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,"
        "ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
        "Alignment,MarginL,MarginR,MarginV,Encoding\n"
        f"Style: Default,Arial,50,{pri},&H00FFFF00,&H00000000,{back},"
        f"1,0,0,0,100,100,0,0,1,3,2,2,40,40,85,1\n\n"
        "[Events]\n"
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )

    def ts(sec):
        h = int(sec // 3600); m = int((sec % 3600) // 60); s = sec % 60
        return f"{h}:{m:02d}:{int(s):02d}.{int((s % 1) * 100):02d}"

    events = []
    for b in beats:
        words = b["text"].split()
        if not words:
            continue
        wdur = (b["end"] - b["start"]) / len(words)
        for chunk_start in range(0, len(words), 7):
            chunk = words[chunk_start: chunk_start + 7]
            ls = b["start"] + chunk_start * wdur
            le = ls + len(chunk) * wdur
            kara = " ".join(f"{{\\k{max(1, int(wdur * 100))}}}{w}" for w in chunk)
            events.append(f"Dialogue: 0,{ts(ls)},{ts(le)},Default,,0,0,0,,{kara}")

    return header + "\n".join(events) + "\n"


# ── Utilities ─────────────────────────────────────────────────────────────────

def _run(cmd, check=True):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"FFmpeg error:\n{r.stderr[-2500:]}")
    return r


def parse_beats(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Cinematic video generator — prompt + YouTube concept replication",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--script",   help="Script text or path to .txt/.md file")
    ap.add_argument("--prompt",   default="dark epic cinematic",
                    help="Style prompt: dark / cinematic / epic / tech / gold / minimal …")
    ap.add_argument("--ref-url",  dest="ref_url",
                    help="YouTube URL to analyse and replicate (pacing + colour palette)")
    ap.add_argument("--voice",    default="edge:en-US-GuyNeural",
                    help="TTS: edge:VoiceName  or  gtts:lang  (default: edge:en-US-GuyNeural)")
    ap.add_argument("--output",   default=f"out/video_{int(time.time())}",
                    help="Output directory")
    ap.add_argument("--text-anim", dest="text_anim", default="fade",
                    choices=["fade", "slide-up", "slide-down", "slide-left", "slide-right"],
                    help="Text entrance animation")
    ap.add_argument("--bg-motion", dest="bg_motion", default="drift",
                    choices=["drift", "static", "zoom-in", "zoom-out", "pan", "pulse"],
                    help="Background camera motion")
    ap.add_argument("--intensity", default="medium",
                    choices=["subtle", "medium", "strong"],
                    help="Motion intensity")
    ap.add_argument("--transition", default="none",
                    choices=["none", "fade", "dissolve", "slide", "wipe", "smooth"],
                    help="Transition between beats")
    ap.add_argument("--force",    action="store_true",
                    help="Regenerate all clips even if cached")
    args = ap.parse_args()

    # ── Read script ───────────────────────────────────────────────────────────
    if not args.script:
        if sys.stdin.isatty():
            ap.error("Provide --script or pipe script text via stdin.")
        script_text = sys.stdin.read()
    elif Path(args.script).exists():
        script_text = Path(args.script).read_text(encoding="utf-8")
    else:
        script_text = args.script   # treat as inline text

    out_dir    = Path(args.output)
    beats_dir  = out_dir / "beats"
    out_dir.mkdir(parents=True, exist_ok=True)
    beats_dir.mkdir(exist_ok=True)

    # ── Style resolution ──────────────────────────────────────────────────────
    style = style_from_prompt(args.prompt)
    print(f"Prompt style: {style['name']}")

    # ── YouTube analysis ──────────────────────────────────────────────────────
    yt_analysis = {}
    if args.ref_url:
        print("\n▶ youtube-analysis")
        try:
            yt_analysis = analyze_youtube(args.ref_url, out_dir)
            style = apply_yt_analysis(style, yt_analysis, args.prompt)
            print(f"  Final style after YT merge: {style.get('name')}")
        except Exception as e:
            print(f"  [warn] YouTube analysis failed: {e}")

    beat_texts = parse_beats(script_text)
    print(f"\nScript: {len(beat_texts)} beats  |  style: {style.get('name')}")

    # ── Narrate ───────────────────────────────────────────────────────────────
    print("\n▶ narrate")
    beats = []
    cumulative = 0.0
    beat_wavs  = []

    for i, text in enumerate(beat_texts):
        wav = beats_dir / f"beat_{i:03d}.wav"
        if args.force:
            wav.unlink(missing_ok=True)
        if not wav.exists():
            print(f"  [{i}] synthesising: {text[:55]!r}…")
            dur = synthesize(text, args.voice, wav)
        else:
            dur = wav_duration(wav)
            print(f"  [{i}] cached  ({dur:.1f}s)")

        beats.append({"index": i, "text": text,
                      "start": cumulative, "end": cumulative + dur})
        cumulative += dur + SILENCE_MS / 1000.0
        beat_wavs.append(wav)

    narration_wav = out_dir / "narration.wav"
    total_dur     = concat_wavs(beat_wavs, SILENCE_MS, narration_wav)
    print(f"  narration: {total_dur:.1f}s")

    # ── Visuals ───────────────────────────────────────────────────────────────
    anim = {"text_anim": args.text_anim, "bg_motion": args.bg_motion,
            "intensity": args.intensity}
    transition = args.transition
    overlap = 0.4
    print(f"\n▶ visuals  (text={args.text_anim}, bg={args.bg_motion}, "
          f"intensity={args.intensity}, transition={transition})")
    beat_videos = []
    render_durs = []

    for b in beats:
        vid = beats_dir / f"beat_{b['index']:03d}.mp4"
        if args.force:
            vid.unlink(missing_ok=True)
        dur = b["end"] - b["start"]
        # When transitions are on, extend non-last beats by the overlap so the
        # xfade cross-blend doesn't shorten the timeline (keeps audio in sync).
        if transition != "none" and b["index"] < len(beats) - 1:
            dur += overlap
        render_durs.append(dur)
        if not vid.exists():
            print(f"  [{b['index']}] rendering ({dur:.1f}s)  {b['text'][:45]!r}…")
            make_beat_video(b["text"], dur, style, b["index"], vid, anim)
        else:
            print(f"  [{b['index']}] cached")
        beat_videos.append(vid)

    silent_track = out_dir / "silent_track.mp4"
    print(f"  assembling beat clips  (transition={transition})…")
    assemble_transitions(beat_videos, render_durs, transition, silent_track, overlap)

    # ── Captions ──────────────────────────────────────────────────────────────
    print("\n▶ captions")
    ass_path = out_dir / "captions.ass"
    ass_path.write_text(build_ass(beats, style), encoding="utf-8")
    print(f"  {ass_path} written")

    # ── Assemble master ───────────────────────────────────────────────────────
    print("\n▶ assemble")
    master = out_dir / "master.mp4"
    _run([
        "ffmpeg", "-y",
        "-i", str(silent_track),
        "-i", str(narration_wav),
        "-map", "0:v", "-map", "1:a",
        "-vf", f"ass={ass_path}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "17",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-shortest",
        str(master),
    ])

    size_mb = master.stat().st_size / 1e6
    print(f"  ✅ master.mp4 → {master}  ({size_mb:.1f} MB, {total_dur:.0f}s)")

    # ── Manifest ──────────────────────────────────────────────────────────────
    manifest = {
        "style": style.get("name"),
        "prompt": args.prompt,
        "ref_url": args.ref_url,
        "youtube_analysis": yt_analysis,
        "voice": args.voice,
        "total_duration": round(total_dur, 2),
        "resolution": f"{W}x{H}",
        "fps": FPS,
        "beats": beats,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"\n✅  Done → {out_dir}/")


if __name__ == "__main__":
    main()
