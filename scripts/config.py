"""Cross-platform configuration, .env loader, binary resolution & Playwright helper."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPTS_DIR.parent


def _load_dotenv() -> None:
    """Load key=value pairs from .env in cwd or skill root without overriding shell env."""
    for env_path in (Path.cwd() / ".env", SKILL_ROOT / ".env"):
        if not env_path.is_file():
            continue
        try:
            for raw in env_path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                    val = val[1:-1]
                if key:
                    os.environ.setdefault(key, val)
        except OSError:
            continue


_load_dotenv()


def _resolve_binary(env_name: str, default_name: str) -> str:
    env_val = (os.environ.get(env_name) or "").strip()
    if env_val:
        p = Path(env_val)
        if p.is_file():
            return str(p)
        resolved = shutil.which(env_val)
        if resolved:
            return resolved
        return env_val

    found = shutil.which(default_name)
    if found:
        return found

    exe_name = f"{default_name}.exe" if os.name == "nt" else default_name
    for cand in (
        Path.cwd() / "tools" / "ffmpeg" / exe_name,
        Path.cwd() / "video-studio" / "tools" / "ffmpeg" / exe_name,
        Path.cwd().parent / "video-studio" / "tools" / "ffmpeg" / exe_name,
        SKILL_ROOT / "tools" / "ffmpeg" / exe_name,
    ):
        if cand.is_file():
            return str(cand)

    return default_name


def _resolve_audio_dir() -> Path:
    env_audio = (os.environ.get("GITHUB_RADAR_AUDIO_DIR") or "").strip()
    if env_audio:
        p = Path(env_audio)
        if p.is_dir():
            return p
    for cand in (
        SKILL_ROOT / "assets" / "audio",
        SCRIPTS_DIR / "assets_audio",
    ):
        if cand.is_dir():
            return cand
    return SKILL_ROOT / "assets" / "audio"


def _default_ass_font() -> str:
    env_font = (os.environ.get("GITHUB_RADAR_ASS_FONT") or "").strip()
    if env_font:
        return env_font
    if sys.platform == "win32":
        return "Microsoft YaHei"
    if sys.platform == "darwin":
        return "PingFang SC"
    return "Noto Sans CJK SC"


FFMPEG_EXE: str = _resolve_binary("FFMPEG_EXE", "ffmpeg")
FFPROBE_EXE: str = _resolve_binary("FFPROBE_EXE", "ffprobe")
DEFAULT_OUT_ROOT: Path = Path(os.environ.get("GITHUB_RADAR_OUT_ROOT") or (Path.cwd() / "output"))
HISTORY_DIR: Path = Path(os.environ.get("GITHUB_RADAR_HISTORY_DIR") or (DEFAULT_OUT_ROOT / "history"))
ASSETS_AUDIO_DIR: Path = _resolve_audio_dir()
ASS_FONT_NAME: str = _default_ass_font()


def launch_chromium(pw: Any) -> Any:
    """Launch headless Chrome if configured/available, else Playwright bundled Chromium."""
    channel = (os.environ.get("PLAYWRIGHT_CHANNEL") or "").strip()
    if channel:
        return pw.chromium.launch(channel=channel, headless=True)
    try:
        return pw.chromium.launch(channel="chrome", headless=True)
    except Exception:
        return pw.chromium.launch(headless=True)
