# 12 项自动质检规格与常见工程故障排查

## 一、`qa_audit.py` 12 项自动检查清单

| # | 检查字段 (`qa_report.json`) | 判定阈值 | 设计目的 |
|---|---|---|---|
| 1 | `resolution_1080x1920` | `width == 1080 and height == 1920` | 确保 9:16 标准竖屏成片 |
| 2 | `codecs_h264_aac` | `video=h264` & `audio=aac` | 确保跨平台剪映/手机/社媒秒上传兼容 |
| 3 | `av_sync_under_0_35s` | `abs(v_dur - a_dur) < 0.35s` | 防止音画逐渐漂移 |
| 4 | `no_black_frames` | `blackdetect(d=0.6, pix_th=0.10) == 0` | 防止转场失误或黑场帧 |
| 5 | `no_long_silence` | `silencedetect(-40dB, d=1.5) == 0` | 防止漏音、TTS 空文件或音频截断 |
| 6 | `audible_mean_volume` | `-28.0 <= mean_volume_db <= -8.0` | 防止声画过轻（如 `amix` 未设 `normalize=0`）或爆音 |
| 7 | `srt_zero_overlaps` | 相邻字幕 `start >= prev_end` | 防止双行叠加打架 |
| 8 | `srt_no_broken_tokens` | 无尾随孤立 `.` 或截断版本号 `v2.` | 防止英文/版本号被句点错误拆行 |
| 9 | `srt_exact_text_match` | 归一化文本匹配度 `== 1.0`（最低 `0.99`） | 防止语音与字幕各自用两套文案 |
| 10 | `facts_consistent` | 全部 repo `stars_delta` 与 `stargazers_count` 全等 | 防止 AI 幻觉改写数据 |
| 11 | `no_noassertion_leak` | `0` 处 `NOASSERTION` 出现在卡片/归属字段 | 避免直接把 GitHub 原始占位符渲染上屏 |
| 12 | `clean_mp4_matches_captioned` | 字节数与流参数完全一致 | 保证主交付文件 `9x16-YYYYMMDD.mp4` 与规格检查一致性 |

---

## 二、常见故障与排查

1. **`DEEPSEEK_API_KEY is not set`**
   - 检查项目根目录 `.env` 或当前终端环境变量是否配置；脚本会自动加载 `.env` 但不会覆盖已有的 Shell 环境变量。
2. **`ffmpeg` / `ffprobe` 找不到**
   - 在 `.env` 设置 `FFMPEG_EXE` 与 `FFPROBE_EXE` 的绝对路径，或将 FFmpeg `bin` 目录加入系统 `PATH`。
3. **Playwright 启动报错找不到 `chrome` 渠道**
   - `config.launch_chromium` 已内置降级逻辑（先尝试系统 Chrome，失败自动回退 Playwright 自带 Chromium）；如仍未安装内核，执行 `playwright install chromium` 即可。
4. **`gh api` 报错未登录或未安装 `gh` CLI**
   - `fetcher.py` 会在 `gh api` 失败后自动降级请求公开的 GitHub REST API；如遇高频匿名限流（60 req/hr），在 `.env` 配置 `GITHUB_TOKEN` 即可提升至 5000 req/hr。
5. **Windows 下代理干扰本地 `:8010` 或 API 请求**
   - `fetcher.py` 已显式启用 `trust_env=False` 直连；若网络必须走公司/本地代理才能访问 GitHub，可检查本机网络连通性。
6. **Linux 下字幕方块乱码**
   - Linux 默认选用 `Noto Sans CJK SC`，请确认系统已安装 `fonts-noto-cjk`（Ubuntu/Debian：`sudo apt install fonts-noto-cjk`），或在 `.env` 用 `GITHUB_RADAR_ASS_FONT` 指定已安装的中文字体名。
