import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from job import Job
from pipeline import run


def main():
    ap = argparse.ArgumentParser(
        prog="videogen",
        description="Turn a script into a narrated, captioned video.",
    )
    ap.add_argument("script", help="Path to script file (.md or .txt)")
    ap.add_argument("--prompt", default="", help="Global style/mood prompt")
    ap.add_argument("--aspect", default="16:9", choices=["16:9", "9:16", "1:1"])
    ap.add_argument("--voice", default="kokoro:af_heart",
                    help="TTS voice: kokoro:<id> | elevenlabs:<id> | openai:<id>")
    ap.add_argument("--music", default="on", choices=["on", "off"])
    ap.add_argument("--music-dir", default=None, help="Directory of background music tracks")
    ap.add_argument("--clips", type=int, default=3, help="Number of short clips to cut (0=none)")
    ap.add_argument("--clip-len", type=int, default=30, help="Target clip length in seconds")
    ap.add_argument("--captions-style", default="bold-pop",
                    choices=["bold-pop", "clean", "minimal"])
    ap.add_argument("--force", action="store_true", help="Re-run all stages even if completed")
    ap.add_argument("--resume", metavar="JOB_ID", help="Resume an existing job by ID")

    a = ap.parse_args()
    opts = vars(a)

    if a.resume:
        from job import Job
        job = Job.load(a.resume)
        print(f"Resuming job {job.id} (status: {job.status})")
    else:
        script = Path(a.script)
        if not script.exists():
            ap.error(f"Script file not found: {a.script}")
        job = Job.new(str(script), a.prompt, opts)
        print(f"Job {job.id} created → {job.outdir}")

    run(job, force=a.force)


if __name__ == "__main__":
    main()
