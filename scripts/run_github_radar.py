"""Unified CLI orchestrator for the GitHub Daily/Weekly Top 10 -> 9:16 Video Radar pipeline (V3)."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from config import DEFAULT_OUT_ROOT  # noqa: E402
from fetcher import run_fetch  # noqa: E402
from qa_audit import run_qa_audit  # noqa: E402
from renderer import render_video  # noqa: E402
from scriptwriter import generate_storyboard  # noqa: E402
from tts_engine import (  # noqa: E402
    DEFAULT_EDGE_RATE,
    DEFAULT_EDGE_VOICE,
    probe_duration,
    synthesize_storyboard_audio,
)


def write_review_checklist(
    manifest: dict[str, Any],
    report: dict[str, Any],
    storyboard: dict[str, Any],
    qa_report: dict[str, Any],
    out_dir: Path,
) -> Path:
    lines = [
        "# GitHub 趋势榜成片人工审核清单 (Human Review Checklist V3)",
        "",
        "> 合规与发布原则：**仅人工审核后手动发布，本工作流绝不自动发布。**",
        "",
        f"- **榜单周期**: `{manifest['period']}` ({manifest.get('date_range_display', '')}) | **赛道策略**: `{manifest['focus']}`",
        f"- **成片标题**: {storyboard['video_title']}",
        f"- **成片时长**: `{manifest['video_duration_sec']}s` (`1080x1920`, 9:16 竖屏, 语速 `{manifest['audio'].get('rate', '+15%')}`)",
        f"- **字幕条数**: `{manifest['outputs']['srt_cues_count']}` 条 | **QA 自动质检**: `{'PASS' if qa_report.get('all_passed') else 'FAIL'}`",
        f"- **状态**: `{manifest['status']}` (`{manifest['publish_status']}`)",
        "",
        "## 交付文件一览",
        "",
        f"- 带烧录高亮字幕成片 (9:16 交付主文件): [{Path(manifest['outputs']['final_9x16_clean']).name}](./{Path(manifest['outputs']['final_9x16_clean']).name})",
        f"- 无字幕母版 (9:16, 适合剪映二次精修): [{Path(manifest['outputs']['final_9x16_no_subtitles']).name}](./{Path(manifest['outputs']['final_9x16_no_subtitles']).name})",
        f"- SRT 外挂字幕: [{Path(manifest['outputs']['srt_path']).name}](./{Path(manifest['outputs']['srt_path']).name})",
        f"- 竖屏封面 (9:16): [{Path(manifest['outputs']['cover_9x16']).name}](./{Path(manifest['outputs']['cover_9x16']).name})",
        f"- 小红书/图文封面 (3:4): [{Path(manifest['outputs']['cover_3x4']).name}](./{Path(manifest['outputs']['cover_3x4']).name})",
        f"- QA 质检报告: [QA_REPORT.md](./QA_REPORT.md) · [qa_report.json](./qa_report.json)",
        f"- 事实与出处台账: [facts.md](./facts.md) · [sources.md](./sources.md)",
        f"- 真人配音替换表: [voice_recording_sheet.md](./voice_recording_sheet.md)",
        f"- `video-talkcraft` 精品精制素材包: `{manifest['outputs']['talkcraft_handoff']}`",
        "",
        "## 精选讲解项目（从 Top 10 中精选）",
        "",
        "| 成片顺位 | 原榜排名 | 仓库名 | 增量 Star | 总 Star | 协议 | 主素材类型 | 官方Demo | README图 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in report["selected"]:
        m = s["media"]
        lines.append(
            f"| #{s['video_rank']} | #{s['trending_rank']} | {s['full_name']} | +{s['stars_delta']:,} | "
            f"{s['stargazers_count']:,} | {s.get('license_display') or s['license']} | `{m['primary_type']}` | "
            f"{'是' if m.get('demo_video_path') else '否'} | {len(m.get('readme_images') or [])}张 |"
        )

    lines.extend(
        [
            "",
            "## 完整 GitHub Top 10 归档",
            "",
            "| 原榜 | 仓库名 | 增量 Star | 语言 | 协议 | 综合筛选分 |",
            "|---|---|---|---|---|---|",
        ]
    )
    for r in report["raw_top10"]:
        lines.append(
            f"| #{r['trending_rank']} | {r['full_name']} | +{r['stars_delta']:,} | {r['language']} | "
            f"{r.get('license_display') or r['license']} | {r['selection_score']} |"
        )

    lines.extend(
        [
            "",
            "## 替换为真人录音并秒级重渲",
            "",
            "1. 对照 [voice_recording_sheet.md](./voice_recording_sheet.md) 录制对应分段的 `.wav` / `.mp3` 文件放入 `voice_override/` 目录（支持只替换其中几段）。",
            "2. 运行下方命令跳过抓取直接重合成片：",
            "```powershell",
            f'python "{Path(__file__).resolve()}" --stage render --out-dir "{out_dir}" --voice-dir "{out_dir / "voice_override"}"',
            "```",
            "",
            "## 人工发布前核查项",
            "- [ ] 核对口播稿中的 Star 数、开源协议与项目核心定位无误",
            "- [ ] 检查 9:16 舞台窗口 4 段动态画面切换自然、重点英文术语高亮清晰",
            "- [ ] 试听配音语速、背景音乐音量平衡及字幕时间轴同步",
            "- [ ] 确认保留 `github.com/<owner>/<repo>` 出处署名后，手动上传至抖音/小红书/视频号/B站",
        ]
    )

    p = out_dir / "REVIEW_CHECKLIST.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def main() -> int:
    parser = argparse.ArgumentParser(description="GitHub Trending Top 10 -> 9:16 Vertical Short Video Pipeline (V3)")
    parser.add_argument("--period", choices=("daily", "weekly"), default="weekly", help="Trending period")
    parser.add_argument("--top-n", type=int, default=5, help="Number of top projects to spotlight (default: 5)")
    parser.add_argument("--focus", choices=("ai-dev", "all", "ai-only"), default="ai-dev", help="Selection focus")
    parser.add_argument("--tts", choices=("edge", "dashscope"), default="edge", help="Default TTS engine")
    parser.add_argument("--voice", default=DEFAULT_EDGE_VOICE, help="Edge-TTS voice name")
    parser.add_argument("--rate", default=DEFAULT_EDGE_RATE, help="Edge-TTS speaking rate (default: +15%%)")
    parser.add_argument("--no-bgm", dest="enable_bgm", action="store_false", help="Disable ambient background music")
    parser.set_defaults(enable_bgm=True)
    parser.add_argument("--dedupe-days", type=int, default=0, help="Softly deprioritize repos featured within N days")
    parser.add_argument("--voice-dir", help="Optional directory containing human voice recordings")
    parser.add_argument("--stage", choices=("all", "fetch", "script", "render"), default="all", help="Pipeline stage(s)")
    parser.add_argument("--out-dir", help="Output run directory")
    parser.add_argument("--deliver-dir", help="Optional directory to copy final 9x16-YYYYMMDD deliverables into")

    args = parser.parse_args()
    top_n = max(1, min(10, args.top_n))
    out_dir = Path(args.out_dir) if args.out_dir else (
        DEFAULT_OUT_ROOT / f"github_radar_{args.period}_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    voice_dir = Path(args.voice_dir) if args.voice_dir else None

    started = time.monotonic()

    if args.stage in ("all", "fetch"):
        report = run_fetch(
            period=args.period,
            top_n=top_n,
            focus=args.focus,
            out_dir=out_dir,
            dedupe_days=args.dedupe_days,
        )
    else:
        report_path = out_dir / "trending_report.json"
        if not report_path.is_file():
            raise FileNotFoundError(f"Missing {report_path}; run fetch stage first.")
        report = json.loads(report_path.read_text(encoding="utf-8"))

    if args.stage == "fetch":
        print(f"[done] Fetch stage complete -> {out_dir}")
        return 0

    if args.stage in ("all", "script"):
        storyboard = generate_storyboard(report, out_dir)
    else:
        sb_path = out_dir / "storyboard.json"
        if not sb_path.is_file():
            raise FileNotFoundError(f"Missing {sb_path}; run script stage first.")
        storyboard = json.loads(sb_path.read_text(encoding="utf-8"))

    if args.stage == "script":
        print(f"[done] Script stage complete -> {out_dir / 'storyboard.json'}")
        return 0

    audio_manifest = synthesize_storyboard_audio(
        storyboard=storyboard,
        out_dir=out_dir,
        tts_engine=args.tts,
        edge_voice=args.voice,
        edge_rate=args.rate,
        voice_dir=voice_dir,
    )
    render_outputs = render_video(
        report=report,
        storyboard=storyboard,
        audio_manifest=audio_manifest,
        out_dir=out_dir,
        enable_bgm=args.enable_bgm,
    )
    qa_report = run_qa_audit(
        out_dir=out_dir,
        report=report,
        storyboard=storyboard,
        audio_manifest=audio_manifest,
        render_outputs=render_outputs,
    )
    final_dur = round(probe_duration(Path(render_outputs["final_9x16_captioned"])), 2)
    elapsed = round(time.monotonic() - started, 2)

    manifest = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "elapsed_sec": elapsed,
        "period": report["period"],
        "date_range_display": report.get("date_range_display", ""),
        "focus": report["focus"],
        "top_n": len(report["selected"]),
        "video_title": storyboard["video_title"],
        "video_duration_sec": final_dur,
        "status": "PENDING_HUMAN_REVIEW",
        "publish_status": "MANUAL_ONLY_NO_AUTO_PUBLISH",
        "qa_passed": qa_report["all_passed"],
        "selected_repos": [
            {
                "video_rank": s["video_rank"],
                "trending_rank": s["trending_rank"],
                "full_name": s["full_name"],
                "stars_delta": s["stars_delta"],
                "stargazers_count": s["stargazers_count"],
                "license": s.get("license_display") or s["license"],
                "primary_media_type": s["media"]["primary_type"],
            }
            for s in report["selected"]
        ],
        "outputs": render_outputs,
        "audio": {
            "rate": args.rate,
            "total_duration_sec": audio_manifest["total_duration_sec"],
            "engines": [s["engine"] for s in audio_manifest["segments"]],
        },
    }
    (out_dir / "radar_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    checklist_path = write_review_checklist(manifest, report, storyboard, qa_report, out_dir)

    if args.deliver_dir:
        deliver_dir = Path(args.deliver_dir)
        deliver_dir.mkdir(parents=True, exist_ok=True)
        for key in ("final_9x16_clean", "final_9x16_no_subtitles", "srt_path", "cover_9x16", "cover_3x4"):
            src_file = Path(render_outputs[key])
            if src_file.is_file():
                shutil.copy2(src_file, deliver_dir / src_file.name)
        print(f"  Delivered To  : {deliver_dir}")

    print(f"\n[done] GitHub Radar V3 completed in {elapsed}s (video={final_dur}s, qa_passed={qa_report['all_passed']})")
    print(f"  Main MP4      : {render_outputs['final_9x16_clean']}")
    print(f"  No-Sub MP4    : {render_outputs['final_9x16_no_subtitles']}")
    print(f"  SRT           : {render_outputs['srt_path']}")
    print(f"  Covers        : {render_outputs['cover_9x16']} | {render_outputs['cover_3x4']}")
    print(f"  QA Report     : {out_dir / 'QA_REPORT.md'}")
    print(f"  Checklist     : {checklist_path}")
    return 0 if qa_report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
