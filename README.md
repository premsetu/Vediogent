# Vediogent — AI Video Generation Pipeline

Turn a plain-text script into a narrated, captioned, stock-video-matched master video — then auto-cut vertical shorts from it.

## Quick start

```bash
# 1. Install system deps
apt-get install ffmpeg yt-dlp        # or brew install ffmpeg yt-dlp

# 2. Install Python deps
pip install -r requirements.txt

# 3. Configure secrets
cp .env.example .env
# edit .env — set PEXELS_API_KEY at minimum

# 4. Run
cd videogen
python cli.py ../example_script.md \
  --prompt "fast-paced, dramatic, punchy cuts" \
  --voice kokoro:af_heart \
  --clips 3
```

Output lands in `out/<job_id>/`.

## Script format

Beats are separated by **blank lines**. Inline directives (optional):

| Directive | Effect |
|---|---|
| `[[visual: keyword phrase]]` | Force Pexels search term for this beat |
| `[[clip: path/url \| 00:12-00:20]]` | Pin a specific video clip (and optional trim) |
| `[[image: path/url]]` | Pin a still image (Ken Burns applied) |
| `[[hold: 3.5]]` | Override on-screen duration (seconds) |

## Pipeline stages

```
parse_script → narrate → caption → resolve_visuals → assemble → cut_clips
```

Each stage writes to `out/<job_id>/` and checkpoints in `jobs/<job_id>.json`. Re-run resumes from the last completed stage unless `--force` is passed.

## CLI flags

| Flag | Default | Description |
|---|---|---|
| `--prompt` | `""` | Global style/mood (drives keywords, music, captions) |
| `--aspect` | `16:9` | Output aspect: `16:9`, `9:16`, `1:1` |
| `--voice` | `kokoro:af_heart` | TTS: `kokoro:<id>`, `elevenlabs:<id>`, `openai:<id>` |
| `--music` | `on` | Mix background music (`on`/`off`) |
| `--music-dir` | `assets/music/` | Directory of royalty-free tracks |
| `--clips` | `3` | Number of 9:16 short clips to cut (`0` = none) |
| `--clip-len` | `30` | Target clip length in seconds |
| `--captions-style` | `bold-pop` | `bold-pop`, `clean`, or `minimal` |
| `--force` | off | Re-run all stages |
| `--resume JOB_ID` | — | Resume an existing job |

## Environment variables (`.env`)

See `.env.example` for all available variables, including API keys for Pexels, ElevenLabs, OpenAI, and Anthropic, plus `LLM_BACKEND` (`ollama` / `claude` / `openai`) and `WHISPERX_MODEL`.

## Outputs (`out/<job_id>/`)

| File | Description |
|---|---|
| `master.mp4` | Full narrated + captioned video |
| `clips/clip_NN.mp4` | Vertical 9:16 shorts |
| `narration.wav` | Concatenated TTS audio |
| `captions.json` | Word-level timestamps |
| `manifest.json` | Which visual was used per beat |
| `job.json` | Full job state (re-runnable) |

## Phase roadmap

- **Phase 0** — skeleton: CLI/pipeline/job wired; stage stubs ✅
- **Phase 1** — narrate + caption: Kokoro → WhisperX ✅
- **Phase 2** — visuals + assemble: Pexels stock + FFmpeg → `master.mp4` ✅ **MVP**
- **Phase 3** — clips: LLM highlight pick + 9:16 re-crop ✅
- **Phase 4** — polish: Ken Burns, music ducking, ElevenLabs, caption presets ✅
- **Phase 5** — posting: platform APIs (separate spec)
