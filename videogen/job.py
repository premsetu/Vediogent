from dataclasses import dataclass, field, asdict
from typing import Optional
import json, pathlib, uuid


@dataclass
class Beat:
    index: int
    text: str
    visual_kind: str = "auto"        # auto | clip | image | stock
    visual_ref: Optional[str] = None # path/url/keyword
    in_out: Optional[str] = None     # "00:12-00:20" for clips
    hold: Optional[float] = None     # forced on-screen seconds
    start: Optional[float] = None    # filled after narrate
    end: Optional[float] = None
    media_path: Optional[str] = None # filled after resolve_visuals


@dataclass
class Job:
    id: str
    script_path: str
    prompt: str
    opts: dict
    beats: list = field(default_factory=list)
    status: str = "created"          # per-stage: parsed/narrated/captioned/...
    artifacts: dict = field(default_factory=dict)

    @property
    def outdir(self) -> pathlib.Path:
        return pathlib.Path("out") / self.id

    def save(self):
        p = pathlib.Path("jobs")
        p.mkdir(exist_ok=True)
        data = asdict(self)
        (p / f"{self.id}.json").write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, job_id: str) -> "Job":
        p = pathlib.Path("jobs") / f"{job_id}.json"
        data = json.loads(p.read_text())
        beats_raw = data.pop("beats", [])
        job = cls(**data)
        job.beats = [Beat(**b) for b in beats_raw]
        return job

    @classmethod
    def new(cls, script_path, prompt, opts):
        j = cls(id=uuid.uuid4().hex[:8], script_path=script_path, prompt=prompt, opts=opts)
        j.outdir.mkdir(parents=True, exist_ok=True)
        return j
