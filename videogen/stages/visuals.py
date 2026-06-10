"""
visuals.py — Resolve a media file for every beat.

Priority:
  [[clip: url|in_out]]  → download with yt-dlp, trim
  [[image: path/url]]   → download/copy, prepare Ken Burns still
  [[visual: keyword]]   → Pexels stock search with explicit keyword
  auto                  → ask LLM for keywords → Pexels search

All resolved paths stored in beat.media_path.
manifest.json records what was used for each beat.
"""
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

_PEXELS_VIDEO_URL = "https://api.pexels.com/videos/search"
_PEXELS_PHOTO_URL = "https://api.pexels.com/v1/search"
_CACHE_DIR = Path("cache") / "pexels"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _aspect_wh(aspect: str):
    mapping = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080)}
    return mapping.get(aspect, (1920, 1080))


def _run_ffmpeg(*args, check=True):
    cmd = ["ffmpeg", "-y", *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"FFmpeg error:\n{result.stderr[-2000:]}")
    return result


def _download_url(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, dest)


def _yt_dlp_download(url: str, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_template = str(dest_dir / "%(id)s.%(ext)s")
    result = subprocess.run(
        ["yt-dlp", "-f", "mp4/bestvideo+bestaudio", "--merge-output-format", "mp4",
         "-o", out_template, url],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed:\n{result.stderr[-1000:]}")
    files = list(dest_dir.glob("*.mp4"))
    if not files:
        raise RuntimeError(f"yt-dlp produced no mp4 in {dest_dir}")
    return max(files, key=lambda f: f.stat().st_mtime)


def _parse_in_out(in_out: str):
    """Parse '00:12-00:20' or '12-20' → (start_sec, end_sec)."""
    if not in_out:
        return None, None

    def to_sec(t: str) -> float:
        parts = t.strip().split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])

    start_s, _, end_s = in_out.partition("-")
    return to_sec(start_s), to_sec(end_s)


def _normalize_video(src: Path, dest: Path, w: int, h: int, duration: float,
                     in_out: str = None):
    """Scale/pad/trim/loop a video to exact WxH and duration."""
    ss_args = []
    t_args = []
    start_sec, end_sec = _parse_in_out(in_out)
    if start_sec is not None:
        ss_args = ["-ss", str(start_sec)]
        if end_sec is not None:
            t_args = ["-t", str(end_sec - start_sec)]

    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,"
        f"setsar=1"
    )

    _run_ffmpeg(
        *ss_args, "-i", str(src),
        *t_args,
        "-vf", vf,
        "-t", str(duration),
        "-r", "30",
        "-c:v", "libx264", "-preset", "fast",
        "-an",
        str(dest),
    )


def _ken_burns(src: Path, dest: Path, w: int, h: int, duration: float):
    """Apply a slow zoom-in Ken Burns effect to a still image."""
    frames = int(duration * 30)
    scale_w = int(w * 1.1)
    scale_h = int(h * 1.1)
    vf = (
        f"scale={scale_w}:{scale_h},"
        f"zoompan=z='min(zoom+0.0015,1.2)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d={frames}:s={w}x{h}:fps=30,"
        f"setsar=1"
    )
    _run_ffmpeg(
        "-loop", "1", "-i", str(src),
        "-vf", vf,
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "fast",
        "-an",
        str(dest),
    )


# ── Pexels ───────────────────────────────────────────────────────────────────

def _pexels_video(keyword: str, w: int, h: int) -> str | None:
    key = config.PEXELS_API_KEY
    if not key:
        return None

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_key = keyword.lower().replace(" ", "_")
    cache_file = _CACHE_DIR / f"{cache_key}_v.json"

    if cache_file.exists():
        data = json.loads(cache_file.read_text())
    else:
        req = urllib.request.Request(
            f"{_PEXELS_VIDEO_URL}?query={urllib.parse.quote(keyword)}&per_page=5&orientation=landscape",
            headers={"Authorization": key},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
            cache_file.write_text(json.dumps(data))
        except Exception as e:
            print(f"  [warn] Pexels video search failed: {e}")
            return None

    videos = data.get("videos", [])
    for video in videos:
        for vf in sorted(video.get("video_files", []), key=lambda x: x.get("width", 0), reverse=True):
            if vf.get("width", 0) >= 1280:
                return vf["link"]
    return None


def _pexels_photo(keyword: str) -> str | None:
    key = config.PEXELS_API_KEY
    if not key:
        return None

    import urllib.parse

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_key = keyword.lower().replace(" ", "_")
    cache_file = _CACHE_DIR / f"{cache_key}_p.json"

    if cache_file.exists():
        data = json.loads(cache_file.read_text())
    else:
        req = urllib.request.Request(
            f"{_PEXELS_PHOTO_URL}?query={urllib.parse.quote(keyword)}&per_page=5",
            headers={"Authorization": key},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
            cache_file.write_text(json.dumps(data))
        except Exception as e:
            print(f"  [warn] Pexels photo search failed: {e}")
            return None

    photos = data.get("photos", [])
    if photos:
        return photos[0]["src"]["large2x"]
    return None


# ── LLM keyword extraction ────────────────────────────────────────────────────

def _llm_keywords(text: str, prompt_ctx: str) -> str:
    backend = config.LLM_BACKEND

    instruction = (
        f"Given this narration beat:\n\n\"{text}\"\n\n"
        f"Global style: {prompt_ctx or 'neutral'}\n\n"
        "Respond with 1-3 stock video search keywords (comma-separated) that best "
        "represent the visual content. Be specific and concrete. No explanation."
    )

    if backend == "claude":
        return _llm_claude(instruction)
    elif backend == "openai":
        return _llm_openai(instruction)
    else:
        return _llm_ollama(instruction)


def _llm_ollama(prompt: str) -> str:
    import urllib.parse
    payload = json.dumps({"model": "llama3", "prompt": prompt, "stream": False})
    req = urllib.request.Request(
        "http://localhost:11434/api/generate",
        data=payload.encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
        return data.get("response", "").strip()
    except Exception as e:
        print(f"  [warn] Ollama failed: {e}; falling back to first 5 words")
        return " ".join(text.split()[:5]) if (text := prompt.split('"')[1] if '"' in prompt else prompt) else "nature"


def _llm_claude(prompt: str) -> str:
    key = config.ANTHROPIC_API_KEY
    if not key:
        raise EnvironmentError("ANTHROPIC_API_KEY not set for LLM_BACKEND=claude")
    try:
        import anthropic
    except ImportError:
        raise ImportError("anthropic not installed. Run: pip install anthropic")

    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=64,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


def _llm_openai(prompt: str) -> str:
    key = config.OPENAI_API_KEY
    if not key:
        raise EnvironmentError("OPENAI_API_KEY not set for LLM_BACKEND=openai")
    try:
        from openai import OpenAI
    except ImportError:
        raise ImportError("openai not installed. Run: pip install openai")

    client = OpenAI(api_key=key)
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=64,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content.strip()


# ── Stage entry point ────────────────────────────────────────────────────────

def run(job):
    import urllib.parse  # needed in module scope for _pexels helpers

    aspect = job.opts.get("aspect", "16:9")
    w, h = _aspect_wh(aspect)
    media_dir = job.outdir / "media"
    media_dir.mkdir(exist_ok=True)
    manifest = {}

    for beat in job.beats:
        duration = beat.hold or max((beat.end or 0) - (beat.start or 0), 1.0)
        out_path = media_dir / f"beat_{beat.index:03d}.mp4"

        if beat.visual_kind == "clip":
            raw_dir = media_dir / f"raw_{beat.index:03d}"
            if beat.visual_ref and beat.visual_ref.startswith("http"):
                raw = _yt_dlp_download(beat.visual_ref, raw_dir)
            else:
                raw = Path(beat.visual_ref)
            _normalize_video(raw, out_path, w, h, duration, beat.in_out)
            source = f"clip:{beat.visual_ref}"

        elif beat.visual_kind == "image":
            img_path = media_dir / f"beat_{beat.index:03d}_img"
            if beat.visual_ref and beat.visual_ref.startswith("http"):
                suffix = Path(beat.visual_ref).suffix or ".jpg"
                img_path = img_path.with_suffix(suffix)
                _download_url(beat.visual_ref, img_path)
            else:
                img_path = Path(beat.visual_ref)
            _ken_burns(img_path, out_path, w, h, duration)
            source = f"image:{beat.visual_ref}"

        else:
            # auto or stock — need keywords
            if beat.visual_kind == "stock" and beat.visual_ref:
                keyword = beat.visual_ref
            else:
                keyword = _llm_keywords(beat.text, job.prompt)
                print(f"  beat {beat.index} → LLM keywords: {keyword!r}")

            # Try video first, fall back to photo
            video_url = _pexels_video(keyword, w, h)
            if video_url:
                raw_path = _CACHE_DIR / f"{keyword.replace(' ', '_')}.mp4"
                if not raw_path.exists():
                    print(f"  downloading Pexels video: {keyword!r}…")
                    _download_url(video_url, raw_path)
                _normalize_video(raw_path, out_path, w, h, duration)
                source = f"pexels_video:{keyword}"
            else:
                photo_url = _pexels_photo(keyword)
                if photo_url:
                    suffix = Path(photo_url).suffix.split("?")[0] or ".jpg"
                    raw_path = _CACHE_DIR / f"{keyword.replace(' ', '_')}{suffix}"
                    if not raw_path.exists():
                        print(f"  downloading Pexels photo: {keyword!r}…")
                        _download_url(photo_url, raw_path)
                    _ken_burns(raw_path, out_path, w, h, duration)
                    source = f"pexels_photo:{keyword}"
                else:
                    # last resort: black frame
                    print(f"  [warn] no visual found for beat {beat.index}; using black frame")
                    _run_ffmpeg(
                        "-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:r=30",
                        "-t", str(duration), "-c:v", "libx264",
                        str(out_path),
                    )
                    source = "black_fallback"

        beat.media_path = str(out_path)
        manifest[str(beat.index)] = {"source": source, "duration": duration, "path": str(out_path)}
        print(f"  beat {beat.index} → {out_path.name} ({duration:.1f}s)")

    manifest_path = job.outdir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    job.artifacts["manifest_json"] = str(manifest_path)
    print(f"  manifest.json saved")
