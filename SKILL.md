---
name: github-daily-radar
description: >-
  Automated GitHub Daily/Weekly Trending 9:16 tech short-video generator (V3).
  Use when the user asks to create a daily or weekly GitHub trending open-source
  video ("做一期 GitHub 每日/每周热榜视频", "GitHub 每日一集", "抓今天 GitHub 热门项目做短视频"),
  re-render with human voice recordings, run 12-check video QA, or generate matching
  publish copy and pinned GitHub repo comment links.
---

# GitHub Daily Radar (V3)

Automated end-to-end pipeline that turns today's or this week's live **GitHub Trending Top 10** into a **9:16 (1080×1920, 30FPS)** Chinese tech short video with real project screenshots/demos, WordBoundary-synced dynamic subtitles, animated 3D particle dot-wave background, Mixkit BGM/SFX mixing, and 12-check automated QA.

For complete architectural details and real V3 benchmark numbers, see [WORKFLOW_SUMMARY.md](./WORKFLOW_SUMMARY.md).

## Prerequisites

1. **Environment variables** (configure in `.env` or shell environment; never commit real keys):
   - `DEEPSEEK_API_KEY` (required for `deepseek-chat` script generation)
   - `GITHUB_TOKEN` (optional; used automatically if `gh` CLI is not installed/authenticated)
   - `DASHSCOPE_API_KEY` (optional; only needed if using `--tts dashscope`)
   - Optional overrides: `FFMPEG_EXE`, `FFPROBE_EXE`, `GITHUB_RADAR_OUT_ROOT`, `GITHUB_RADAR_ASS_FONT`
2. **System binaries**: `ffmpeg` + `ffprobe` (in `PATH`, `FFMPEG_EXE`/`FFPROBE_EXE`, or `./tools/ffmpeg/`).
3. **Python deps**: `pip install -r requirements.txt` (and `playwright install chromium` if local Chrome is absent).

## Core Commands

Run commands using relative paths from the skill root (or pass `--out-dir` explicitly):

### 1. Daily Video (New Episode · Recommended)

```bash
python -u ./scripts/run_github_radar.py --period daily --top-n 5 --focus ai-dev --dedupe-days 7
```

- Use `--top-n 3` for a concise 45–75s daily bulletin, or `--top-n 5` for a richer 90–145s deep-dive.
- `--dedupe-days 7` softly avoids repeating repos featured in `output/history/trending_snapshots.jsonl` over the last 7 days.

### 2. Weekly Roundup Video

```bash
python -u ./scripts/run_github_radar.py --period weekly --top-n 5 --focus ai-dev
```

### 3. Human Voice Swap & Fast Re-render

When the user records `.wav`/`.mp3` files matching segment IDs in `voice_recording_sheet.md` inside `<out_dir>/voice_override/`:

```bash
python -u ./scripts/run_github_radar.py --stage render --out-dir "<out_dir>" --voice-dir "<out_dir>/voice_override"
```

### 4. Local n8n Automation Server (`:8010`)

```bash
python -u ./scripts/server.py
```

Import `./references/n8n-github-trending-pipeline.json` into n8n (`Schedule Trigger` + `Form Trigger` + `Human Review Gate`).

## Mandatory Verification Gate (Before Presenting Output)

Never declare a rendered video ready solely from exit codes:

1. Inspect `<out_dir>/qa_report.json` and confirm `"all_passed": true` across all 12 checks (`resolution_1080x1920`, `av_sync_under_0_35s`, `no_black_frames`, `no_long_silence`, `audible_mean_volume`, `srt_zero_overlaps`, `srt_no_broken_tokens`, `srt_exact_text_match`, `facts_consistent`, `no_noassertion_leak`, `clean_mp4_matches_captioned`, `codecs_h264_aac`).
2. Inspect all generated preview frames (`<out_dir>/qa_frames/frame_01_hook.png` through `frame_07_outro_ranking.png`, `<out_dir>/cover_9x16.png`, and `<out_dir>/cover_3x4.png`) using `read_file` to verify:
   - Dynamic top/bottom dot-wave margin layout frames cleanly around the central white card.
   - Real repo screenshots/videos fit without clipping or overlaid UI clutter.
   - Large mint-pill subtitles are centered in the bottom card subtitle slot with clean line breaks and zero mid-word tears.
3. Consult [./references/qa-and-troubleshooting.md](./references/qa-and-troubleshooting.md) if any QA metric fails.

## Post-Render Delivery Checklist

After QA and visual review pass:

1. Deliver `<out_dir>/final_9x16_clean.mp4` (and note `<out_dir>/final_9x16_no_subtitles.mp4`, `final_subtitles.srt`, `cover_9x16.png`, `cover_3x4.png`). If the user asked for the file in a specific workspace directory, copy it there and keep `<out_dir>` intact for traceability.
2. Read `<out_dir>/facts.md` and `<out_dir>/trending_report.json`, then generate:
   - **Short video publish copy** (concise platform caption + tags)
   - **Separated pinned-comment GitHub links block** (exact `#1`–`#N` `https://github.com/<owner>/<repo>` URLs strictly copied from `facts.md`)
   - Follow [./references/publish-templates.md](./references/publish-templates.md).

## Hard Safety & Honesty Guardrails

- **MANUAL_ONLY_NO_AUTO_PUBLISH**: Never upload or publish videos automatically to social platforms; always stop at `PENDING_HUMAN_REVIEW`.
- **Zero Fabrication**: Never invent star counts, deltas, licenses, benchmarks, or fake UI screenshots. Only cite facts recorded in `trending_report.json` / `facts.md`.
- **Exact Voice–Subtitle Match**: Spoken narration and burned-in ASS/SRT subtitles must always share the same finalized `storyboard.json`.
