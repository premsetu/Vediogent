"""
parse_script.py — Split a script file into Beat objects.

Directives (stripped from narration text):
  [[visual: keyword phrase]]
  [[clip: path/or/url | 00:12-00:20]]
  [[image: path/or/url]]
  [[hold: 3.5]]
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from job import Beat

_DIR_RE = re.compile(r"\[\[(\w+):\s*([^\]]+?)\]\]")


def _parse_beat(index: int, raw: str) -> Beat:
    text = raw
    beat = Beat(index=index, text="")

    for m in _DIR_RE.finditer(raw):
        key = m.group(1).lower()
        val = m.group(2).strip()

        if key == "visual":
            beat.visual_kind = "stock"
            beat.visual_ref = val
        elif key == "clip":
            beat.visual_kind = "clip"
            if "|" in val:
                ref, in_out = val.split("|", 1)
                beat.visual_ref = ref.strip()
                beat.in_out = in_out.strip()
            else:
                beat.visual_ref = val
        elif key == "image":
            beat.visual_kind = "image"
            beat.visual_ref = val
        elif key == "hold":
            beat.hold = float(val)

    # Strip all directives from the narration text
    text = _DIR_RE.sub("", raw).strip()
    beat.text = text
    return beat


def run(job):
    script = Path(job.script_path)
    raw = script.read_text(encoding="utf-8")

    # Split on blank lines; filter empty paragraphs
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", raw)]
    paragraphs = [p for p in paragraphs if p]

    beats = []
    for i, para in enumerate(paragraphs):
        beat = _parse_beat(i, para)
        if beat.text:   # skip purely-directive paragraphs with no narration
            beats.append(beat)

    if not beats:
        raise ValueError("Script produced no beats — check formatting (blank lines between beats).")

    job.beats = beats
    job.artifacts["beat_count"] = len(beats)
    print(f"  parsed {len(beats)} beats from {script.name}")
