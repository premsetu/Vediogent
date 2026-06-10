"""
narrate.py — Synthesize per-beat narration, concatenate → narration.wav.

Backends (selected via job.opts["voice"]):
  kokoro:<voice_id>       — local Kokoro TTS (default: af_heart)
  elevenlabs:<voice_id>   — ElevenLabs API
  openai:<voice_id>       — OpenAI TTS API

Each beat is synthesized individually so we get exact per-beat WAV durations,
then clips are concatenated with a 150ms silence pad between beats.
"""
import sys
import struct
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


_SILENCE_MS = 150  # ms padding between beats


def _silence_wav(duration_ms: int, sample_rate: int = 24000, channels: int = 1) -> bytes:
    """Return raw PCM bytes for silence of the given duration."""
    n_samples = int(sample_rate * duration_ms / 1000) * channels
    return struct.pack(f"<{n_samples}h", *([0] * n_samples))


def _wav_params(path: Path):
    with wave.open(str(path), "rb") as w:
        return w.getparams(), w.readframes(w.getnframes())


def _concat_wavs(paths: list, silence_ms: int, out_path: Path):
    """Concatenate WAV files with silence padding; all must share same params."""
    frames_list = []
    params = None
    silence_raw = None

    for i, p in enumerate(paths):
        par, raw = _wav_params(p)
        if params is None:
            params = par
            silence_raw = _silence_wav(silence_ms, par.framerate, par.nchannels)
        frames_list.append(raw)
        if i < len(paths) - 1:
            frames_list.append(silence_raw)

    with wave.open(str(out_path), "wb") as w:
        w.setparams(params)
        for chunk in frames_list:
            w.writeframes(chunk)


def _duration_wav(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


# ── Backend: Kokoro ──────────────────────────────────────────────────────────

def _synth_kokoro(text: str, voice_id: str, out_path: Path):
    try:
        from kokoro import KPipeline
    except ImportError:
        raise ImportError(
            "Kokoro not installed. Run: pip install kokoro soundfile"
        )
    import soundfile as sf
    import numpy as np

    lang = voice_id[0] if voice_id else "a"  # first char = lang code
    pipeline = KPipeline(lang_code=lang)
    generator = pipeline(text, voice=voice_id, speed=1.0, split_pattern=None)

    chunks = []
    sr = None
    for _, _, audio in generator:
        if sr is None:
            sr = 24000
        chunks.append(audio)

    if not chunks:
        raise RuntimeError(f"Kokoro produced no audio for text: {text[:60]!r}")

    audio = np.concatenate(chunks)
    sf.write(str(out_path), audio, sr, subtype="PCM_16")


# ── Backend: ElevenLabs ──────────────────────────────────────────────────────

def _synth_elevenlabs(text: str, voice_id: str, out_path: Path):
    import config
    try:
        from elevenlabs import ElevenLabs
    except ImportError:
        raise ImportError("ElevenLabs not installed. Run: pip install elevenlabs")

    key = config.ELEVENLABS_KEY
    if not key:
        raise EnvironmentError("ELEVENLABS_API_KEY not set.")

    client = ElevenLabs(api_key=key)
    audio = client.generate(text=text, voice=voice_id, model="eleven_multilingual_v2")
    out_path.write_bytes(b"".join(audio))


# ── Backend: OpenAI TTS ──────────────────────────────────────────────────────

def _synth_openai(text: str, voice_id: str, out_path: Path):
    import config
    try:
        from openai import OpenAI
    except ImportError:
        raise ImportError("OpenAI not installed. Run: pip install openai")

    key = config.OPENAI_API_KEY
    if not key:
        raise EnvironmentError("OPENAI_API_KEY not set.")

    client = OpenAI(api_key=key)
    resp = client.audio.speech.create(model="tts-1-hd", voice=voice_id, input=text)
    out_path.write_bytes(resp.content)


# ── Dispatcher ───────────────────────────────────────────────────────────────

def synthesize(text: str, voice_spec: str, out_path: Path):
    """
    Synthesize `text` using the voice spec (backend:voice_id) → WAV at out_path.
    Always writes a standard WAV file (PCM 16-bit).
    """
    backend, _, voice_id = voice_spec.partition(":")
    backend = backend.lower()

    if backend == "kokoro":
        _synth_kokoro(text, voice_id or "af_heart", out_path)
    elif backend == "elevenlabs":
        _synth_elevenlabs(text, voice_id, out_path)
    elif backend == "openai":
        _synth_openai(text, voice_id or "onyx", out_path)
    else:
        raise ValueError(f"Unknown TTS backend: {backend!r}. Use kokoro/elevenlabs/openai.")


# ── Stage entry point ────────────────────────────────────────────────────────

def run(job):
    voice_spec = job.opts.get("voice", "kokoro:af_heart")
    beat_wavs = []
    beats_dir = job.outdir / "beats"
    beats_dir.mkdir(exist_ok=True)

    cumulative = 0.0

    for beat in job.beats:
        beat_wav = beats_dir / f"beat_{beat.index:03d}.wav"
        if not beat_wav.exists():
            print(f"  synthesizing beat {beat.index}: {beat.text[:50]!r}…")
            synthesize(beat.text, voice_spec, beat_wav)
        dur = _duration_wav(beat_wav)
        beat.start = cumulative
        beat.end = cumulative + dur
        cumulative += dur + (_SILENCE_MS / 1000.0)
        beat_wavs.append(beat_wav)

    narration_path = job.outdir / "narration.wav"
    _concat_wavs(beat_wavs, _SILENCE_MS, narration_path)

    job.artifacts["narration_wav"] = str(narration_path)
    job.artifacts["narration_duration"] = _duration_wav(narration_path)
    print(f"  narration.wav → {narration_path} ({job.artifacts['narration_duration']:.1f}s)")
