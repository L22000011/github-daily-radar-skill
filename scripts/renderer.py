"""Playwright multi-beat RGBA renderer, ambient BGM synthesizer, FFmpeg 9:16 compositor & ASS highlighter."""
from __future__ import annotations

import base64
import datetime as dt
import html
import json
import math
import re
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright

from config import (
    ASS_FONT_NAME,
    ASSETS_AUDIO_DIR,
    FFMPEG_EXE,
    FFPROBE_EXE,
    launch_chromium,
)

BASE_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: "Microsoft YaHei", "SimHei", "PingFang SC", "Segoe UI", system-ui, sans-serif;
  color: #141416;
  overflow: hidden;
}
.mono { font-family: "Cascadia Code", "Consolas", "Segoe UI", monospace; }
.editorial-heavy { font-family: "SimHei", "Microsoft YaHei", sans-serif; }
"""

HIGHLIGHT_TERMS = (
    "GitHub", "TypeScript", "JavaScript", "Python", "Claude Code", "Claude",
    "Anthropic", "OpenAI", "Agent", "MCP", "LLM", "SDK", "SKILL.md",
    "Apache 2.0", "Apache-2.0", "MIT", "React", "Go", "CLI", "PR", "MDN",
    "Web", "AI", "API", "StyleX", "Rust", "Docker", "RAG", "SQL", "OSS",
    "UI", "Git", "Next.js", "Node.js",
)
_TERM_PATTERN = (
    r"(?<![A-Za-z0-9_])(?:"
    + "|".join(re.escape(t) for t in sorted(HIGHLIGHT_TERMS, key=len, reverse=True))
    + r")(?![A-Za-z0-9_])"
)
_NUM_HOOK_PATTERN = (
    r"(?:(?:单日|本周|今日|本日|一周|累计|总星标?|最高)?(?:狂飙|暴涨|飙升|新增|拿下|斩获|超过|突破|高达)?"
    r"(?:[零一二两三四五六七八九十百千万亿]+|\d+(?:\.\d+)?)(?:多|余)?[万亿]?多?(?:颗|个|条|倍)?(?:星|★))"
    r"|(?:第[一二三四五六七八九十\d]+[名款个]?)"
    r"|(?:Top\s*\d+)"
    r"|(?:[一二两三四五六七八九十\d]+分之一)"
    r"|(?:[一二两三四五六七八九十]+(?:大|个|款))"
)
_ASS_HL_RE = re.compile(rf"(?P<term>{_TERM_PATTERN})|(?P<num>{_NUM_HOOK_PATTERN})")


def _github_svg(size: int = 34, fill: str = "#141416") -> str:
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="{fill}" xmlns="http://www.w3.org/2000/svg">'
        '<path fill-rule="evenodd" clip-rule="evenodd" d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.53 1.032 1.53 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z"/>'
        "</svg>"
    )


def _dot_wave_script(phase: float = 0.0, width: int = 1080, height: int = 1920) -> str:
    return f"""
<script>
(() => {{
  const c = document.getElementById('dot-wave');
  if (!c) return;
  const ctx = c.getContext('2d');
  const W = {width}, H = {height}, PH = {phase:.3f};
  ctx.fillStyle = '#040406';
  ctx.fillRect(0, 0, W, H);

  function drawRibbonBand(yStart, rowStep, rows, cols, xStep, ampY, persp, fadeBottom) {{
    for (let r = 0; r < rows; r++) {{
      const nr = r / Math.max(1, rows - 1);
      const rowFade = fadeBottom ? Math.pow(1 - nr * 0.55, 1.1) : (0.35 + 0.65 * Math.sin(nr * Math.PI));
      for (let col = -3; col <= cols; col++) {{
        const nc = col / cols;
        const px = col * xStep + (nc - 0.5) * r * persp + Math.sin(r * 0.38 + PH) * 10;
        const w1 = Math.sin(col * 0.16 + r * 0.32 + PH);
        const w2 = Math.cos(col * 0.08 - r * 0.18 + PH * 0.7);
        const py = yStart + r * rowStep + w1 * ampY + w2 * (ampY * 0.55);
        if (px < -8 || px > W + 8 || py < -8 || py > H + 8) continue;
        const crest = 0.5 + 0.5 * Math.sin(col * 0.16 + r * 0.32 + PH + 0.5);
        const edgeFade = Math.sin(Math.min(1, Math.max(0, (px + 40) / (W + 80))) * Math.PI);
        const alpha = Math.min(0.72, Math.max(0.05, (0.14 + 0.56 * Math.pow(crest, 1.3)) * rowFade * (0.35 + 0.65 * edgeFade)));
        const rad = 1.15 + 2.35 * Math.pow(crest, 1.45) * (0.7 + 0.3 * edgeFade);
        ctx.beginPath();
        ctx.arc(px, py, rad, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(242, 245, 248, ${{alpha.toFixed(3)}})`;
        ctx.fill();
      }}
    }}
  }}

  const topRows = H >= 1800 ? 15 : 9;
  drawRibbonBand(20, 15, topRows, 58, 19.5, 22, 4.2, true);

  const botStart = H >= 1800 ? (H - 425) : (H - 185);
  const botRows = H >= 1800 ? 20 : 10;
  drawRibbonBand(botStart, 20, botRows, 60, 19.0, 28, -3.5, false);
}})();
</script>"""


def generate_animated_dot_wave_bg(out_mp4: Path, loop_sec: float = 6.0, fps: int = 30) -> Path:
    """Synthesize a seamless 6s 1080x1920 30fps 3D dot-wave + subtle aurora background loop."""
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    w, h = 1080, 1920
    n_frames = max(30, int(round(loop_sec * fps)))

    # Precompute circular anti-aliased sprite kernels for radii 1.2..3.8 in 0.2 steps
    kernels: list[tuple[int, np.ndarray]] = []
    for k_idx in range(14):
        rad = 1.2 + k_idx * 0.2
        half = int(math.ceil(rad + 1.5))
        yy, xx = np.mgrid[-half : half + 1, -half : half + 1]
        dist = np.sqrt(xx * xx + yy * yy)
        mask = np.clip((rad + 0.65 - dist) / 1.15, 0.0, 1.0).astype(np.float32)
        kernels.append((half, mask))

    # Top and bottom bands only (white rounded card completely covers y=220..1540)
    top_h = 256
    bot_y0 = 1504
    bot_h = h - bot_y0
    # Half-resolution aurora coordinate grids (upsampled cleanly with repeat/linear or compute directly)
    ty, tx = np.mgrid[0:top_h:2, 0:w:2].astype(np.float32)
    by, bx = np.mgrid[bot_y0:h:2, 0:w:2].astype(np.float32)

    # Precompute ribbon coordinates (rows x cols flattened)
    def _band_grid(y_start: float, row_step: float, rows: int, cols: int, x_step: float, amp_y: float, persp: float, fade_bottom: bool):
        rr, cc = np.mgrid[0:rows, -3 : cols + 1]
        r_f = rr.ravel().astype(np.float32)
        c_f = cc.ravel().astype(np.float32)
        nr = r_f / max(1.0, float(rows - 1))
        nc = c_f / float(cols)
        if fade_bottom:
            row_fade = np.power(1.0 - nr * 0.55, 1.1)
        else:
            row_fade = 0.35 + 0.65 * np.sin(nr * math.pi)
        return r_f, c_f, nc, row_fade, y_start, row_step, x_step, amp_y, persp

    top_grid = _band_grid(20.0, 15.0, 15, 58, 19.5, 22.0, 4.2, True)
    bot_grid = _band_grid(h - 425.0, 20.0, 20, 60, 19.0, 28.0, -3.5, False)

    cmd = [
        str(FFMPEG_EXE),
        "-y",
        "-f", "rawvideo",
        "-pix_fmt", "rgb24",
        "-s", f"{w}x{h}",
        "-r", str(fps),
        "-i", "-",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        str(out_mp4),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert proc.stdin is not None

    frame_buf = np.empty((h, w, 3), dtype=np.uint8)
    top_f = np.zeros((top_h, w), dtype=np.float32)
    bot_f = np.zeros((bot_h, w), dtype=np.float32)

    def _stamp_band(buf2d: np.ndarray, y_offset: int, band_h: int, grid_tuple, ph: float) -> None:
        buf2d.fill(0.0)
        r_f, c_f, nc, row_fade, y_start, row_step, x_step, amp_y, persp = grid_tuple
        px = c_f * x_step + (nc - 0.5) * r_f * persp + np.sin(r_f * 0.38 + ph) * 10.0
        w1 = np.sin(c_f * 0.16 + r_f * 0.32 + ph)
        w2 = np.cos(c_f * 0.08 - r_f * 0.18 - ph)  # exact 2*pi periodicity
        py = y_start + r_f * row_step + w1 * amp_y + w2 * (amp_y * 0.55) - y_offset

        valid = (px >= -4.0) & (px < w + 4.0) & (py >= -4.0) & (py < band_h + 4.0)
        if not np.any(valid):
            return
        px = px[valid]
        py = py[valid]
        rf_v = row_fade[valid]
        crest = 0.5 + 0.5 * np.sin(c_f[valid] * 0.16 + r_f[valid] * 0.32 + ph + 0.5)
        edge_fade = np.sin(np.clip((px + 40.0) / (w + 80.0), 0.0, 1.0) * math.pi)
        alphas = np.clip((0.16 + 0.62 * np.power(crest, 1.25)) * rf_v * (0.35 + 0.65 * edge_fade), 0.05, 0.78)
        k_indices = np.clip(
            np.rint((1.15 + 2.45 * np.power(crest, 1.4) * (0.7 + 0.3 * edge_fade) - 1.2) / 0.2).astype(np.int32),
            0,
            len(kernels) - 1,
        )
        ixs = np.rint(px).astype(np.int32).tolist()
        iys = np.rint(py).astype(np.int32).tolist()
        ks = k_indices.tolist()
        als = alphas.tolist()
        for ix, iy, ki, al in zip(ixs, iys, ks, als):
            half, mask = kernels[ki]
            y0, y1 = max(0, iy - half), min(band_h, iy + half + 1)
            x0, x1 = max(0, ix - half), min(w, ix + half + 1)
            if y0 < y1 and x0 < x1:
                ky0 = y0 - (iy - half)
                ky1 = ky0 + (y1 - y0)
                kx0 = x0 - (ix - half)
                kx1 = kx0 + (x1 - x0)
                np.maximum(buf2d[y0:y1, x0:x1], mask[ky0:ky1, kx0:kx1] * al, out=buf2d[y0:y1, x0:x1])

    for fi in range(n_frames):
        ph = 2.0 * math.pi * (fi / n_frames)
        frame_buf[:] = (4, 4, 6)

        ax1 = w * (0.34 + 0.22 * math.sin(ph))
        ay1 = 105.0 + 36.0 * math.cos(ph)
        g_top_half = np.exp(-(((tx - ax1) / 420.0) ** 2 + ((ty - ay1) / 145.0) ** 2) * 1.35) * 20.0
        g_top = np.repeat(np.repeat(g_top_half, 2, axis=0), 2, axis=1)

        ax2 = w * (0.64 + 0.22 * math.cos(ph))
        ay2 = 1720.0 + 48.0 * math.sin(ph)
        g_bot_half = np.exp(-(((bx - ax2) / 480.0) ** 2 + ((by - ay2) / 210.0) ** 2) * 1.25) * 24.0
        g_bot = np.repeat(np.repeat(g_bot_half, 2, axis=0), 2, axis=1)

        _stamp_band(top_f, 0, top_h, top_grid, ph)
        _stamp_band(bot_f, bot_y0, bot_h, bot_grid, ph)

        frame_buf[:top_h, :, 0] = np.clip(4.0 + g_top * 0.16 + top_f * 238.0, 0.0, 255.0).astype(np.uint8)
        frame_buf[:top_h, :, 1] = np.clip(4.0 + g_top * 0.92 + top_f * 242.0, 0.0, 255.0).astype(np.uint8)
        frame_buf[:top_h, :, 2] = np.clip(6.0 + g_top * 0.64 + top_f * 245.0, 0.0, 255.0).astype(np.uint8)

        frame_buf[bot_y0:, :, 0] = np.clip(4.0 + g_bot * 0.16 + bot_f * 238.0, 0.0, 255.0).astype(np.uint8)
        frame_buf[bot_y0:, :, 1] = np.clip(4.0 + g_bot * 0.92 + bot_f * 242.0, 0.0, 255.0).astype(np.uint8)
        frame_buf[bot_y0:, :, 2] = np.clip(6.0 + g_bot * 0.64 + bot_f * 245.0, 0.0, 255.0).astype(np.uint8)

        proc.stdin.write(frame_buf.tobytes())

    proc.stdin.close()
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"FFmpeg animated background generation failed with code {rc}")
    return out_mp4


def _esc(val: Any) -> str:
    return html.escape(str(val or ""), quote=True)


def _avatar_data_uri(path_str: str | None) -> str:
    if not path_str:
        return ""
    p = Path(path_str)
    if not p.is_file():
        return ""
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _format_srt_ts(sec: float) -> str:
    ms = max(0, int(round(sec * 1000)))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, r = divmod(rem, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d},{r:03d}"


def _format_ass_ts(sec: float) -> str:
    cs = max(0, int(round(sec * 100)))
    h, rem = divmod(cs, 360_000)
    m, rem = divmod(rem, 6_000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def _highlight_ass_terms(text: str) -> str:
    clean = text.replace("{", "").replace("}", "").replace("\n", " ")

    def _repl(m: re.Match[str]) -> str:
        if m.group("term"):
            # Bold deep emerald (&H00577804 = #047857 BGR)
            return r"{\c&H00577804&}" + m.group(0) + r"{\c&H00141416&}"
        # Bold burnt orange (&H000C41C2 = #C2410C BGR) for stars/ranking numbers
        return r"{\c&H000C41C2&}" + m.group(0) + r"{\c&H00141416&}"

    styled = _ASS_HL_RE.sub(_repl, clean)
    return r"{\fscx96\fscy96\t(0,80,\fscx100\fscy100)}" + styled


def generate_ambient_bgm_wav(duration_sec: float, out_wav: Path) -> Path:
    """Synthesize a soft copyright-free dark-tech ambient chord pad (44.1kHz mono WAV)."""
    sr = 44100
    n_samples = max(sr, int(math.ceil((duration_sec + 0.5) * sr)))
    t = np.linspace(0.0, n_samples / sr, n_samples, endpoint=False, dtype=np.float64)

    # Warm Am9 -> Fmaj7 -> Cmaj7 -> G6 chord progression every 4s
    chords = [
        (110.0, 164.81, 220.0, 261.63),   # Am
        (87.31, 130.81, 174.61, 220.0),   # Fmaj7
        (130.81, 196.00, 246.94, 329.63), # Cmaj7
        (98.00, 146.83, 196.00, 246.94),  # G6
    ]
    signal = np.zeros_like(t)
    chord_len = 4.0
    for idx, freqs in enumerate(chords):
        phase = ((t % (chord_len * len(chords))) >= idx * chord_len) & (
            (t % (chord_len * len(chords))) < (idx + 1) * chord_len
        )
        # Smooth crossfade envelope across chord transitions
        local_t = (t - idx * chord_len) % (chord_len * len(chords))
        env = np.where(phase, np.sin(np.pi * np.clip(local_t / chord_len, 0.0, 1.0)), 0.0)
        pad = sum(np.sin(2.0 * np.pi * f * t) * (0.35 if i == 0 else 0.18) for i, f in enumerate(freqs))
        signal += env * pad

    # Subtle slow breathing LFO + fade-in / fade-out
    lfo = 0.78 + 0.22 * np.sin(2.0 * np.pi * 0.25 * t)
    signal *= lfo
    fade_in = min(int(0.8 * sr), n_samples // 4)
    fade_out = min(int(1.2 * sr), n_samples // 4)
    signal[:fade_in] *= np.linspace(0.0, 1.0, fade_in)
    signal[-fade_out:] *= np.linspace(1.0, 0.0, fade_out)

    peak = np.max(np.abs(signal)) or 1.0
    pcm = np.int16(np.clip((signal / peak) * 0.65, -1.0, 1.0) * 32767)
    with wave.open(str(out_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    return out_wav


def _build_repo_map(storyboard: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        s["full_name"]: s
        for s in storyboard.get("segments", [])
        if s.get("kind") == "repo" and s.get("full_name")
    }


def _headline_font_size(text: str, large: int = 54, mid: int = 48, small: int = 42) -> int:
    n = len(text or "")
    if n <= 13:
        return large
    if n <= 17:
        return mid
    return small


def _stroked_svg_title(
    text: str,
    *,
    font_size: int,
    fill_color: str,
    stroke_w: int = 8,
    width: int = 976,
    height: int = 70,
    shadow_alpha: float = 0.20,
) -> str:
    y_pos = int(round(height * 0.78))
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg" style="display:block;overflow:visible;">'
        f'<text x="6" y="{y_pos}" '
        f'font-family="\'Microsoft YaHei\', \'PingFang SC\', \'SimHei\', sans-serif" '
        f'font-weight="900" font-style="italic" font-size="{font_size}" '
        f'fill="{fill_color}" stroke="#141416" stroke-width="{stroke_w}" '
        f'stroke-linejoin="round" stroke-linecap="round" paint-order="stroke fill" '
        f'style="filter: drop-shadow(0 4px 10px rgba(0,0,0,{shadow_alpha:.2f}));">'
        f"{_esc(text)}</text></svg>"
    )


def _build_editorial_shell_html(
    *,
    header_pill: str,
    viewport_bg: str,
    viewport_inner_html: str,
    kicker_text: str,
    headline_text: str,
    meta_bar_html: str,
    total_repos: int,
    active_step: int,
    phase: float,
) -> str:
    hl_size = _headline_font_size(headline_text)
    kicker_svg = _stroked_svg_title(
        kicker_text, font_size=54, fill_color="#86EFAC", stroke_w=8, width=976, height=68, shadow_alpha=0.16
    )
    headline_svg = _stroked_svg_title(
        headline_text, font_size=hl_size, fill_color="#FFFFFF", stroke_w=9, width=976, height=70, shadow_alpha=0.22
    )
    bars_html = ""
    for idx in range(1, max(1, total_repos) + 1):
        cls = "sb-cur" if idx == active_step else ("sb-done" if idx < active_step else "sb-idle")
        bars_html += f'<div class="sb-seg {cls}"></div>'

    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{BASE_CSS}
body {{
  width: 1080px; height: 1920px; position: relative;
  background: transparent;
}}
.white-card {{
  position: absolute; left: 26px; top: 220px; width: 1028px; height: 1320px;
  background: #FFFFFF; border-radius: 28px;
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.72);
  z-index: 1;
}}
.browser-box {{
  position: absolute; left: 46px; top: 238px; width: 988px; height: 970px;
  border: 5px solid #141416; border-radius: 20px;
  background: #FFFFFF; overflow: hidden;
  display: flex; flex-direction: column;
  z-index: 2;
}}
.browser-hdr {{
  height: 78px; flex-shrink: 0;
  border-bottom: 3px solid #141416;
  padding: 0 22px; background: #FFFFFF;
  display: flex; align-items: center; justify-content: space-between;
}}
.hdr-left {{ display: flex; align-items: center; gap: 12px; min-width: 0; }}
.masthead {{
  font-size: 36px; font-weight: 900; color: #141416; letter-spacing: 0.6px;
  line-height: 1;
}}
.ctx-pill {{
  background: #F3F4F6; color: #374151;
  border: 1.5px solid #D1D5DB;
  font-size: 18px; font-weight: 800; padding: 5px 13px; border-radius: 999px;
  white-space: nowrap;
}}
.mac-dots {{ display: flex; align-items: center; gap: 13px; flex-shrink: 0; }}
.mac-dot {{ width: 26px; height: 26px; border-radius: 50%; }}
.viewport {{
  width: 978px; height: 744px; flex-shrink: 0;
  position: relative; overflow: hidden;
  background: {viewport_bg};
}}
.sub-strip {{
  height: 138px; flex-shrink: 0; width: 100%;
  border-top: 1.5px solid #E5E7EB;
  background: #FFFFFF;
}}
.editorial-footer {{
  position: absolute; left: 46px; top: 1218px; width: 988px; height: 306px;
  display: flex; flex-direction: column; justify-content: space-between;
  padding: 14px 6px 4px 6px;
  z-index: 2;
}}
.titles-wrap {{ display: flex; flex-direction: column; gap: 14px; }}
.meta-bar {{
  border-top: 1.5px solid #E5E7EB;
  padding-top: 14px;
  display: flex; align-items: center; justify-content: space-between; gap: 14px;
}}
.meta-left {{ display: flex; align-items: center; gap: 12px; min-width: 0; flex: 1; }}
.meta-avatar {{
  width: 38px; height: 38px; border-radius: 9px; flex-shrink: 0;
  border: 1px solid #D1D5DB; background: #F9FAFB; object-fit: cover;
}}
.meta-repo {{
  font-size: 22px; font-weight: 800; color: #141416;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}}
.meta-total {{ font-size: 20px; font-weight: 700; color: #4B5563; white-space: nowrap; }}
.meta-right {{ display: flex; align-items: center; gap: 8px; flex-shrink: 0; }}
.verdict-chip {{
  background: #ECFDF5; color: #065F46;
  border: 1.5px solid #6EE7B7;
  font-size: 19px; font-weight: 800; padding: 5px 13px; border-radius: 10px;
}}
.lic-chip {{
  background: #F3F4F6; color: #374151;
  border: 1px solid #D1D5DB;
  font-size: 18px; font-weight: 700; padding: 5px 11px; border-radius: 10px;
}}
.story-bars {{
  position: absolute; left: 46px; top: 1580px; width: 988px; height: 6px;
  display: flex; gap: 12px; z-index: 2;
}}
.sb-seg {{ flex: 1; height: 6px; border-radius: 999px; }}
.sb-done {{ background: rgba(255, 255, 255, 0.65); }}
.sb-cur {{ background: #86EFAC; box-shadow: 0 0 10px rgba(134, 239, 172, 0.55); }}
.sb-idle {{ background: rgba(255, 255, 255, 0.22); }}
</style></head><body>
  <div class="white-card"></div>
  <div class="browser-box">
    <div class="browser-hdr">
      <div class="hdr-left">
        {_github_svg(34, "#141416")}
        <span class="masthead editorial-heavy">GitHub风向标</span>
        <span class="ctx-pill">{_esc(header_pill)}</span>
      </div>
      <div class="mac-dots">
        <span class="mac-dot" style="background:#D85A4A;"></span>
        <span class="mac-dot" style="background:#EAB338;"></span>
        <span class="mac-dot" style="background:#65C466;"></span>
      </div>
    </div>
    <div class="viewport">{viewport_inner_html}</div>
    <div class="sub-strip"></div>
  </div>
  <div class="editorial-footer">
    <div class="titles-wrap">
      {kicker_svg}
      {headline_svg}
    </div>
    <div class="meta-bar">{meta_bar_html}</div>
  </div>
  <div class="story-bars">{bars_html}</div>
</body></html>"""


def _build_list_viewport_html(storyboard: dict[str, Any], seg: dict[str, Any], *, is_outro: bool) -> str:
    repo_map = _build_repo_map(storyboard)
    date_range = seg.get("date_range") or storyboard.get("date_range_display") or ""
    rows_html = ""
    for item in seg.get("repos_preview", []):
        repo_seg = repo_map.get(item["full_name"], {})
        avatar_uri = _avatar_data_uri(
            item.get("avatar_path") or (repo_seg.get("media") or {}).get("avatar_path")
        )
        avatar_html = (
            f'<img class="lv-av" src="{avatar_uri}" alt="logo" />'
            if avatar_uri
            else f'<div class="lv-av-fb">#{item["rank"]}</div>'
        )
        one_liner = item.get("one_liner") or repo_seg.get("one_liner") or item.get("language") or ""
        lang = item.get("language") or repo_seg.get("language") or "Open Source"
        lic = repo_seg.get("license") or ""
        verdict = item.get("recommendation_verdict") or repo_seg.get("recommendation_verdict") or ""
        sub_tag = verdict if (is_outro and verdict) else (f"{lang} · {lic}" if lic else lang)
        url_line = f'<div class="lv-url mono">{_esc(item.get("url", ""))}</div>' if is_outro and item.get("url") else ""

        rows_html += f"""
        <div class="lv-row">
          <div class="lv-rk">#{item['rank']}</div>
          {avatar_html}
          <div class="lv-mid">
            <div class="lv-top">
              <span class="lv-name mono">{_esc(item['full_name'])}</span>
              <span class="lv-chip">{_esc(sub_tag)}</span>
            </div>
            <div class="lv-desc">{_esc(one_liner)}</div>
            {url_line}
          </div>
          <div class="lv-star">+{int(item['stars_delta']):,} ★</div>
        </div>"""

    banner_right = "官方 Demo 与实拍演示精选" if not is_outro else "开源协议与仓库直达链接"
    return f"""<style>
.lv-wrap {{
  width: 978px; height: 744px; padding: 18px 24px;
  display: flex; flex-direction: column; gap: 11px;
  background: #FAFAFB;
}}
.lv-head {{
  display: flex; align-items: center; justify-content: space-between;
  padding-bottom: 8px; border-bottom: 1.5px solid #E5E7EB;
}}
.lv-date {{ font-size: 19px; font-weight: 800; color: #141416; }}
.lv-note {{
  font-size: 17px; font-weight: 800; color: #065F46;
  background: #ECFDF5; border: 1px solid #6EE7B7;
  padding: 4px 12px; border-radius: 999px;
}}
.lv-list {{ flex: 1; display: flex; flex-direction: column; justify-content: space-between; gap: 9px; }}
.lv-row {{
  flex: 1;
  display: flex; align-items: center; gap: 14px;
  background: #FFFFFF; border: 1.5px solid #E5E7EB;
  border-radius: 14px; padding: 10px 16px;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.03);
}}
.lv-rk {{
  width: 44px; height: 44px; border-radius: 11px; flex-shrink: 0;
  background: #141416; color: #86EFAC;
  font-size: 21px; font-weight: 900;
  display: flex; align-items: center; justify-content: center;
}}
.lv-av {{
  width: 46px; height: 46px; border-radius: 11px; flex-shrink: 0;
  border: 1px solid #D1D5DB; background: #FFFFFF; object-fit: cover;
}}
.lv-av-fb {{
  width: 46px; height: 46px; border-radius: 11px; flex-shrink: 0;
  background: #F3F4F6; color: #141416; font-weight: 900; font-size: 19px;
  display: flex; align-items: center; justify-content: center;
}}
.lv-mid {{ flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }}
.lv-top {{ display: flex; align-items: center; gap: 8px; min-width: 0; }}
.lv-name {{
  font-size: 21px; font-weight: 900; color: #111827;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}}
.lv-chip {{
  flex-shrink: 0; font-size: 15px; font-weight: 800; color: #374151;
  background: #F3F4F6; border: 1px solid #D1D5DB;
  padding: 2px 9px; border-radius: 7px;
}}
.lv-desc {{
  font-size: 19px; color: #4B5563; font-weight: 600;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}}
.lv-url {{
  font-size: 15px; color: #047857; font-weight: 700;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}}
.lv-star {{
  flex-shrink: 0;
  background: #FEF3C7; color: #92400E;
  border: 1.5px solid #F59E0B;
  border-radius: 10px; padding: 6px 13px;
  font-size: 20px; font-weight: 900; white-space: nowrap;
}}
</style>
<div class="lv-wrap">
  <div class="lv-head">
    <div class="lv-date mono">统计周期 · {_esc(date_range)}</div>
    <div class="lv-note">{_esc(banner_right)}</div>
  </div>
  <div class="lv-list">{rows_html}</div>
</div>"""


def _build_hook_html(storyboard: dict[str, Any], seg: dict[str, Any]) -> str:
    repos_cnt = len(seg.get("repos_preview") or []) or 5
    date_range = seg.get("date_range") or storyboard.get("date_range_display") or ""
    period_word = "本日" if storyboard["period"] == "daily" else "一周"
    meta_html = f"""
      <div class="meta-left">
        <span class="meta-repo mono">{_esc(date_range)}</span>
        <span class="meta-total">· Top {repos_cnt} 开源项目实测</span>
      </div>
      <div class="meta-right">
        <span class="verdict-chip">真实 Star 数据与官网演示</span>
      </div>"""
    return _build_editorial_shell_html(
        header_pill=f"{storyboard['cover_badge']}",
        viewport_bg="#FAFAFB",
        viewport_inner_html=_build_list_viewport_html(storyboard, seg, is_outro=False),
        kicker_text=f"{period_word}GitHub大事件",
        headline_text=seg.get("title") or storyboard.get("video_title") or "五大飙升开源项目盘点",
        meta_bar_html=meta_html,
        total_repos=repos_cnt,
        active_step=1,
        phase=0.2,
    )


def _build_repo_bg_html(storyboard: dict[str, Any], seg: dict[str, Any], total_repos: int) -> str:
    period_word = "今日" if storyboard["period"] == "daily" else "一周"
    avatar_uri = _avatar_data_uri((seg.get("media") or {}).get("avatar_path"))
    avatar_html = (
        f'<img class="meta-avatar" src="{avatar_uri}" alt="logo" />'
        if avatar_uri
        else ""
    )
    meta_html = f"""
      <div class="meta-left">
        {avatar_html}
        <span class="meta-repo mono">{_esc(seg['full_name'])}</span>
        <span class="meta-total">累计 {_esc(seg['stars_total_display'])} ★</span>
      </div>
      <div class="meta-right">
        <span class="verdict-chip">{_esc(seg['recommendation_verdict'])}</span>
        <span class="lic-chip mono">{_esc(seg['license'])}</span>
      </div>"""
    return _build_editorial_shell_html(
        header_pill=f"TOP {seg['rank']} / {total_repos} · {seg['language']}",
        viewport_bg="#FFFFFF",
        viewport_inner_html="",
        kicker_text=f"{period_word}GitHub飙升 · TOP {seg['rank']}",
        headline_text=seg["card_headline"],
        meta_bar_html=meta_html,
        total_repos=total_repos,
        active_step=int(seg["rank"]),
        phase=0.7 * int(seg["rank"]),
    )


def _build_repo_fg_badges_html(storyboard: dict[str, Any], seg: dict[str, Any]) -> str:
    """Transparent 978x744 overlay placing subtle top-corner badges over the media stage."""
    period_label = "今日" if storyboard["period"] == "daily" else "本周"
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{BASE_CSS}
body {{ width: 978px; height: 744px; position: relative; background: transparent; }}
.src-tag {{
  position: absolute; left: 16px; top: 14px;
  background: rgba(255, 255, 255, 0.94); color: #141416;
  border: 1.5px solid rgba(20, 20, 22, 0.18);
  font-size: 16px; font-weight: 800; padding: 5px 12px; border-radius: 9px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.10);
}}
.mint-badge {{
  position: absolute; right: 16px; top: 14px;
  background: #86EFAC; color: #052E16;
  border: 2px solid #141416;
  font-size: 20px; font-weight: 900; padding: 5px 15px; border-radius: 11px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.14);
}}
</style></head><body>
  <div class="src-tag mono">素材源自 GitHub · {_esc(seg['license'])}</div>
  <div class="mint-badge">{period_label} {_esc(seg['stars_delta_display'])} ★</div>
</body></html>"""


def _build_beat1_stats_html(storyboard: dict[str, Any], seg: dict[str, Any]) -> str:
    """Beat 1 stage card (978x744, light paper editorial): Star telemetry, features & dark CLI."""
    total = max(1, int(seg["stargazers_count"]))
    delta = max(0, int(seg["stars_delta"]))
    growth_pct = min(100.0, max(8.0, (delta / total) * 100.0))
    period_word = "24 小时新增星标" if storyboard["period"] == "daily" else "过去 7 天新增星标"
    cli_cmd = seg.get("cli_snippet") or f"git clone https://github.com/{seg['full_name']}.git"
    avatar_uri = _avatar_data_uri((seg.get("media") or {}).get("avatar_path"))
    avatar_html = (
        f'<img class="b1-av" src="{avatar_uri}" alt="logo" />'
        if avatar_uri
        else ""
    )
    feats = "".join(
        f'<div class="b1-feat"><span class="b1-fnum">0{i}</span><span class="b1-ftxt">{_esc(f)}</span></div>'
        for i, f in enumerate((seg.get("core_features") or [])[:3], start=1)
    )

    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{BASE_CSS}
body {{
  width: 978px; height: 744px;
  background: #FAFAFB;
  padding: 66px 28px 24px 28px;
  display: flex; flex-direction: column; justify-content: space-between; gap: 14px;
}}
.b1-top {{
  display: flex; align-items: center; justify-content: space-between;
  padding-bottom: 10px; border-bottom: 1.5px solid #E5E7EB;
}}
.b1-kicker {{
  background: #141416; color: #FFFFFF;
  font-size: 18px; font-weight: 800; padding: 5px 15px; border-radius: 999px;
}}
.b1-url {{ font-size: 18px; font-weight: 700; color: #4B5563; }}
.b1-id {{ display: flex; align-items: center; gap: 16px; }}
.b1-av {{
  width: 58px; height: 58px; border-radius: 14px; flex-shrink: 0;
  border: 1.5px solid #D1D5DB; background: #FFFFFF; object-fit: cover;
}}
.b1-one {{
  font-size: 32px; font-weight: 900; line-height: 1.28; color: #111827;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}}
.metrics-card {{
  background: #FFFFFF;
  border: 1.5px solid #D1D5DB;
  border-radius: 18px; padding: 18px 22px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.04);
}}
.metrics-grid {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; margin-bottom: 12px; }}
.m-lbl {{ font-size: 18px; color: #6B7280; font-weight: 700; }}
.m-amb {{ font-size: 38px; font-weight: 900; color: #D97706; margin-top: 2px; }}
.m-ink {{ font-size: 38px; font-weight: 900; color: #111827; margin-top: 2px; }}
.m-emr {{ font-size: 36px; font-weight: 900; color: #059669; margin-top: 4px; }}
.bar-track {{
  width: 100%; height: 18px; background: #E5E7EB;
  border-radius: 999px; overflow: hidden;
}}
.bar-fill {{
  height: 100%; width: {growth_pct:.1f}%;
  background: linear-gradient(90deg, #10B981, #86EFAC); border-radius: 999px;
}}
.b1-feats {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px; }}
.b1-feat {{
  background: #FFFFFF; border: 1.5px solid #E5E7EB; border-left: 4px solid #10B981;
  border-radius: 12px; padding: 10px 12px;
  display: flex; align-items: flex-start; gap: 8px;
}}
.b1-fnum {{ font-size: 16px; font-weight: 900; color: #059669; margin-top: 2px; flex-shrink: 0; }}
.b1-ftxt {{ font-size: 18px; font-weight: 700; color: #1F2937; line-height: 1.28; }}
.cli-box {{
  background: #18181B; border: 1.5px solid #27272A;
  border-radius: 15px; padding: 13px 18px;
}}
.cli-hdr {{
  display: flex; align-items: center; justify-content: space-between;
  font-size: 16px; color: #A1A1AA; font-weight: 700; margin-bottom: 6px;
}}
.dots {{ display: flex; gap: 6px; }}
.dot {{ width: 10px; height: 10px; border-radius: 999px; }}
.cli-cmd {{
  font-size: 21px; color: #86EFAC; font-weight: 700;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}}
</style></head><body>
  <div class="b1-top">
    <span class="b1-kicker">01 · 项目定位与星标飙升</span>
    <span class="b1-url mono">github.com/{_esc(seg['full_name'])}</span>
  </div>
  <div class="b1-id">
    {avatar_html}
    <div class="b1-one">{_esc(seg['one_liner'])}</div>
  </div>
  <div class="metrics-card">
    <div class="metrics-grid">
      <div><div class="m-lbl">{period_word}</div><div class="m-amb">{_esc(seg['stars_delta_display'])} ★</div></div>
      <div><div class="m-lbl">GitHub 累计星标</div><div class="m-ink">{_esc(seg['stars_total_display'])} ★</div></div>
      <div><div class="m-lbl">近周增量占比</div><div class="m-emr">{growth_pct:.1f}%</div></div>
    </div>
    <div class="bar-track"><div class="bar-fill"></div></div>
  </div>
  <div class="b1-feats">{feats}</div>
  <div class="cli-box">
    <div class="cli-hdr">
      <span>官方快速上手命令</span>
      <div class="dots">
        <span class="dot" style="background:#D85A4A;"></span>
        <span class="dot" style="background:#EAB338;"></span>
        <span class="dot" style="background:#65C466;"></span>
      </div>
    </div>
    <div class="cli-cmd mono">$ {_esc(cli_cmd)}</div>
  </div>
</body></html>"""


def _build_demo_panel_html(storyboard: dict[str, Any], seg: dict[str, Any]) -> str:
    """194px light-editorial figure caption band (978x194) beneath 16:9 (978x550) demo video."""
    feats = "".join(
        f'<div class="dp-item"><span class="dp-bul">▪</span><span>{_esc(f)}</span></div>'
        for f in (seg.get("core_features") or [])[:3]
    )
    cases = "".join(
        f'<span class="dp-pill">{_esc(u)}</span>'
        for u in (seg.get("use_cases") or [])[:2]
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{BASE_CSS}
body {{
  width: 978px; height: 194px;
  background: #F8FAFC;
  border-top: 2.5px solid #141416;
  padding: 14px 22px;
  display: grid; grid-template-columns: 1.08fr 0.92fr; gap: 18px;
}}
.dp-col {{ display: flex; flex-direction: column; justify-content: space-between; min-width: 0; }}
.dp-hdr {{ display: flex; align-items: center; gap: 10px; }}
.live-pill {{
  display: inline-flex; align-items: center; gap: 7px;
  background: #141416; color: #FFFFFF;
  font-size: 16px; font-weight: 900; padding: 4px 12px; border-radius: 999px;
}}
.live-dot {{ width: 9px; height: 9px; border-radius: 50%; background: #EF4444; }}
.dp-subhdr {{ font-size: 17px; font-weight: 800; color: #059669; }}
.dp-list {{ display: flex; flex-direction: column; gap: 5px; }}
.dp-item {{
  display: flex; align-items: baseline; gap: 7px;
  font-size: 18px; font-weight: 700; color: #1F2937;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}}
.dp-bul {{ color: #10B981; font-weight: 900; }}
.dp-pills {{ display: flex; gap: 8px; flex-wrap: wrap; }}
.dp-pill {{
  background: #ECFDF5; color: #065F46; border: 1px solid #6EE7B7;
  font-size: 17px; font-weight: 800; padding: 5px 12px; border-radius: 9px;
}}
.dp-aud {{ font-size: 18px; font-weight: 700; color: #374151; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.dp-diff {{ font-size: 17px; font-weight: 600; color: #4B5563; line-height: 1.3; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }}
</style></head><body>
  <div class="dp-col">
    <div class="dp-hdr">
      <span class="live-pill"><span class="live-dot"></span>官方实拍 Demo</span>
      <span class="dp-subhdr">{_esc(seg['full_name'])}</span>
    </div>
    <div class="dp-list">{feats}</div>
  </div>
  <div class="dp-col" style="border-left:1.5px solid #E5E7EB;padding-left:18px;">
    <div class="dp-pills">{cases}</div>
    <div class="dp-aud"><span style="color:#6B7280;">适合人群：</span>{_esc(seg['target_audience'])}</div>
    <div class="dp-diff">{_esc(seg['vs_alternative'])}</div>
  </div>
</body></html>"""


def _build_beat4_value_html(seg: dict[str, Any]) -> str:
    """Beat 4 stage card (978x744, light paper editorial): Differentiator, Use Cases, Verdict & CLI."""
    cases = seg.get("use_cases") or []
    case1 = cases[0] if len(cases) > 0 else seg["one_liner"]
    case2 = cases[1] if len(cases) > 1 else seg["target_audience"]
    cli_cmd = seg.get("cli_snippet") or f"git clone https://github.com/{seg['full_name']}.git"
    feats_chips = "".join(f'<span class="b4-fchip">{_esc(f)}</span>' for f in (seg.get("core_features") or [])[:3])
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{BASE_CSS}
body {{
  width: 978px; height: 744px;
  background: #FAFAFB;
  padding: 66px 28px 24px 28px;
  display: flex; flex-direction: column; justify-content: space-between; gap: 12px;
}}
.b4-top {{ display: flex; align-items: center; justify-content: space-between; }}
.b4-kicker {{
  background: #141416; color: #FFFFFF;
  font-size: 18px; font-weight: 800; padding: 5px 15px; border-radius: 999px;
}}
.b4-verdict {{
  background: #ECFDF5; color: #065F46; border: 1.5px solid #6EE7B7;
  font-size: 19px; font-weight: 900; padding: 5px 15px; border-radius: 10px;
}}
.sec-lbl {{ font-size: 18px; font-weight: 900; color: #047857; margin-bottom: 7px; }}
.diff-card {{
  background: #FFFFFF; border: 1.5px solid #D1D5DB; border-left: 5px solid #10B981;
  border-radius: 16px; padding: 15px 20px;
  font-size: 25px; font-weight: 800; color: #111827; line-height: 1.35;
}}
.cases-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
.case-card {{
  background: #FFFFFF; border: 1.5px solid #D1D5DB;
  border-radius: 16px; padding: 14px 18px;
}}
.case-tag {{ font-size: 15px; font-weight: 900; color: #059669; margin-bottom: 5px; letter-spacing: 0.5px; }}
.case-txt {{ font-size: 23px; font-weight: 800; color: #111827; line-height: 1.3; }}
.b4-fchips {{ display: flex; gap: 8px; flex-wrap: wrap; }}
.b4-fchip {{
  background: #F3F4F6; color: #374151; border: 1px solid #D1D5DB;
  font-size: 17px; font-weight: 700; padding: 5px 12px; border-radius: 9px;
}}
.b4-bottom {{
  display: flex; align-items: center; justify-content: space-between; gap: 14px;
  background: #18181B; border-radius: 14px; padding: 12px 18px;
}}
.b4-aud {{ font-size: 19px; font-weight: 700; color: #F9FAFB; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.b4-cli {{
  font-size: 18px; color: #86EFAC; font-weight: 700;
  max-width: 440px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex-shrink: 0;
}}
</style></head><body>
  <div class="b4-top">
    <span class="b4-kicker">04 · 技术亮点与实战评估</span>
    <span class="b4-verdict">{_esc(seg['recommendation_verdict'])}</span>
  </div>
  <div>
    <div class="sec-lbl">相比传统方案的区别</div>
    <div class="diff-card">{_esc(seg['vs_alternative'])}</div>
  </div>
  <div>
    <div class="sec-lbl">最推荐的典型落地场景</div>
    <div class="cases-grid">
      <div class="case-card"><div class="case-tag">SCENARIO 01</div><div class="case-txt">{_esc(case1)}</div></div>
      <div class="case-card"><div class="case-tag">SCENARIO 02</div><div class="case-txt">{_esc(case2)}</div></div>
    </div>
  </div>
  <div class="b4-fchips">{feats_chips}</div>
  <div class="b4-bottom">
    <div class="b4-aud"><span style="color:#86EFAC;">推荐人群：</span>{_esc(seg['target_audience'])}</div>
    <div class="b4-cli mono">$ {_esc(cli_cmd)}</div>
  </div>
</body></html>"""


def _build_outro_html(storyboard: dict[str, Any], seg: dict[str, Any]) -> str:
    repos_cnt = len(seg.get("repos_preview") or []) or 5
    date_range = seg.get("date_range") or storyboard.get("date_range_display") or ""
    period_word = "本日" if storyboard["period"] == "daily" else "本周"
    meta_html = f"""
      <div class="meta-left">
        <span class="meta-repo mono">{_esc(date_range)}</span>
        <span class="meta-total">· 已核验真实仓库与开源协议</span>
      </div>
      <div class="meta-right">
        <span class="verdict-chip">点赞收藏追更下期 Top 5</span>
      </div>"""
    return _build_editorial_shell_html(
        header_pill=f"{period_word}榜单汇总 · Top {repos_cnt}",
        viewport_bg="#FAFAFB",
        viewport_inner_html=_build_list_viewport_html(storyboard, seg, is_outro=True),
        kicker_text=f"{period_word}Top{repos_cnt}精选汇总",
        headline_text="评论区聊聊你最想先部署哪个",
        meta_bar_html=meta_html,
        total_repos=repos_cnt,
        active_step=repos_cnt,
        phase=4.2,
    )


def _build_cover_html(storyboard: dict[str, Any], width: int, height: int) -> str:
    repos = [s for s in storyboard["segments"] if s["kind"] == "repo"]
    cards = ""
    for r in repos[:5]:
        avatar_uri = _avatar_data_uri((r.get("media") or {}).get("avatar_path"))
        avatar_html = (
            f'<img class="c-av" src="{avatar_uri}" alt="logo" />'
            if avatar_uri
            else f'<div class="c-av-fb">#{r["rank"]}</div>'
        )
        cards += f"""
        <div class="c-row">
          <div class="c-rk">#{r['rank']}</div>
          {avatar_html}
          <div class="c-mid">
            <div class="c-nm mono">{_esc(r['full_name'])}</div>
            <div class="c-sub">{_esc(r['card_headline'])} · {_esc(r['language'])}</div>
          </div>
          <div class="c-st">{_esc(r['stars_delta_display'])} ★</div>
        </div>"""

    is_tall = height >= 1800
    card_top = 230 if is_tall else 82
    card_h = 1360 if is_tall else 1276
    t1_size = 72 if is_tall else 62
    t2_size = 66 if is_tall else 56
    l1_svg = _stroked_svg_title(
        storyboard["cover_line1"],
        font_size=t1_size,
        fill_color="#86EFAC",
        stroke_w=10,
        width=width - 120,
        height=92 if is_tall else 80,
        shadow_alpha=0.18,
    )
    l2_svg = _stroked_svg_title(
        storyboard["cover_line2"],
        font_size=t2_size,
        fill_color="#FFFFFF",
        stroke_w=11,
        width=width - 120,
        height=88 if is_tall else 76,
        shadow_alpha=0.22,
    )
    row_pad = "12px 16px" if is_tall else "10px 15px"
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{BASE_CSS}
body {{ width: {width}px; height: {height}px; position: relative; background: #040406; }}
#dot-wave {{ position: absolute; left: 0; top: 0; width: {width}px; height: {height}px; z-index: 0; }}
.cover-card {{
  position: absolute; left: 28px; top: {card_top}px; width: {width - 56}px; height: {card_h}px;
  background: #FFFFFF; border-radius: 28px; padding: 24px 24px 26px 24px;
  display: flex; flex-direction: column; justify-content: space-between;
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.72); z-index: 1;
}}
.win {{
  border: 5px solid #141416; border-radius: 20px; background: #FAFAFB; overflow: hidden;
  display: flex; flex-direction: column; flex: 1; margin-bottom: 16px;
}}
.win-hdr {{
  height: 76px; flex-shrink: 0; border-bottom: 3px solid #141416; padding: 0 22px;
  background: #FFFFFF; display: flex; align-items: center; justify-content: space-between;
}}
.win-left {{ display: flex; align-items: center; gap: 12px; }}
.win-brand {{ font-size: 36px; font-weight: 900; color: #141416; }}
.win-pill {{
  background: #F3F4F6; color: #374151; border: 1.5px solid #D1D5DB;
  font-size: 18px; font-weight: 800; padding: 5px 13px; border-radius: 999px;
}}
.mac-dots {{ display: flex; gap: 13px; }}
.mac-dot {{ width: 26px; height: 26px; border-radius: 50%; }}
.win-body {{
  flex: 1; padding: 16px 18px; display: flex; flex-direction: column; justify-content: space-between; gap: 10px;
}}
.c-row {{
  flex: 1; display: flex; align-items: center; gap: 13px;
  background: #FFFFFF; border: 1.5px solid #E5E7EB; border-radius: 14px; padding: {row_pad};
}}
.c-rk {{
  background: #141416; color: #86EFAC; font-weight: 900; font-size: 22px;
  border-radius: 10px; width: 46px; height: 46px; display: flex; align-items: center; justify-content: center; flex-shrink: 0;
}}
.c-av {{
  width: 48px; height: 48px; border-radius: 11px; flex-shrink: 0;
  border: 1px solid #D1D5DB; background: #FFFFFF; object-fit: cover;
}}
.c-av-fb {{
  width: 48px; height: 48px; border-radius: 11px; flex-shrink: 0;
  background: #F3F4F6; color: #141416; font-weight: 900; font-size: 19px;
  display: flex; align-items: center; justify-content: center;
}}
.c-mid {{ flex: 1; min-width: 0; }}
.c-nm {{ font-size: 22px; font-weight: 900; color: #111827; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.c-sub {{ font-size: 18px; color: #4B5563; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 2px; }}
.c-st {{
  background: #FEF3C7; color: #92400E; border: 1.5px solid #F59E0B;
  font-weight: 900; font-size: 21px; padding: 6px 12px; border-radius: 10px; flex-shrink: 0;
}}
.c-titles {{ padding: 4px 6px; display: flex; flex-direction: column; gap: 8px; }}
</style></head><body>
  <canvas id="dot-wave" width="{width}" height="{height}"></canvas>
  <div class="cover-card">
    <div class="win">
      <div class="win-hdr">
        <div class="win-left">
          {_github_svg(34, "#141416")}
          <span class="win-brand editorial-heavy">GitHub风向标</span>
          <span class="win-pill">{_esc(storyboard['cover_badge'])} · {_esc(storyboard.get('date_range_display', ''))}</span>
        </div>
        <div class="mac-dots">
          <span class="mac-dot" style="background:#D85A4A;"></span>
          <span class="mac-dot" style="background:#EAB338;"></span>
          <span class="mac-dot" style="background:#65C466;"></span>
        </div>
      </div>
      <div class="win-body">{cards}</div>
    </div>
    <div class="c-titles">
      {l1_svg}
      {l2_svg}
    </div>
  </div>
  {_dot_wave_script(1.1, width, height)}
</body></html>"""


def resolve_output_date_tag(storyboard: dict[str, Any], report: dict[str, Any] | None = None) -> str:
    """Return compact YYYYMMDD issue date tag from date_range_display (trailing date if range)."""
    disp = str(storyboard.get("date_range_display") or (report or {}).get("date_range_display") or "")
    matches = re.findall(r"(20\d{2})[.\-/]?(\d{2})[.\-/]?(\d{2})", disp)
    if matches:
        y, m, d = matches[-1]
        return f"{y}{m}{d}"
    return dt.datetime.now().strftime("%Y%m%d")


def _render_html_pngs(storyboard: dict[str, Any], out_dir: Path) -> dict[str, dict[str, Path]]:
    date_tag = resolve_output_date_tag(storyboard)
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    rendered: dict[str, dict[str, Path]] = {
        "segments": {},
        "fg_badges": {},
        "beat1": {},
        "beat4": {},
        "demo_bar": {},
        "covers": {},
    }
    repo_count = sum(1 for s in storyboard["segments"] if s["kind"] == "repo")

    with sync_playwright() as pw:
        browser = launch_chromium(pw)
        try:
            page = browser.new_page(viewport={"width": 1080, "height": 1920}, device_scale_factor=1)
            for seg in storyboard["segments"]:
                seg_id = seg["id"]
                if seg["kind"] == "hook":
                    out_png = frames_dir / f"{seg_id}.png"
                    page.set_viewport_size({"width": 1080, "height": 1920})
                    page.set_content(_build_hook_html(storyboard, seg))
                    page.screenshot(path=str(out_png), omit_background=True)
                    rendered["segments"][seg_id] = out_png
                elif seg["kind"] == "repo":
                    page.set_viewport_size({"width": 1080, "height": 1920})
                    bg_png = frames_dir / f"{seg_id}_shell.png"
                    page.set_content(_build_repo_bg_html(storyboard, seg, repo_count))
                    page.screenshot(path=str(bg_png), omit_background=True)
                    rendered["segments"][seg_id] = bg_png

                    page.set_viewport_size({"width": 978, "height": 744})
                    fg_png = frames_dir / f"{seg_id}_fg_badges.png"
                    page.set_content(_build_repo_fg_badges_html(storyboard, seg))
                    page.screenshot(path=str(fg_png), omit_background=True)
                    rendered["fg_badges"][seg_id] = fg_png

                    b1_png = frames_dir / f"{seg_id}_beat1.png"
                    page.set_content(_build_beat1_stats_html(storyboard, seg))
                    page.screenshot(path=str(b1_png), omit_background=False)
                    rendered["beat1"][seg_id] = b1_png

                    b4_png = frames_dir / f"{seg_id}_beat4.png"
                    page.set_content(_build_beat4_value_html(seg))
                    page.screenshot(path=str(b4_png), omit_background=False)
                    rendered["beat4"][seg_id] = b4_png

                    page.set_viewport_size({"width": 978, "height": 194})
                    bar_png = frames_dir / f"{seg_id}_demo_panel.png"
                    page.set_content(_build_demo_panel_html(storyboard, seg))
                    page.screenshot(path=str(bar_png), omit_background=False)
                    rendered["demo_bar"][seg_id] = bar_png
                else:
                    page.set_viewport_size({"width": 1080, "height": 1920})
                    out_png = frames_dir / f"{seg_id}.png"
                    page.set_content(_build_outro_html(storyboard, seg))
                    page.screenshot(path=str(out_png), omit_background=True)
                    rendered["segments"][seg_id] = out_png

            page.set_viewport_size({"width": 1080, "height": 1920})
            cover_9_16 = out_dir / f"9x16-{date_tag}.png"
            page.set_content(_build_cover_html(storyboard, 1080, 1920))
            page.screenshot(path=str(cover_9_16))
            rendered["covers"]["cover_9x16"] = cover_9_16

            page.set_viewport_size({"width": 1080, "height": 1440})
            cover_3_4 = out_dir / f"3x4-{date_tag}.png"
            page.set_content(_build_cover_html(storyboard, 1080, 1440))
            page.screenshot(path=str(cover_3_4))
            rendered["covers"]["cover_3x4"] = cover_3_4
        finally:
            browser.close()
    return rendered


def _run_ffmpeg(args: list[str], cwd: Path) -> None:
    res = subprocess.run(
        [str(FFMPEG_EXE), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg failed ({res.returncode}):\n{res.stderr[-1500:]}")


def _pick_beat2_and_beat3(seg: dict[str, Any]) -> tuple[tuple[str, Path], tuple[str, Path]]:
    """Choose two distinct real media assets for Beat 2 & Beat 3."""
    m = seg["media"]
    demo = Path(m["demo_video_path"]) if m.get("demo_video_path") else None
    r_imgs = [Path(p) for p in (m.get("readme_images") or []) if Path(p).is_file()]
    home = Path(m["homepage_screenshot"]) if m.get("homepage_screenshot") and Path(m["homepage_screenshot"]).is_file() else None
    gh = Path(m["github_screenshot"]) if m.get("github_screenshot") and Path(m["github_screenshot"]).is_file() else None

    if demo and demo.is_file():
        b2 = ("video", demo)
        if r_imgs:
            b3 = ("image", r_imgs[0])
        elif home:
            b3 = ("scroll_top", home)
        else:
            b3 = ("scroll_top", gh)  # type: ignore[arg-type]
        return b2, b3

    if home:
        b2 = ("scroll_top", home)
        if r_imgs:
            b3 = ("image", r_imgs[0])
        elif gh:
            b3 = ("scroll_mid", gh)
        else:
            b3 = ("scroll_mid", home)
        return b2, b3

    if r_imgs:
        b2 = ("image", r_imgs[0])
        if len(r_imgs) > 1:
            b3 = ("image", r_imgs[1])
        elif gh:
            b3 = ("scroll_mid", gh)
        else:
            b3 = ("image", r_imgs[0])
        return b2, b3

    assert gh is not None
    return ("scroll_top", gh), ("scroll_mid", gh)


def _stage_input_and_filter(
    inp_idx: int,
    kind: str,
    path: Path,
    dur: float,
    out_label: str,
    bar_label: str | None = None,
) -> tuple[list[str], str]:
    dur_str = f"{dur:.3f}"
    if kind == "card":
        n_frames = max(2, int(round(dur * 30.0)) + 2)
        inp = ["-i", str(path)]
        flt = (
            f"[{inp_idx}:v]scale=1956:1488:flags=lanczos,"
            f"zoompan=z='min(1.026,1+0.024*on/max(1,{n_frames}))':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={n_frames}:s=978x744:fps=30,"
            f"trim=duration={dur_str},format=yuv420p,setsar=1,setpts=PTS-STARTPTS[{out_label}];"
        )
        return inp, flt

    if kind == "video":
        inp = ["-stream_loop", "-1", "-t", dur_str, "-i", str(path)]
        if bar_label:
            flt = (
                f"[{inp_idx}:v]fps=30,scale=978:550:force_original_aspect_ratio=decrease,"
                f"setsar=1,pad=978:744:(ow-iw)/2:(550-ih)/2:color=0x141416,format=rgba[vid_{inp_idx}];"
                f"[{bar_label}]trim=duration={dur_str},setpts=PTS-STARTPTS[bar_{inp_idx}];"
                f"[vid_{inp_idx}][bar_{inp_idx}]overlay=0:550:format=auto:shortest=1,"
                f"trim=duration={dur_str},format=yuv420p,setsar=1,setpts=PTS-STARTPTS[{out_label}];"
            )
        else:
            flt = (
                f"[{inp_idx}:v]fps=30,scale=978:744:force_original_aspect_ratio=decrease,"
                f"setsar=1,pad=978:744:(ow-iw)/2:(oh-ih)/2:color=0xFAFAFB,"
                f"trim=duration={dur_str},format=yuv420p,setsar=1,setpts=PTS-STARTPTS[{out_label}];"
            )
        return inp, flt

    if kind == "image":
        inp = ["-loop", "1", "-t", dur_str, "-i", str(path)]
        flt = (
            f"[{inp_idx}:v]fps=30,format=rgba,split=2[bgin_{inp_idx}][fgin_{inp_idx}];"
            f"[bgin_{inp_idx}]scale=978:744:force_original_aspect_ratio=increase,"
            f"crop=978:744,format=yuv420p,boxblur=16:2,eq=brightness=-0.12,format=rgba[bgimg_{inp_idx}];"
            f"[fgin_{inp_idx}]scale=978:744:force_original_aspect_ratio=decrease[fgimg_{inp_idx}];"
            f"[bgimg_{inp_idx}][fgimg_{inp_idx}]overlay=(W-w)/2:(H-h)/2:format=auto,"
            f"trim=duration={dur_str},format=yuv420p,setsar=1,setpts=PTS-STARTPTS[{out_label}];"
        )
        return inp, flt

    # Scrollable 2x webpage / GitHub screenshot across full 978px browser viewport
    inp = ["-loop", "1", "-t", dur_str, "-i", str(path)]
    with Image.open(path) as im:
        iw, ih = im.size
    scaled_h = int(round(ih * (978.0 / iw)))
    scaled_h = scaled_h if scaled_h % 2 == 0 else scaled_h + 1
    if scaled_h > 754:
        max_scroll = scaled_h - 744
        start_y = min(max_scroll, 360) if kind == "scroll_mid" and max_scroll > 420 else 0
        span_y = max(0, max_scroll - start_y)
        flt = (
            f"[{inp_idx}:v]fps=30,scale=978:{scaled_h},"
            f"crop=978:744:0:'min({max_scroll}, max(0, {start_y}+{span_y}*t/{dur_str}))',"
            f"trim=duration={dur_str},format=yuv420p,setsar=1,setpts=PTS-STARTPTS[{out_label}];"
        )
    else:
        flt = (
            f"[{inp_idx}:v]fps=30,format=rgba,split=2[bgin_{inp_idx}][fgin_{inp_idx}];"
            f"[bgin_{inp_idx}]scale=978:744:force_original_aspect_ratio=increase,"
            f"crop=978:744,format=yuv420p,boxblur=16:2,eq=brightness=-0.12,format=rgba[bgimg_{inp_idx}];"
            f"[fgin_{inp_idx}]scale=978:744:force_original_aspect_ratio=decrease[fgimg_{inp_idx}];"
            f"[bgimg_{inp_idx}][fgimg_{inp_idx}]overlay=(W-w)/2:(H-h)/2:format=auto,"
            f"trim=duration={dur_str},format=yuv420p,setsar=1,setpts=PTS-STARTPTS[{out_label}];"
        )
    return inp, flt


def _beat_timing(duration_sec: float, f: float = 0.30) -> tuple[float, float, float, float, float, float, float]:
    total_raw = duration_sec + 3.0 * f
    d1 = round(total_raw * 0.22, 3)
    d2 = round(total_raw * 0.36, 3)
    d3 = round(total_raw * 0.22, 3)
    d4 = round(total_raw - (d1 + d2 + d3), 3)
    off1 = round(d1 - f, 3)
    off2 = round(d1 + d2 - 2.0 * f, 3)
    off3 = round(d1 + d2 + d3 - 3.0 * f, 3)
    return d1, d2, d3, d4, off1, off2, off3


def _render_segment_clip(
    seg: dict[str, Any],
    png_map: dict[str, dict[str, Path]],
    wav_path: Path,
    duration_sec: float,
    out_mp4: Path,
    work_dir: Path,
    bg_loop_mp4: Path,
    bg_start_offset: float,
) -> None:
    seg_id = seg["id"]
    dur_str = f"{duration_sec:.3f}"
    start_mod = round(bg_start_offset % 6.0, 3)
    bg_feed_dur = f"{start_mod + duration_sec + 0.50:.3f}"
    y_bounce = "if(lt(t,0.24),round(18*(1-t/0.24)*(1-t/0.24)),0)"

    if seg["kind"] in ("hook", "outro"):
        fc_ho = (
            f"[0:v]trim=start={start_mod:.3f}:duration={dur_str},setpts=PTS-STARTPTS,fps=30,format=yuv420p[bgmov];"
            f"[1:v]fps=30,format=rgba[ov];"
            f"[bgmov][ov]overlay=0:'{y_bounce}':format=auto:shortest=1,format=yuv420p[v]"
        )
        _run_ffmpeg(
            [
                "-y",
                "-stream_loop", "-1", "-t", bg_feed_dur, "-i", str(bg_loop_mp4),
                "-loop", "1", "-t", dur_str, "-i", str(png_map["segments"][seg_id]),
                "-i", str(wav_path),
                "-filter_complex", fc_ho,
                "-map", "[v]", "-map", "2:a",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
                "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "1",
                "-shortest",
                str(out_mp4),
            ],
            cwd=work_dir,
        )
        return

    f = 0.30
    d1, d2, d3, d4, off1, off2, off3 = _beat_timing(duration_sec, f=f)

    b1_png = png_map["beat1"][seg_id]
    b4_png = png_map["beat4"][seg_id]
    shell_png = png_map["segments"][seg_id]
    fg_badge_png = png_map["fg_badges"][seg_id]
    (b2_kind, b2_path), (b3_kind, b3_path) = _pick_beat2_and_beat3(seg)

    has_video = (b2_kind == "video") or (b3_kind == "video")
    bar_setup_flt = ""
    b2_bar_lbl: str | None = None
    b3_bar_lbl: str | None = None
    if has_video:
        if b2_kind == "video" and b3_kind == "video":
            bar_setup_flt = "[8:v]fps=30,format=rgba,split=2[bbar2][bbar3];"
            b2_bar_lbl, b3_bar_lbl = "bbar2", "bbar3"
        elif b2_kind == "video":
            bar_setup_flt = "[8:v]fps=30,format=rgba[bbar2];"
            b2_bar_lbl = "bbar2"
        else:
            bar_setup_flt = "[8:v]fps=30,format=rgba[bbar3];"
            b3_bar_lbl = "bbar3"

    cmd_inputs: list[str] = []
    inp1, flt1 = _stage_input_and_filter(0, "card", b1_png, d1, "st1")
    inp2, flt2 = _stage_input_and_filter(1, b2_kind, b2_path, d2, "st2", bar_label=b2_bar_lbl)
    inp3, flt3 = _stage_input_and_filter(2, b3_kind, b3_path, d3, "st3", bar_label=b3_bar_lbl)
    inp4, flt4 = _stage_input_and_filter(3, "card", b4_png, d4, "st4")

    cmd_inputs.extend(inp1 + inp2 + inp3 + inp4)
    # 4: bg_dot_wave_loop.mp4, 5: audio WAV, 6: repo_shell RGBA, 7: fg_badges RGBA, optional 8: demo_panel
    cmd_inputs.extend(
        [
            "-stream_loop", "-1", "-t", bg_feed_dur, "-i", str(bg_loop_mp4),
            "-i", str(wav_path),
            "-loop", "1", "-t", dur_str, "-i", str(shell_png),
            "-loop", "1", "-t", dur_str, "-i", str(fg_badge_png),
        ]
    )
    if has_video:
        cmd_inputs.extend(["-loop", "1", "-t", dur_str, "-i", str(png_map["demo_bar"][seg_id])])

    fc = (
        bar_setup_flt
        + flt1
        + flt2
        + flt3
        + flt4
        + f"[st1][st2]xfade=transition=smoothleft:duration={f}:offset={off1}[x12];"
        + f"[x12][st3]xfade=transition=slideleft:duration={f}:offset={off2}[x123];"
        + f"[x123][st4]xfade=transition=smoothup:duration={f}:offset={off3}[stage];"
        + f"[4:v]trim=start={start_mod:.3f}:duration={dur_str},setpts=PTS-STARTPTS,fps=30,format=yuv420p[bgmov];"
        + f"[6:v]fps=30,format=rgba[shell];"
        + f"[7:v]fps=30,format=rgba[fgb];"
        + f"[bgmov][shell]overlay=0:'{y_bounce}':format=auto:shortest=1[sh1];"
        + f"[sh1][stage]overlay=51:'321+{y_bounce}':shortest=1[mid];"
        + f"[mid][fgb]overlay=51:'321+{y_bounce}':format=auto:shortest=1,format=yuv420p[v]"
    )

    _run_ffmpeg(
        [
            "-y",
            *cmd_inputs,
            "-filter_complex", fc,
            "-map", "[v]", "-map", "5:a",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "1",
            "-shortest",
            str(out_mp4),
        ],
        cwd=work_dir,
    )


def _write_subtitles(storyboard: dict[str, Any], audio_manifest: dict[str, Any], out_dir: Path) -> tuple[Path, Path, int]:
    audio_by_id = {s["id"]: s for s in audio_manifest["segments"]}
    offset = 0.0
    all_cues: list[dict[str, Any]] = []

    for seg in storyboard["segments"]:
        a = audio_by_id[seg["id"]]
        for cue in a["cues"]:
            all_cues.append(
                {
                    "text": cue["text"],
                    "start": round(offset + float(cue["start_sec"]), 3),
                    "end": round(offset + float(cue["end_sec"]), 3),
                }
            )
        offset += float(a["duration_sec"])

    # Guarantee zero overlaps globally
    for i in range(1, len(all_cues)):
        if all_cues[i]["start"] < all_cues[i - 1]["end"]:
            all_cues[i]["start"] = all_cues[i - 1]["end"]
        if all_cues[i]["end"] <= all_cues[i]["start"]:
            all_cues[i]["end"] = round(all_cues[i]["start"] + 0.22, 3)

    date_tag = resolve_output_date_tag(storyboard)
    srt_blocks = [
        f"{idx}\n{_format_srt_ts(c['start'])} --> {_format_srt_ts(c['end'])}\n{c['text']}\n"
        for idx, c in enumerate(all_cues, start=1)
    ]
    srt_path = out_dir / f"9x16-{date_tag}.srt"
    srt_path.write_text("\n".join(srt_blocks), encoding="utf-8")

    ass_header = f"""[Script Info]
Title: GitHub Radar Editorial Subtitles V3
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{ASS_FONT_NAME},52,&H00141416,&H0000FFFF,&H00E4FCEB,&H00E4FCEB,-1,0,0,0,100,100,1,0,3,13,0,2,72,72,739,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = [
        f"Dialogue: 0,{_format_ass_ts(c['start'])},{_format_ass_ts(c['end'])},Default,,0,0,0,,{_highlight_ass_terms(c['text'])}"
        for c in all_cues
    ]
    ass_path = out_dir / f"9x16-{date_tag}.ass"
    ass_path.write_text(ass_header + "\n".join(events) + "\n", encoding="utf-8")
    return srt_path, ass_path, len(all_cues)


def _decode_audio_f32(path: Path) -> np.ndarray:
    raw = subprocess.check_output(
        [
            str(FFMPEG_EXE),
            "-v", "error",
            "-i", str(path),
            "-f", "f32le",
            "-acodec", "pcm_f32le",
            "-ac", "1",
            "-ar", "44100",
            "-",
        ],
    )
    return np.frombuffer(raw, dtype=np.float32)


def _build_sfx_track_wav(storyboard: dict[str, Any], audio_manifest: dict[str, Any], out_wav: Path) -> bool:
    sfx_dir = ASSETS_AUDIO_DIR / "sfx"
    hook_mp3 = sfx_dir / "pk-impact-hit-fast-exciting.mp3"
    swoosh_mp3 = sfx_dir / "pk-transition-wind-swoosh-short.mp3"
    pop_mp3 = sfx_dir / "pk-ui-pop.mp3"
    slide_mp3 = sfx_dir / "pk-paper-paper-slide.mp3"
    if not (hook_mp3.is_file() and swoosh_mp3.is_file() and pop_mp3.is_file() and slide_mp3.is_file()):
        return False

    sr = 44100
    total_dur = float(audio_manifest["total_duration_sec"])
    n_samples = max(sr, int(math.ceil((total_dur + 0.5) * sr)))
    buf = np.zeros(n_samples, dtype=np.float32)

    sfx_cache = {
        "hook": _decode_audio_f32(hook_mp3),
        "swoosh": _decode_audio_f32(swoosh_mp3),
        "pop": _decode_audio_f32(pop_mp3),
        "slide": _decode_audio_f32(slide_mp3),
    }

    def _place(key: str, at_sec: float, gain: float) -> None:
        arr = sfx_cache[key]
        idx0 = max(0, int(round(at_sec * sr)))
        if idx0 >= n_samples:
            return
        take = min(len(arr), n_samples - idx0)
        if take > 0:
            buf[idx0 : idx0 + take] += arr[:take] * gain

    audio_by_id = {s["id"]: s for s in audio_manifest["segments"]}
    cur_t = 0.0
    for seg in storyboard["segments"]:
        dur = float(audio_by_id[seg["id"]]["duration_sec"])
        if seg["kind"] == "hook":
            _place("hook", 0.04, 0.20)
        elif seg["kind"] == "repo":
            _, _, _, _, off1, _, off3 = _beat_timing(dur, f=0.30)
            _place("swoosh", cur_t + 0.02, 0.22)
            _place("pop", cur_t + off1, 0.16)
            _place("slide", cur_t + off3, 0.16)
        else:
            _place("swoosh", cur_t + 0.02, 0.22)
        cur_t += dur

    pcm = np.int16(np.clip(buf, -0.95, 0.95) * 32767.0)
    with wave.open(str(out_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    return True


def _pick_bgm_track(total_dur: float, out_dir: Path) -> tuple[Path, bool]:
    for cand in (
        ASSETS_AUDIO_DIR / "bgm" / "tonight-hiphop.mp3",
        ASSETS_AUDIO_DIR / "bgm" / "cat-walk.mp3",
    ):
        if cand.is_file():
            return cand, True
    synth_wav = out_dir / "audio" / "ambient_bgm.wav"
    return generate_ambient_bgm_wav(total_dur, synth_wav), False


def _append_audio_attribution(out_dir: Path, used_mixkit_bgm: bool, used_sfx: bool) -> None:
    if not (used_mixkit_bgm or used_sfx):
        return
    src_md = out_dir / "sources.md"
    if not src_md.is_file():
        return
    cur = src_md.read_text(encoding="utf-8")
    if "Mixkit Stock Music Free License" in cur:
        return
    extra = [
        "",
        "## 配乐与音效许可声明 (BGM & SFX Attribution)",
        "",
        "- BGM: *Tonight* by Michael Ramir C. (`tonight-hiphop.mp3`) — Mixkit Stock Music Free License (Royalty-Free Commercial Use).",
        "- Transition & UI SFX: `pk-impact-hit-fast-exciting.mp3`, `pk-transition-wind-swoosh-short.mp3`, `pk-ui-pop.mp3`, `pk-paper-paper-slide.mp3` (`video-shotcraft` / Mixkit Free SFX Library).",
    ]
    src_md.write_text(cur.rstrip() + "\n" + "\n".join(extra) + "\n", encoding="utf-8")


def _export_talkcraft_handoff(report: dict[str, Any], storyboard: dict[str, Any], out_dir: Path) -> Path:
    handoff = out_dir / "talkcraft_handoff"
    handoff.mkdir(parents=True, exist_ok=True)
    sentences = [
        {"i": i, "id": seg["id"], "kind": seg["kind"], "text": seg["spoken_text"]}
        for i, seg in enumerate(storyboard["segments"], start=1)
    ]
    (handoff / "script.json").write_text(
        json.dumps({"title": storyboard["video_title"], "sentences": sentences}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    for fname in ("facts.md", "sources.md", "trending_report.json", "storyboard.json"):
        src = out_dir / fname
        if src.is_file():
            shutil.copy2(src, handoff / fname)
    assets_src = out_dir / "assets"
    assets_dst = handoff / "assets"
    if assets_src.is_dir():
        if assets_dst.exists():
            shutil.rmtree(assets_dst)
        shutil.copytree(assets_src, assets_dst)
    return handoff


def render_video(
    report: dict[str, Any],
    storyboard: dict[str, Any],
    audio_manifest: dict[str, Any],
    out_dir: Path,
    enable_bgm: bool = True,
) -> dict[str, Any]:
    print("[render] Synthesizing seamless 3D animated particle dot-wave background loop...")
    bg_loop_mp4 = generate_animated_dot_wave_bg(out_dir / "frames" / "bg_dot_wave_loop.mp4")

    print("[render] Rendering Playwright transparent editorial cards & covers...")
    png_map = _render_html_pngs(storyboard, out_dir)
    audio_by_id = {s["id"]: s for s in audio_manifest["segments"]}

    parts_dir = out_dir / "parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    part_mp4s: list[Path] = []

    bg_offset = 0.0
    for seg in storyboard["segments"]:
        seg_id = seg["id"]
        a_info = audio_by_id[seg_id]
        seg_dur = float(a_info["duration_sec"])
        part_mp4 = parts_dir / f"{seg_id}.mp4"
        print(f"[render] Compositing V3 dynamic segment {seg_id} ({seg_dur:.2f}s @ bg={bg_offset:.2f}s)...")
        _render_segment_clip(
            seg=seg,
            png_map=png_map,
            wav_path=Path(a_info["wav_path"]),
            duration_sec=seg_dur,
            out_mp4=part_mp4,
            work_dir=out_dir,
            bg_loop_mp4=bg_loop_mp4,
            bg_start_offset=bg_offset,
        )
        part_mp4s.append(part_mp4)
        bg_offset += seg_dur

    concat_list = parts_dir / "concat.txt"
    concat_list.write_text("\n".join(f"file '{p.as_posix()}'" for p in part_mp4s), encoding="utf-8")

    date_tag = resolve_output_date_tag(storyboard, report)
    no_sub_mp4 = out_dir / f"9x16-{date_tag}_no_subtitles.mp4"
    total_dur = float(audio_manifest["total_duration_sec"])

    sfx_wav = out_dir / "audio" / "sfx_mix.wav"
    sfx_ok = _build_sfx_track_wav(storyboard, audio_manifest, sfx_wav)
    used_mixkit_bgm = False
    bgm_name = None

    if enable_bgm:
        bgm_src, used_mixkit_bgm = _pick_bgm_track(total_dur, out_dir)
        bgm_name = bgm_src.name
        fade_out_st = max(0.0, total_dur - 1.5)
        bgm_dur_str = f"{total_dur + 0.25:.3f}"
        if sfx_ok:
            print(f"[render] Mixing normalized voice + Mixkit BGM ({bgm_src.name}) + layered transition SFX -> {no_sub_mp4.name}...")
            _run_ffmpeg(
                [
                    "-y",
                    "-f", "concat", "-safe", "0", "-i", str(concat_list),
                    "-stream_loop", "-1", "-t", bgm_dur_str, "-i", str(bgm_src),
                    "-i", str(sfx_wav),
                    "-filter_complex",
                    "[0:a]loudnorm=I=-16:TP=-1.5:LRA=11[voice];"
                    f"[1:a]lowpass=f=3400,afade=t=in:st=0:d=0.60,afade=t=out:st={fade_out_st:.3f}:d=1.50,volume=0.095[bgm];"
                    "[2:a]volume=0.90[sfx];"
                    "[voice][bgm][sfx]amix=inputs=3:duration=first:normalize=0,alimiter=limit=0.95,aresample=44100[aout]",
                    "-map", "0:v", "-map", "[aout]",
                    "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                    "-movflags", "+faststart",
                    str(no_sub_mp4),
                ],
                cwd=out_dir,
            )
        else:
            print(f"[render] Mixing normalized voice + BGM ({bgm_src.name}) -> {no_sub_mp4.name}...")
            _run_ffmpeg(
                [
                    "-y",
                    "-f", "concat", "-safe", "0", "-i", str(concat_list),
                    "-stream_loop", "-1", "-t", bgm_dur_str, "-i", str(bgm_src),
                    "-filter_complex",
                    "[0:a]loudnorm=I=-16:TP=-1.5:LRA=11[voice];"
                    f"[1:a]lowpass=f=3400,afade=t=in:st=0:d=0.60,afade=t=out:st={fade_out_st:.3f}:d=1.50,volume=0.095[bgm];"
                    "[voice][bgm]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95,aresample=44100[aout]",
                    "-map", "0:v", "-map", "[aout]",
                    "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                    "-movflags", "+faststart",
                    str(no_sub_mp4),
                ],
                cwd=out_dir,
            )
    else:
        print(f"[render] Concatenating & normalizing audio (no BGM) -> {no_sub_mp4.name}...")
        _run_ffmpeg(
            [
                "-y",
                "-f", "concat", "-safe", "0", "-i", str(concat_list),
                "-c:v", "copy",
                "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                "-movflags", "+faststart",
                str(no_sub_mp4),
            ],
            cwd=out_dir,
        )

    srt_path, ass_path, cue_count = _write_subtitles(storyboard, audio_manifest, out_dir)
    captioned_mp4 = out_dir / f"9x16-{date_tag}.mp4"
    print(f"[render] Burning prominent bold tri-color ASS subtitles -> {captioned_mp4.name}...")
    _run_ffmpeg(
        [
            "-y",
            "-i", str(no_sub_mp4),
            "-vf", f"ass={ass_path.name}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(captioned_mp4),
        ],
        cwd=out_dir,
    )

    _append_audio_attribution(out_dir, used_mixkit_bgm=used_mixkit_bgm, used_sfx=sfx_ok)
    handoff_dir = _export_talkcraft_handoff(report, storyboard, out_dir)
    return {
        "video_main": str(captioned_mp4),
        "final_9x16_captioned": str(captioned_mp4),
        "final_9x16_clean": str(captioned_mp4),
        "final_9x16_no_subtitles": str(no_sub_mp4),
        "srt_path": str(srt_path),
        "ass_path": str(ass_path),
        "srt_cues_count": cue_count,
        "cover_9x16": str(png_map["covers"]["cover_9x16"]),
        "cover_3x4": str(png_map["covers"]["cover_3x4"]),
        "talkcraft_handoff": str(handoff_dir),
        "bgm_enabled": enable_bgm,
        "bgm_track": bgm_name,
        "sfx_enabled": sfx_ok,
    }
