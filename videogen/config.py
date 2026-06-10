import os
from pathlib import Path

_dotenv = Path(__file__).parent.parent / ".env"
if _dotenv.exists():
    for line in _dotenv.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def env(key: str, default=None, required: bool = True) -> str:
    val = os.environ.get(key, default)
    if required and val is None:
        raise EnvironmentError(f"Required env var '{key}' is not set.")
    return val


PEXELS_API_KEY   = env("PEXELS_API_KEY", required=False)
ELEVENLABS_KEY   = env("ELEVENLABS_API_KEY", required=False)
OPENAI_API_KEY   = env("OPENAI_API_KEY", required=False)
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY", required=False)
LLM_BACKEND      = env("LLM_BACKEND", "ollama")   # ollama | claude | openai
WHISPERX_MODEL   = env("WHISPERX_MODEL", "large-v2")
KOKORO_SPEED     = float(env("KOKORO_SPEED", "1.0", required=False))
MUSIC_DIR        = Path(env("MUSIC_DIR", str(Path(__file__).parent / "assets" / "music"), required=False))
