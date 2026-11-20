"""DeepSeek-Chat 7-point structured scriptwriter strictly grounded in trending_report.json."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"


def _call_deepseek(prompt: str, max_retries: int = 3) -> dict[str, Any]:
    api_key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set in environment.")

    payload = {
        "model": "deepseek-chat",
        "temperature": 0.35,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是一位专业中文科技短视频导演与 GitHub 开源研究员。"
                    "必须严格依据输入的核实 JSON 数据编写解说脚本和视觉卡片信息，严禁编造任何未给出的跑分、用户数或功能。"
                    "输出必须是合法 JSON 对象。"
                ),
            },
            {"role": "user", "content": prompt},
        ],
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            with httpx.Client(timeout=60.0, trust_env=False) as client:
                resp = client.post(DEEPSEEK_API_URL, headers=headers, json=payload)
            if resp.status_code >= 500 and attempt < max_retries:
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            data = resp.json()
            return json.loads(data["choices"][0]["message"]["content"])
        except Exception as exc:
            last_err = exc
            if attempt < max_retries:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"DeepSeek script generation failed after {max_retries} attempts: {last_err}")


def generate_storyboard(report: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    period = report["period"]
    period_cn = "日榜" if period == "daily" else "周榜"
    period_span = "过去二十四小时" if period == "daily" else "过去七天"
    date_range = report.get("date_range_display") or ""
    selected = report["selected"]
    n_repos = len(selected)

    repo_facts = []
    for item in selected:
        media = item.get("media") or {}
        repo_facts.append(
            {
                "video_rank": item["video_rank"],
                "full_name": item["full_name"],
                "stars_delta": item["stars_delta"],
                "stargazers_count": item["stargazers_count"],
                "language": item["language"],
                "license": item.get("license_display") or item.get("license"),
                "has_official_demo_video": bool(media.get("demo_video_path")),
                "has_homepage": bool(media.get("homepage_screenshot")),
                "cli_snippet": item.get("cli_snippet"),
                "description": item["description"],
                "topics": item.get("topics", [])[:8],
                "readme_excerpt": item.get("readme_excerpt", "")[:1600],
            }
        )

    per_repo_chars = "80-102" if period == "daily" else "95-115"
    max_repo_len = 112 if period == "daily" else 125
    prompt = f"""
请基于以下 GitHub {period_cn}（统计周期 {date_range}）实测抓取的 {n_repos} 个高增长开源项目事实数据，策划一支 **头部科技大博主风格的 9:16 竖屏短视频口播脚本**（+15% 语速下全片控制在 {"55-82" if period == "daily" else "105-138"} 秒）。

### 核心创作原则（参考头部科技/AI博主口播质感）
1. **开头 `hook_spoken`**（24–36 字，4–5 秒）：
   - 千万不要用干巴巴的模板腔！结合本期榜首/爆款项目的真实痛点或最高星标飙升直接抛钩子（例如：“{period_span}GitHub又杀出五个爆火开源项目，最高狂飙两千多星，个个都能直接塞进你的工作流！”）。
2. **每个项目 `spoken_text`**（**严格控制在 {per_repo_chars} 字，绝不超过 {max_repo_len} 字**）：
   - 必须自然覆盖七个真实要素：**项目名称 + 真实 Star 增量/总量 + 一句话定位 + 核心能力 + 与传统方案差异 + 典型落地场景 + 适合人群与推荐度**。
   - **禁止流水账和复读机模板！** 绝不能 5 个项目全用同一句式（例如连续五遍“第X名是XX，新增YY星，总星ZZ，它是…”）。请像真人口播一样用 **多样化承上启下的自然过渡语** 把 5 个项目串成一条流畅的叙事线：
     - 第 1 个：直接切入榜首热点（如“先看今天热度炸裂的第一名…”或“排在榜首的是…”）
     - 第 2 个：顺势承接或对比（如“紧接着第二款同样很猛…”或“看完了A，第二名直接把…”）
     - 第 3 个：痛点切入（如“如果你平时也被…折磨，那第三款…”）
     - 第 4 个：递进拔高（如“说到效率，第四个项目更是直接打通了…”）
     - 第 5 个：压轴收束（如“最后压轴的第五名，专攻…”）
   - 语气像懂行的科技博主跟朋友安利好工具：**短句为主、节奏利落、口语化表达**，避免翻译腔（如把“用于实现自动化评估”改成“能自动帮你跑评测”）。
   - 数字在口播里写成中文口语读法（如「两千多星」「一千三百多星」「一万三千多星」），绝不出现带逗号的数字（如 `2,028`）；
   - 英文专业术语保留标准拼写：`GitHub`、`Python`、`Claude`、`Claude Code`、`Agent`、`MCP`、`LLM`、`SKILL.md`、`Apache 2.0`、`MIT`、`CLI`、`TypeScript`、`Go`、`React`、`OpenAI`、`Anthropic`、`.NET`；
   - 严禁把 `owner/repo` 念成“斜杠”，口播里自然称呼项目名或团队+项目名即可。
3. **结尾 `outro_spoken`**（18–28 字，4–6 秒）：
   - 干净利落收束榜单并抛互动问题（例如：“这五个开源新星，你准备先部署哪一个？评论区聊聊！”），不要冗长求三连套话。
4. **结构化视觉卡片字段（与口播互补而非照抄，必须全部基于事实 JSON）**：
   - `card_headline`：<= 16 字抓眼卖点
   - `card_tags`：2–3 个短标签（<= 7 字）
   - `one_liner`：<= 22 字一句话定位
   - `core_features`：3 条核心功能短句（每条 <= 15 字）
   - `vs_alternative`：<= 22 字相比传统方案的核心区别
   - `use_cases`：2 条典型落地场景（每条 <= 16 字）
   - `target_audience`：<= 16 字适合开发者/团队类型
   - `recommendation_verdict`：<= 12 字推荐结论（如「前端与Agent必装」「团队PR提效首选」）
5. **事实红线**：绝对不准编造未给出的数据、用户数、协议或功能。

输入事实 JSON：
{json.dumps(repo_facts, ensure_ascii=False, indent=2)}

请严格输出如下 JSON 格式：
{{
  "video_title": "<=20字中文标题",
  "cover_badge": "GitHub {period_cn}飙升 · Top {n_repos}",
  "cover_line1": "6-10字完整顺畅封面第一行",
  "cover_line2": "6-10字完整顺畅封面第二行（完整短语勿截断，如：五大AI神器值得藏）",
  "hook_spoken": "...",
  "hook_subtitle": "<=20字开头副标题",
  "repos": [
    {{
      "video_rank": 1,
      "full_name": "owner/repo",
      "card_headline": "...",
      "card_tags": ["...", "...", "..."],
      "one_liner": "...",
      "core_features": ["...", "...", "..."],
      "vs_alternative": "...",
      "use_cases": ["...", "..."],
      "target_audience": "...",
      "recommendation_verdict": "..." ,
      "spoken_text": "..."
    }}
  ],
  "outro_spoken": "..."
}}
"""
    print("[scriptwriter] Generating 7-point structured Chinese script via deepseek-chat...")
    ai_out = _call_deepseek(prompt)

    # Automatic length guard: if any repo spoken_text exceeds max_repo_len, tighten via 1 quick pass
    too_long = [r for r in ai_out.get("repos", []) if len(str(r.get("spoken_text", ""))) > max_repo_len + 10]
    if too_long or len(str(ai_out.get("hook_spoken", ""))) > 44:
        print(f"[scriptwriter] Tightening {len(too_long)} long script segment(s) to hit target 90-150s pacing...")
        tighten_prompt = (
            f"请把下面 JSON 中过长的 `spoken_text`（每条必须严格压到 {per_repo_chars} 字、绝不超 {max_repo_len} 字）"
            f"和 `hook_spoken`（<=34字）精简改写，必须保留「项目名称→Star增长→定位→功能→亮点→场景→适合人群/推荐」七要素及所有真实数字和规范英文术语，"
            f"保持相同 JSON 结构输出：\n{json.dumps(ai_out, ensure_ascii=False)}"
        )
        ai_out = _call_deepseek(tighten_prompt)

    by_rank = {int(r["video_rank"]): r for r in ai_out.get("repos", []) if "video_rank" in r}
    segments: list[dict[str, Any]] = [
        {
            "id": "seg_00_hook",
            "kind": "hook",
            "title": ai_out.get("video_title") or f"GitHub {period_cn}开源精选 Top {n_repos}",
            "subtitle": ai_out.get("hook_subtitle") or "高增长 AI 与开发者开源项目速览",
            "date_range": date_range,
            "spoken_text": str(ai_out.get("hook_spoken") or "").strip(),
            "repos_preview": [
                {
                    "rank": s["video_rank"],
                    "full_name": s["full_name"],
                    "stars_delta": s["stars_delta"],
                    "language": s["language"],
                    "avatar_path": (s.get("media") or {}).get("avatar_path"),
                    "one_liner": str((by_rank.get(s["video_rank"]) or {}).get("one_liner") or s["description"][:40]).strip(),
                    "recommendation_verdict": str((by_rank.get(s["video_rank"]) or {}).get("recommendation_verdict") or "值得Star体验").strip(),
                }
                for s in selected
            ],
        }
    ]

    for s in selected:
        rank = s["video_rank"]
        gen = by_rank.get(rank) or {}
        lic_disp = s.get("license_display") or s.get("license") or "见仓库 LICENSE"
        segments.append(
            {
                "id": f"seg_{rank:02d}_{s['full_name'].split('/')[-1]}",
                "kind": "repo",
                "rank": rank,
                "full_name": s["full_name"],
                "stars_delta": s["stars_delta"],
                "stars_delta_display": f"+{s['stars_delta']:,}",
                "stargazers_count": s["stargazers_count"],
                "stars_total_display": f"{s['stargazers_count']:,}",
                "language": s["language"],
                "license": lic_disp,
                "cli_snippet": s.get("cli_snippet"),
                "card_headline": str(gen.get("card_headline") or s["description"][:28]).strip(),
                "card_tags": list(gen.get("card_tags") or [s["language"], lic_disp])[:3],
                "one_liner": str(gen.get("one_liner") or s["description"][:40]).strip(),
                "core_features": list(gen.get("core_features") or [])[:3],
                "vs_alternative": str(gen.get("vs_alternative") or "面向现代AI与工程工作流深度优化").strip(),
                "use_cases": list(gen.get("use_cases") or [])[:2],
                "target_audience": str(gen.get("target_audience") or "AI开发者与工程团队").strip(),
                "recommendation_verdict": str(gen.get("recommendation_verdict") or "值得Star体验").strip(),
                "spoken_text": str(gen.get("spoken_text") or s["description"]).strip(),
                "source_attribution": f"github.com/{s['full_name']} · {lic_disp}",
                "media": s["media"],
            }
        )

    outro_rows = []
    for seg in segments:
        if seg["kind"] == "repo":
            outro_rows.append(
                {
                    "rank": seg["rank"],
                    "full_name": seg["full_name"],
                    "stars_delta": seg["stars_delta"],
                    "one_liner": seg["one_liner"],
                    "avatar_path": (seg.get("media") or {}).get("avatar_path"),
                    "recommendation_verdict": seg.get("recommendation_verdict", ""),
                    "url": f"github.com/{seg['full_name']}",
                }
            )

    segments.append(
        {
            "id": "seg_99_outro",
            "kind": "outro",
            "title": f"本期 GitHub {period_cn}精选排行榜",
            "date_range": date_range,
            "spoken_text": str(ai_out.get("outro_spoken") or "以上就是本期 GitHub 热门项目榜单，你最想先部署哪一个？评论区聊聊！").strip(),
            "repos_preview": outro_rows,
        }
    )

    storyboard = {
        "period": period,
        "focus": report["focus"],
        "date_range_display": date_range,
        "video_title": str(ai_out.get("video_title") or f"GitHub {period_cn}飙升榜 Top {n_repos}").strip(),
        "cover_badge": str(ai_out.get("cover_badge") or f"GitHub {period_cn}热飙 · Top {n_repos}").strip(),
        "cover_line1": str(ai_out.get("cover_line1") or f"GitHub {period_cn}热榜").strip(),
        "cover_line2": str(ai_out.get("cover_line2") or f"{n_repos}个爆火开源神器").strip(),
        "segments": segments,
    }
    (out_dir / "storyboard.json").write_text(json.dumps(storyboard, indent=2, ensure_ascii=False), encoding="utf-8")

    voice_dir = out_dir / "voice_override"
    voice_dir.mkdir(parents=True, exist_ok=True)
    runner_path = Path(__file__).resolve().parent / "run_github_radar.py"
    sheet = [
        "# 真人配音替换对照表 (Human Voice Recording Sheet)",
        "",
        "如需替换为你的真人配音，请按下方文件名录音（`.wav` / `.mp3` / `.m4a`），放入：",
        f"`{voice_dir}`",
        "",
        "支持只替换部分分段，完成后运行：",
        "```powershell",
        f'python "{runner_path}" --stage render --out-dir "{out_dir}" --voice-dir "{voice_dir}"',
        "```",
        "",
        "| 分段 ID | 类型 | 建议文件名 | 最终口播文案（配音与字幕唯一来源） |",
        "|---|---|---|---|",
    ]
    for seg in segments:
        sheet.append(f"| `{seg['id']}` | {seg['kind']} | `{seg['id']}.wav` | {seg['spoken_text']} |")
    (out_dir / "voice_recording_sheet.md").write_text("\n".join(sheet), encoding="utf-8")
    return storyboard
