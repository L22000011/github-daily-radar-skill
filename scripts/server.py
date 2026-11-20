"""Local FastAPI service (port 8010) exposing the GitHub Radar V3 video pipeline to n8n."""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Literal

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from config import DEFAULT_OUT_ROOT  # noqa: E402

SCRIPT_PATH = THIS_DIR / "run_github_radar.py"
OUT_ROOT = DEFAULT_OUT_ROOT
JOBS_DIR = OUT_ROOT / ".radar_jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="GitHub Radar Video API", version="3.0.0")
_jobs_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}


class RadarRunRequest(BaseModel):
    period: Literal["daily", "weekly"] = "weekly"
    top_n: int = Field(default=5, ge=1, le=10)
    focus: Literal["ai-dev", "all", "ai-only"] = "ai-dev"
    tts: Literal["edge", "dashscope"] = "edge"
    voice: str = "zh-CN-YunxiNeural"
    rate: str = "+15%"
    enable_bgm: bool = True
    dedupe_days: int = Field(default=0, ge=0, le=30)
    out_dir: str | None = None


class RadarRerenderRequest(BaseModel):
    tts: Literal["edge", "dashscope"] = "edge"
    rate: str = "+15%"
    enable_bgm: bool = True
    voice_dir: str | None = None


def _persist_job(job: dict[str, Any]) -> None:
    snap = {k: v for k, v in job.items() if k != "process"}
    (JOBS_DIR / f"{job['job_id']}.json").write_text(
        json.dumps(snap, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _load_job(job_id: str) -> dict[str, Any] | None:
    with _jobs_lock:
        if job_id in _jobs:
            return dict(_jobs[job_id])
    p = JOBS_DIR / f"{job_id}.json"
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def _launch_worker(job_id: str, cmd: list[str], out_dir: Path) -> None:
    def _runner() -> None:
        with _jobs_lock:
            job = _jobs[job_id]
            job["status"] = "running"
            job["started_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
            _persist_job(job)

        log_file = out_dir / "radar_pipeline.log"
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert proc.stdout is not None
        with log_file.open("a", encoding="utf-8") as lf:
            for line in proc.stdout:
                clean = line.rstrip("\r\n")
                lf.write(clean + "\n")
                lf.flush()
                with _jobs_lock:
                    logs = _jobs[job_id]["logs"]
                    logs.append(clean)
                    if len(logs) > 200:
                        del logs[: len(logs) - 200]
        rc = proc.wait()

        manifest = None
        manifest_path = out_dir / "radar_manifest.json"
        if rc == 0 and manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        with _jobs_lock:
            job = _jobs[job_id]
            job["status"] = "completed" if rc == 0 else "failed"
            job["returncode"] = rc
            job["finished_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
            job["manifest"] = manifest
            _persist_job(job)

    t = threading.Thread(target=_runner, daemon=True)
    t.start()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "github-radar-api", "version": "3.0.0"}


@app.post("/api/radar/run")
def run_radar(req: RadarRunRequest) -> dict[str, Any]:
    job_id = str(uuid.uuid4())
    out_dir = Path(req.out_dir) if req.out_dir else (
        OUT_ROOT / f"github_radar_{req.period}_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}_{job_id[:6]}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "-u",
        str(SCRIPT_PATH),
        "--period", req.period,
        "--top-n", str(req.top_n),
        "--focus", req.focus,
        "--tts", req.tts,
        "--voice", req.voice,
        "--rate", req.rate,
        "--dedupe-days", str(req.dedupe_days),
        "--stage", "all",
        "--out-dir", str(out_dir),
    ]
    if not req.enable_bgm:
        cmd.append("--no-bgm")

    job = {
        "job_id": job_id,
        "status": "queued",
        "out_dir": str(out_dir),
        "request": req.model_dump(),
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "logs": [],
        "manifest": None,
    }
    with _jobs_lock:
        _jobs[job_id] = job
        _persist_job(job)

    _launch_worker(job_id, cmd, out_dir)
    return {"job_id": job_id, "status": "queued", "out_dir": str(out_dir)}


@app.get("/api/radar/status/{job_id}")
def get_status(job_id: str) -> dict[str, Any]:
    job = _load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Radar job not found")
    return job


@app.post("/api/radar/rerender/{job_id}")
def rerender_job(job_id: str, req: RadarRerenderRequest | None = None) -> dict[str, Any]:
    job = _load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Radar job not found")
    out_dir = Path(job["out_dir"])
    if not (out_dir / "storyboard.json").is_file():
        raise HTTPException(status_code=400, detail="storyboard.json not found for this job")

    req = req or RadarRerenderRequest()
    voice_dir = Path(req.voice_dir) if req.voice_dir else (out_dir / "voice_override")

    cmd = [
        sys.executable,
        "-u",
        str(SCRIPT_PATH),
        "--stage", "render",
        "--tts", req.tts,
        "--rate", req.rate,
        "--out-dir", str(out_dir),
        "--voice-dir", str(voice_dir),
    ]
    if not req.enable_bgm:
        cmd.append("--no-bgm")

    with _jobs_lock:
        _jobs[job_id] = {
            **job,
            "status": "queued",
            "logs": [f"[rerender] Triggered V3 render with voice_dir={voice_dir}"],
        }
        _persist_job(_jobs[job_id])

    _launch_worker(job_id, cmd, out_dir)
    return {"job_id": job_id, "status": "queued", "out_dir": str(out_dir), "voice_dir": str(voice_dir)}


if __name__ == "__main__":
    host = os.environ.get("GITHUB_RADAR_HOST", "0.0.0.0")
    port = int(os.environ.get("GITHUB_RADAR_PORT", "8010"))
    uvicorn.run(app, host=host, port=port)
