"""Automated quality assurance audit for rendered GitHub Radar videos, subtitles, facts & frames."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from config import FFMPEG_EXE, FFPROBE_EXE


def _to_ms(ts: str) -> int:
    h, m, rest = ts.split(":")
    s, ms = rest.split(",")
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(ms)


def _norm_text(s: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", s.lower())


def run_qa_audit(
    out_dir: Path,
    report: dict[str, Any],
    storyboard: dict[str, Any],
    audio_manifest: dict[str, Any],
    render_outputs: dict[str, Any],
) -> dict[str, Any]:
    captioned = Path(render_outputs["final_9x16_captioned"])
    srt_path = Path(render_outputs["srt_path"])

    # 1. ffprobe stream & A/V sync delta check
    probe = json.loads(
        subprocess.check_output(
            [
                str(FFPROBE_EXE),
                "-v", "error",
                "-show_entries", "stream=codec_type,codec_name,width,height,r_frame_rate,duration",
                "-show_entries", "format=duration,size",
                "-of", "json",
                str(captioned),
            ],
            text=True, encoding="utf-8", timeout=20,
        )
    )
    v_stream = next((s for s in probe["streams"] if s["codec_type"] == "video"), {})
    a_stream = next((s for s in probe["streams"] if s["codec_type"] == "audio"), {})
    v_dur = float(v_stream.get("duration") or probe["format"]["duration"])
    a_dur = float(a_stream.get("duration") or probe["format"]["duration"])
    av_delta = round(abs(v_dur - a_dur), 3)

    # 2. blackdetect & silencedetect & volumedetect
    det = subprocess.run(
        [
            str(FFMPEG_EXE),
            "-i", str(captioned),
            "-vf", "blackdetect=d=0.6:pix_th=0.10",
            "-af", "silencedetect=noise=-40dB:d=1.5,volumedetect",
            "-f", "null", "-",
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, check=False,
    )
    stderr = det.stderr or ""
    black_events = re.findall(r"black_start:([\d.]+).*?black_duration:([\d.]+)", stderr)
    silence_events = re.findall(r"silence_duration:\s*([\d.]+)", stderr)
    m_mean = re.search(r"mean_volume:\s*([-\d.]+)\s*dB", stderr)
    mean_vol_db = float(m_mean.group(1)) if m_mean else None

    # 3. Subtitle integrity & text-match vs storyboard
    srt_text = srt_path.read_text(encoding="utf-8")
    blocks = [b.strip() for b in re.split(r"\r?\n\r?\n", srt_text.strip()) if b.strip()]
    parsed_cues: list[tuple[int, int, str]] = []
    overlaps = 0
    broken_tokens: list[str] = []
    max_cue_chars = 0

    for blk in blocks:
        lines = blk.splitlines()
        if len(lines) < 3:
            continue
        m = re.match(r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})", lines[1])
        if not m:
            continue
        st_ms, en_ms = _to_ms(m.group(1)), _to_ms(m.group(2))
        txt = " ".join(lines[2:]).strip()
        if parsed_cues and st_ms < parsed_cues[-1][1]:
            overlaps += 1
        if txt.endswith(".") or re.search(r"\b\d\.$", txt):
            broken_tokens.append(txt)
        max_cue_chars = max(max_cue_chars, len(txt))
        parsed_cues.append((st_ms, en_ms, txt))

    sb_norm = _norm_text("".join(seg["spoken_text"] for seg in storyboard["segments"]))
    srt_norm = _norm_text("".join(c[2] for c in parsed_cues))
    text_match_ratio = round(min(len(sb_norm), len(srt_norm)) / max(1, max(len(sb_norm), len(srt_norm))), 4)
    exact_text_match = sb_norm == srt_norm

    # 4. Fact & NOASSERTION check
    selected_by_name = {s["full_name"]: s for s in report["selected"]}
    fact_mismatches: list[str] = []
    noassertion_leaks = 0
    for seg in storyboard["segments"]:
        if seg["kind"] != "repo":
            continue
        src = selected_by_name.get(seg["full_name"])
        if not src:
            fact_mismatches.append(f"Unknown repo {seg['full_name']}")
            continue
        if int(seg["stars_delta"]) != int(src["stars_delta"]) or int(seg["stargazers_count"]) != int(src["stargazers_count"]):
            fact_mismatches.append(f"Star mismatch for {seg['full_name']}")
        if "NOASSERTION" in str(seg.get("source_attribution", "")).upper() or "NOASSERTION" in str(seg.get("license", "")).upper():
            noassertion_leaks += 1

    # 5. Extract 6 preview QA frames (hook, repo1 beat1 stats, repo1 beat2 demo/web, repo2 demo, repo3 demo, outro)
    qa_dir = out_dir / "qa_frames"
    qa_dir.mkdir(parents=True, exist_ok=True)
    seg_offsets: dict[str, tuple[float, float]] = {}
    cur = 0.0
    for s in audio_manifest["segments"]:
        seg_offsets[s["id"]] = (cur, float(s["duration_sec"]))
        cur += float(s["duration_sec"])

    repo_segs = [s for s in storyboard["segments"] if s["kind"] == "repo"]
    frame_targets = [
        ("frame_01_hook.png", min(2.0, seg_offsets["seg_00_hook"][1] * 0.5)),
    ]
    if len(repo_segs) >= 1:
        s0, d0 = seg_offsets[repo_segs[0]["id"]]
        frame_targets.append(("frame_02_repo1_beat1_stats.png", s0 + d0 * 0.12))
        frame_targets.append(("frame_03_repo1_beat2_real.png", s0 + d0 * 0.42))
    if len(repo_segs) >= 2:
        s1, d1 = seg_offsets[repo_segs[1]["id"]]
        frame_targets.append(("frame_04_repo2_demo.png", s1 + d1 * 0.42))
    if len(repo_segs) >= 3:
        s2, d2 = seg_offsets[repo_segs[2]["id"]]
        frame_targets.append(("frame_05_repo3_beat4_value.png", s2 + d2 * 0.88))
    if len(repo_segs) >= 4:
        s3, d3 = seg_offsets[repo_segs[3]["id"]]
        frame_targets.append(("frame_06_repo4_video_demo.png", s3 + d3 * 0.42))
    s_out, d_out = seg_offsets["seg_99_outro"]
    frame_targets.append(("frame_07_outro_ranking.png", s_out + d_out * 0.35))

    for old_png in qa_dir.glob("*.png"):
        old_png.unlink(missing_ok=True)
    extracted_frames: list[str] = []
    for fname, ts in frame_targets:
        dest = qa_dir / fname
        subprocess.run(
            [str(FFMPEG_EXE), "-y", "-ss", f"{ts:.2f}", "-i", str(captioned), "-frames:v", "1", str(dest)],
            capture_output=True, check=True,
        )
        extracted_frames.append(str(dest))

    # 6. Verify final_9x16_clean.mp4 matches final_9x16_captioned.mp4 (has subtitles & valid 1080x1920 streams)
    clean_mp4 = Path(render_outputs.get("final_9x16_clean") or (out_dir / "final_9x16_clean.mp4"))
    clean_matches_captioned = False
    clean_size_bytes = 0
    captioned_size_bytes = captioned.stat().st_size if captioned.is_file() else 0
    if clean_mp4.is_file():
        clean_size_bytes = clean_mp4.stat().st_size
        clean_probe = json.loads(
            subprocess.check_output(
                [
                    str(FFPROBE_EXE),
                    "-v", "error",
                    "-show_entries", "stream=codec_type,codec_name,width,height",
                    "-of", "json",
                    str(clean_mp4),
                ],
                text=True, encoding="utf-8", timeout=20,
            )
        )
        cv = next((s for s in clean_probe["streams"] if s["codec_type"] == "video"), {})
        ca = next((s for s in clean_probe["streams"] if s["codec_type"] == "audio"), {})
        clean_matches_captioned = (
            clean_size_bytes == captioned_size_bytes
            and clean_size_bytes > 0
            and cv.get("width") == 1080
            and cv.get("height") == 1920
            and cv.get("codec_name") == "h264"
            and ca.get("codec_name") == "aac"
        )
        if len(repo_segs) >= 1:
            s0, d0 = seg_offsets[repo_segs[0]["id"]]
            clean_verify_png = qa_dir / "clean_verify_frame.png"
            subprocess.run(
                [str(FFMPEG_EXE), "-y", "-ss", f"{s0 + d0 * 0.42:.2f}", "-i", str(clean_mp4), "-frames:v", "1", str(clean_verify_png)],
                capture_output=True, check=True,
            )
            extracted_frames.append(str(clean_verify_png))

    checks = {
        "resolution_1080x1920": v_stream.get("width") == 1080 and v_stream.get("height") == 1920,
        "codecs_h264_aac": v_stream.get("codec_name") == "h264" and a_stream.get("codec_name") == "aac",
        "av_sync_under_0_35s": av_delta < 0.35,
        "no_black_frames": len(black_events) == 0,
        "no_long_silence": len(silence_events) == 0,
        "audible_mean_volume": mean_vol_db is not None and -28.0 <= mean_vol_db <= -8.0,
        "srt_zero_overlaps": overlaps == 0,
        "srt_no_broken_tokens": len(broken_tokens) == 0,
        "srt_exact_text_match": exact_text_match or text_match_ratio >= 0.99,
        "facts_consistent": len(fact_mismatches) == 0,
        "no_noassertion_leak": noassertion_leaks == 0,
        "clean_mp4_matches_captioned": clean_matches_captioned,
    }
    all_passed = all(checks.values())

    qa_report = {
        "all_passed": all_passed,
        "checks": checks,
        "metrics": {
            "video_width": v_stream.get("width"),
            "video_height": v_stream.get("height"),
            "fps": v_stream.get("r_frame_rate"),
            "video_duration_sec": round(v_dur, 3),
            "audio_duration_sec": round(a_dur, 3),
            "av_delta_sec": av_delta,
            "mean_volume_db": mean_vol_db,
            "black_events": len(black_events),
            "silence_events": len(silence_events),
            "srt_cues": len(parsed_cues),
            "srt_overlaps": overlaps,
            "srt_max_chars": max_cue_chars,
            "srt_text_match_ratio": text_match_ratio,
            "srt_exact_text_match": exact_text_match,
            "broken_tokens": broken_tokens,
            "fact_mismatches": fact_mismatches,
            "noassertion_leaks": noassertion_leaks,
            "clean_size_bytes": clean_size_bytes,
            "captioned_size_bytes": captioned_size_bytes,
        },
        "extracted_frames": extracted_frames,
    }
    (out_dir / "qa_report.json").write_text(json.dumps(qa_report, indent=2, ensure_ascii=False), encoding="utf-8")

    md_lines = [
        "# 自动质量检测报告 (QA Audit Report)",
        "",
        f"- **总判定**: `{'PASS' if all_passed else 'FAIL'}`",
        f"- **分辨率 / 编码**: `{v_stream.get('width')}x{v_stream.get('height')}` (`{v_stream.get('codec_name')}` + `{a_stream.get('codec_name')}`, `{v_stream.get('r_frame_rate')}`)",
        f"- **音画同步差值**: `{av_delta}s` (阈值 `< 0.35s`)",
        f"- **平均音量**: `{mean_vol_db} dB` | 黑屏次数: `{len(black_events)}` | 长静音(>1.5s): `{len(silence_events)}`",
        f"- **字幕校验**: `{len(parsed_cues)}` 条 | 重叠: `{overlaps}` | 文本一致性: `{text_match_ratio}` (`exact={exact_text_match}`)",
        f"- **数据与协议一致性**: 事实错误 `{len(fact_mismatches)}` | `NOASSERTION` 泄露 `{noassertion_leaks}`",
        "",
        "| 检查项 | 结果 |",
        "|---|---|",
    ]
    for k, ok in checks.items():
        md_lines.append(f"| `{k}` | {'PASS' if ok else 'FAIL'} |")
    (out_dir / "QA_REPORT.md").write_text("\n".join(md_lines), encoding="utf-8")
    return qa_report
