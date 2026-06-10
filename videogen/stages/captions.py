"""
captions.py — Run WhisperX on narration.wav → captions.json (word-level).

Output format: list of {word, start, end} dicts.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config


def run(job):
    narration_wav = Path(job.artifacts.get("narration_wav", job.outdir / "narration.wav"))
    if not narration_wav.exists():
        raise FileNotFoundError(f"narration.wav not found: {narration_wav}")

    captions_path = job.outdir / "captions.json"

    try:
        import whisperx
    except ImportError:
        raise ImportError(
            "WhisperX not installed. Run: pip install whisperx"
        )

    import torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    compute_type = "float16" if device == "cuda" else "int8"

    print(f"  loading WhisperX model ({config.WHISPERX_MODEL}) on {device}…")
    model = whisperx.load_model(config.WHISPERX_MODEL, device, compute_type=compute_type)

    audio = whisperx.load_audio(str(narration_wav))
    result = model.transcribe(audio, batch_size=16)

    print("  aligning word-level timestamps…")
    model_a, metadata = whisperx.load_align_model(
        language_code=result["language"], device=device
    )
    result = whisperx.align(
        result["segments"], model_a, metadata, audio, device,
        return_char_alignments=False,
    )

    words = []
    for seg in result["segments"]:
        for w in seg.get("words", []):
            if "word" in w and "start" in w and "end" in w:
                words.append({
                    "word": w["word"].strip(),
                    "start": round(w["start"], 3),
                    "end": round(w["end"], 3),
                })

    captions_path.write_text(json.dumps(words, indent=2))
    job.artifacts["captions_json"] = str(captions_path)
    print(f"  captions.json → {len(words)} words")
