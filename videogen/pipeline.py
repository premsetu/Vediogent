from stages import parse_script, narrate, captions, visuals, assemble, clips

STAGES = [
    ("parsed",    parse_script.run),
    ("narrated",  narrate.run),
    ("captioned", captions.run),
    ("visuals",   visuals.run),
    ("assembled", assemble.run),
    ("clipped",   clips.run),
]


def run(job, force=False):
    done = [] if force else _completed(job)
    for name, fn in STAGES:
        if name in done:
            print(f"⏭  skip {name}")
            continue
        print(f"▶  {name}")
        try:
            fn(job)
            job.status = name
            job.save()
        except Exception as e:
            job.save()
            raise RuntimeError(f"stage '{name}' failed: {e}") from e
    print(f"✅ done → {job.outdir}")


def _completed(job):
    order = [s[0] for s in STAGES]
    if job.status in order:
        return order[: order.index(job.status) + 1]
    return []
