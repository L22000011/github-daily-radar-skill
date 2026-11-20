"""GitHub Trending scraper, gh API enricher, historical tracker, and 4-tier real media collector."""
from __future__ import annotations

import base64
import datetime as dt
import io
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from PIL import Image
from playwright.sync_api import sync_playwright

import history
from config import FFPROBE_EXE, launch_chromium

AI_DEV_KEYWORDS = (
    "ai",
    "llm",
    "agent",
    "agents",
    "agentic",
    "mcp",
    "claude",
    "codex",
    "cursor",
    "copilot",
    "openai",
    "gemini",
    "anthropic",
    "rag",
    "workflow",
    "cli",
    "devtools",
    "developer",
    "code",
    "review",
    "sdk",
    "framework",
    "automation",
    "prompt",
    "skill",
    "skills",
    "plugin",
    "plugins",
    "terminal",
    "ide",
    "design system",
)

SKIP_HOMEPAGE_HOSTS = (
    "github.com",
    "twitter.com",
    "x.com",
    "discord.gg",
    "discord.com",
    "t.me",
    "youtube.com",
    "youtu.be",
)

SKIP_IMG_SUBSTRINGS = (
    "shields.io",
    "badge",
    "codecov",
    "star-history",
    "coveralls",
    "favicon",
    "sponsors",
    "opencollective",
    "buymeacoffee",
    "ko-fi",
    "img.shields",
    "badgen.net",
    "travis-ci",
    "github.com/actions",
    "workflows/",
    "skills.sh/b/",
)


def _slugify(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "_", value).strip("_") or "repo"


def _parse_int(text: str) -> int:
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else 0


def _gh_api_json(endpoint: str) -> dict[str, Any]:
    if shutil.which("gh"):
        try:
            res = subprocess.run(
                ["gh", "api", endpoint],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
                check=True,
            )
            return json.loads(res.stdout)
        except Exception:
            pass

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "github-daily-radar-skill",
    }
    token = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    api_url = f"https://api.github.com/{endpoint.lstrip('/')}"
    with httpx.Client(timeout=25.0, follow_redirects=True, trust_env=False) as client:
        resp = client.get(api_url, headers=headers)
        resp.raise_for_status()
        return resp.json()


def scrape_trending(period: str = "weekly", limit: int = 10) -> list[dict[str, Any]]:
    since = "daily" if period == "daily" else "weekly"
    url = f"https://github.com/trending?since={since}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    with httpx.Client(timeout=20.0, follow_redirects=True, trust_env=False) as client:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")
    rows: list[dict[str, Any]] = []
    for idx, art in enumerate(soup.select("article.Box-row")[:limit], start=1):
        a = art.select_one("h2 a")
        if not a or not a.get("href"):
            continue
        full_name = re.sub(r"\s+", "", a["href"].strip().lstrip("/"))
        desc_el = art.select_one("p")
        description = desc_el.get_text(" ", strip=True) if desc_el else ""
        lang_el = art.select_one('[itemprop="programmingLanguage"]')
        language = lang_el.get_text(strip=True) if lang_el else ""

        delta_text = ""
        for span in art.select("span.d-inline-block.float-sm-right, span.float-sm-right"):
            txt = span.get_text(" ", strip=True)
            if "star" in txt.lower():
                delta_text = txt
                break
        if not delta_text:
            m = re.search(r"([\d,]+)\s+stars\s+(?:today|this week|this month)", art.get_text(" ", strip=True), re.I)
            if m:
                delta_text = m.group(0)

        rows.append(
            {
                "trending_rank": idx,
                "full_name": full_name,
                "url": f"https://github.com/{full_name}",
                "stars_delta": _parse_int(delta_text),
                "stars_delta_text": delta_text or "0 stars",
                "language": language or "Unknown",
                "trending_description": description,
            }
        )
    if not rows:
        raise RuntimeError(f"No trending repositories parsed from {url}")
    return rows


def _clean_readme_excerpt(readme_text: str, max_chars: int = 2800) -> str:
    text = re.sub(r"<!--.*?-->", " ", readme_text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"`{3}.*?`{3}", " ", text, flags=re.S)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith(("---", "==="))]
    return "\n".join(lines)[:max_chars]


def _extract_cli_snippet(readme_text: str) -> str | None:
    """Extract the first real install/run shell command verbatim from README (never fabricated)."""
    blocks = re.findall(r"```(?:bash|sh|zsh|shell|console|powershell)?\s*\n(.*?)```", readme_text, flags=re.S | re.I)
    cmd_prefixes = ("npx ", "npm ", "pnpm ", "yarn ", "pip ", "uv ", "brew ", "go install ", "cargo ", "git clone ", "curl ", "docker ")
    for blk in blocks:
        for raw_ln in blk.splitlines():
            ln = raw_ln.strip().lstrip("$").strip()
            if not ln or ln.startswith("#"):
                continue
            if ln.lower().startswith(cmd_prefixes) and len(ln) <= 88:
                return ln
    return None


def _resolve_repo_url(raw_url: str, full_name: str, default_branch: str) -> str:
    u = raw_url.strip()
    if u.startswith(("http://", "https://")):
        # Convert github.com/owner/repo/blob/branch/path -> raw.githubusercontent.com
        m = re.match(r"https?://github\.com/([^/]+/[^/]+)/blob/([^/]+)/(.*)", u)
        if m:
            return f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/{m.group(3)}"
        return u
    raw_base = f"https://raw.githubusercontent.com/{full_name}/{default_branch}/"
    return urljoin(raw_base, u.lstrip("./"))


def _extract_media_candidates(readme_text: str, full_name: str, default_branch: str) -> tuple[list[str], list[str]]:
    video_cands: list[str] = []
    img_cands: list[str] = []

    vid_patterns = [
        r'https?://github\.com/user-attachments/assets/[A-Za-z0-9-]+',
        r'https?://[^\s"\'<>)]+\.(?:mp4|webm|mov|gif)(?:\?[^\s"\'<>)]*)?',
        r'<video[^>]+src=["\']([^"\']+)["\']',
        r'!\[[^\]]*\]\(([^)]+\.(?:gif|mp4|webm))\)',
    ]
    for pat in vid_patterns:
        for match in re.findall(pat, readme_text, flags=re.I):
            url = _resolve_repo_url(match, full_name, default_branch)
            low = url.lower()
            if any(skip in low for skip in SKIP_IMG_SUBSTRINGS):
                continue
            if url not in video_cands:
                video_cands.append(url)

    img_patterns = [
        r'!\[[^\]]*\]\(([^)\s]+)\)',
        r'<img[^>]+src=["\']([^"\']+)["\']',
    ]
    for pat in img_patterns:
        for match in re.findall(pat, readme_text, flags=re.I):
            url = _resolve_repo_url(match, full_name, default_branch)
            low = url.lower().split("?")[0]
            if any(skip in url.lower() for skip in SKIP_IMG_SUBSTRINGS):
                continue
            if low.endswith((".svg", ".ico", ".mp4", ".webm", ".mov", ".gif")):
                continue
            if url not in img_cands and url not in video_cands:
                img_cands.append(url)

    return video_cands[:4], img_cands[:6]


def _resolve_license_display(spdx_id: str | None, readme_text: str) -> str:
    raw = (spdx_id or "").strip()
    if raw and raw.upper() not in ("NOASSERTION", "NONE", "OTHER"):
        return raw
    low = readme_text.lower()
    for token, label in (
        ("apache-2.0", "Apache-2.0"),
        ("apache 2.0", "Apache-2.0"),
        ("apache license 2.0", "Apache-2.0"),
        ("mit license", "MIT"),
        ("agpl-3.0", "AGPL-3.0"),
        ("gpl-3.0", "GPL-3.0"),
        ("bsd-3-clause", "BSD-3-Clause"),
    ):
        if token in low:
            return label
    return "见仓库 LICENSE"


def enrich_repo(row: dict[str, Any]) -> dict[str, Any]:
    full_name = row["full_name"]
    info = _gh_api_json(f"repos/{full_name}")
    readme_text = ""
    try:
        rm = _gh_api_json(f"repos/{full_name}/readme")
        if rm.get("content"):
            readme_text = base64.b64decode(rm["content"]).decode("utf-8", errors="replace")
    except Exception:
        readme_text = ""

    default_branch = info.get("default_branch") or "main"
    spdx_raw = (info.get("license") or {}).get("spdx_id") or "NOASSERTION"
    lic_display = _resolve_license_display(spdx_raw, readme_text)
    homepage = (info.get("homepage") or "").strip()
    if homepage and not homepage.startswith(("http://", "https://")):
        homepage = "https://" + homepage

    topics = info.get("topics") or []
    desc = info.get("description") or row.get("trending_description") or ""
    excerpt = _clean_readme_excerpt(readme_text)
    haystack = " ".join([full_name, desc, " ".join(topics), excerpt]).lower()
    matched_keywords = sorted({kw for kw in AI_DEV_KEYWORDS if re.search(rf"\b{re.escape(kw)}\b", haystack)})

    vid_cands, img_cands = _extract_media_candidates(readme_text, full_name, default_branch)
    stars_total = int(info.get("stargazers_count") or 0)
    hist_delta = history.compute_historical_delta(full_name, stars_total)

    return {
        **row,
        "description": desc,
        "stargazers_count": stars_total,
        "forks_count": int(info.get("forks_count") or 0),
        "language": info.get("language") or row.get("language") or "Unknown",
        "license": spdx_raw,
        "license_display": lic_display,
        "owner_avatar_url": (info.get("owner") or {}).get("avatar_url") or "",
        "homepage": homepage,
        "topics": topics,
        "default_branch": default_branch,
        "matched_ai_dev_keywords": matched_keywords,
        "readme_excerpt": excerpt,
        "cli_snippet": _extract_cli_snippet(readme_text),
        "media_candidates": vid_cands,
        "image_candidates": img_cands,
        "historical_snapshot_delta": hist_delta,
    }


def score_and_select(
    repos: list[dict[str, Any]],
    focus: str = "ai-dev",
    top_n: int = 5,
    recent_selected: set[str] | None = None,
) -> list[dict[str, Any]]:
    max_delta = max((r["stars_delta"] for r in repos), default=1) or 1
    recent_selected = recent_selected or set()

    for r in repos:
        delta_norm = (r["stars_delta"] / max_delta) * 100.0
        kw_count = len(r["matched_ai_dev_keywords"])
        demo_bonus = 6.0 if r.get("media_candidates") else 0.0
        dedupe_penalty = 25.0 if r["full_name"] in recent_selected else 0.0

        if focus == "all":
            score = float(r["stars_delta"]) - (dedupe_penalty * 20.0)
        elif focus == "ai-only":
            score = (delta_norm * 0.70 + min(30.0, kw_count * 6.0) + demo_bonus - dedupe_penalty) if kw_count > 0 else -1.0
        else:
            score = delta_norm * 0.65 + min(30.0, kw_count * 6.0) + demo_bonus - dedupe_penalty
        r["selection_score"] = round(score, 2)

    if focus == "ai-only":
        filtered = [r for r in repos if r["selection_score"] > 0]
        ranked = sorted(filtered if len(filtered) >= top_n else repos, key=lambda x: (-x["selection_score"], -x["stars_delta"]))
    elif focus == "all":
        ranked = sorted(repos, key=lambda x: (-x["selection_score"], x["trending_rank"]))
    else:
        ranked = sorted(repos, key=lambda x: (-x["selection_score"], -x["stars_delta"]))

    selected = ranked[:top_n]
    for i, item in enumerate(selected, start=1):
        item["video_rank"] = i
    return selected


def _probe_video_file(path: Path) -> dict[str, Any] | None:
    if not path.is_file() or path.stat().st_size < 15_000:
        return None
    try:
        res = subprocess.run(
            [
                str(FFPROBE_EXE),
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name,width,height,nb_frames",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        info = json.loads(res.stdout)
        streams = info.get("streams") or []
        if not streams:
            return None
        v = streams[0]
        w = int(v.get("width") or 0)
        h = int(v.get("height") or 0)
        dur = float((info.get("format") or {}).get("duration") or 0.0)
        codec = str(v.get("codec_name") or "").lower()
        if w < 320 or h < 180:
            return None
        if codec == "gif":
            nb = int(v.get("nb_frames") or 0)
            if dur < 1.0 and nb < 4:
                return None
        elif dur < 1.2:
            return None
        return {"width": w, "height": h, "duration": round(dur, 2), "codec": codec}
    except Exception:
        return None


def _download_candidate_video(url: str, dest_dir: Path, idx: int) -> tuple[Path, dict[str, Any]] | None:
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        with httpx.Client(timeout=25.0, follow_redirects=True, trust_env=False) as client:
            with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code >= 400:
                    return None
                ct = (resp.headers.get("content-type") or "").lower()
                if "video/" in ct:
                    ext = ".mp4" if "mp4" in ct else (".webm" if "webm" in ct else ".mp4")
                elif "image/gif" in ct or url.lower().split("?")[0].endswith(".gif"):
                    ext = ".gif"
                elif url.lower().split("?")[0].endswith((".mp4", ".webm", ".mov")):
                    ext = Path(url.split("?")[0]).suffix.lower()
                else:
                    return None

                out_path = dest_dir / f"demo_{idx}{ext}"
                written = 0
                with out_path.open("wb") as fh:
                    for chunk in resp.iter_bytes(chunk_size=128 * 1024):
                        written += len(chunk)
                        if written > 35 * 1024 * 1024:
                            out_path.unlink(missing_ok=True)
                            return None
                        fh.write(chunk)
        meta = _probe_video_file(out_path)
        if not meta:
            out_path.unlink(missing_ok=True)
            return None
        return out_path, meta
    except Exception:
        return None


def _download_valid_image(url: str, dest: Path, min_w: int = 480, min_h: int = 240) -> bool:
    if dest.is_file() and dest.stat().st_size > 8_000:
        try:
            with Image.open(dest) as im:
                if im.size[0] >= min_w and im.size[1] >= min_h:
                    return True
        except Exception:
            pass
    try:
        with httpx.Client(timeout=15.0, follow_redirects=True, trust_env=False) as client:
            resp = client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code >= 400 or len(resp.content) > 12 * 1024 * 1024:
                return False
            ct = (resp.headers.get("content-type") or "").lower()
            if "svg" in ct or "html" in ct:
                return False
            with Image.open(io.BytesIO(resp.content)) as im:
                w, h = im.size
                if w < min_w or h < min_h:
                    return False
                if w / max(1, h) > 6.0 or h / max(1, w) > 5.0:
                    return False
                im.convert("RGBA").save(dest, format="PNG")
            return True
    except Exception:
        dest.unlink(missing_ok=True)
        return False


def _is_usable_homepage(url: str) -> bool:
    if not url or not url.startswith(("http://", "https://")):
        return False
    low = url.lower()
    return not any(h in low for h in SKIP_HOMEPAGE_HOSTS)


def _capture_screenshot(browser: Any, url: str, dest: Path, max_height: int = 2400) -> bool:
    if dest.is_file() and dest.stat().st_size > 40_000:
        return True
    ctx = browser.new_context(viewport={"width": 1280, "height": 960}, device_scale_factor=2)
    page = ctx.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(1800)
        doc_h = page.evaluate("() => Math.min(document.body.scrollHeight || 1800, 2400)")
        clip_h = max(960, min(max_height, int(doc_h or 1800)))
        page.screenshot(path=str(dest), clip={"x": 0, "y": 0, "width": 1280, "height": clip_h})
        return dest.is_file() and dest.stat().st_size > 40_000
    except Exception:
        return False
    finally:
        ctx.close()


def collect_media_for_selected(selected: list[dict[str, Any]], out_dir: Path) -> list[dict[str, Any]]:
    assets_root = out_dir / "assets"
    assets_root.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = launch_chromium(pw)
        try:
            for item in selected:
                rank = item["video_rank"]
                repo_dir = assets_root / f"repo_{rank:02d}_{_slugify(item['full_name'])}"
                repo_dir.mkdir(parents=True, exist_ok=True)

                # Owner logo/avatar
                avatar_path = repo_dir / "avatar.png"
                has_avatar = False
                if item.get("owner_avatar_url"):
                    has_avatar = _download_valid_image(item["owner_avatar_url"], avatar_path, min_w=64, min_h=64)

                # Tier 1: Official demo video / GIF
                video_path: Path | None = None
                video_meta: dict[str, Any] | None = None
                video_source_url: str | None = None
                for existing in sorted(repo_dir.glob("demo_*")):
                    meta = _probe_video_file(existing)
                    if meta:
                        video_path = existing
                        video_meta = meta
                        break
                if not video_path:
                    for c_idx, c_url in enumerate(item.get("media_candidates") or [], start=1):
                        dl = _download_candidate_video(c_url, repo_dir, c_idx)
                        if dl:
                            video_path, video_meta = dl
                            video_source_url = c_url
                            break

                # Tier 2: Official README content images (up to 2)
                readme_imgs: list[str] = []
                for img_url in item.get("image_candidates") or []:
                    if len(readme_imgs) >= 2:
                        break
                    dest_img = repo_dir / f"readme_img_{len(readme_imgs) + 1}.png"
                    if _download_valid_image(img_url, dest_img, min_w=480, min_h=240):
                        readme_imgs.append(str(dest_img))

                # Tier 3 & 4: Homepage & GitHub Repo 2x Screenshots
                homepage_png = repo_dir / "homepage.png"
                has_homepage = False
                if _is_usable_homepage(item.get("homepage", "")):
                    has_homepage = _capture_screenshot(browser, item["homepage"], homepage_png)
                    if not has_homepage:
                        homepage_png.unlink(missing_ok=True)

                github_png = repo_dir / "github_page.png"
                has_github = _capture_screenshot(browser, item["url"], github_png)
                if not has_github:
                    github_png.unlink(missing_ok=True)

                if video_path:
                    primary_type = "video"
                    primary_path = str(video_path)
                elif has_homepage:
                    primary_type = "screenshot"
                    primary_path = str(homepage_png)
                elif readme_imgs:
                    primary_type = "image"
                    primary_path = readme_imgs[0]
                elif has_github:
                    primary_type = "screenshot"
                    primary_path = str(github_png)
                else:
                    raise RuntimeError(f"Could not acquire any visual media for {item['full_name']}")

                item["media"] = {
                    "primary_type": primary_type,
                    "primary_path": primary_path,
                    "avatar_path": str(avatar_path) if has_avatar else None,
                    "demo_video_path": str(video_path) if video_path else None,
                    "demo_video_meta": video_meta,
                    "demo_video_source_url": video_source_url,
                    "readme_images": readme_imgs,
                    "homepage_screenshot": str(homepage_png) if has_homepage else None,
                    "github_screenshot": str(github_png) if has_github else None,
                }
                print(
                    f"[media] #{rank} {item['full_name']} -> primary={primary_type}, "
                    f"readme_imgs={len(readme_imgs)}, homepage={has_homepage}, github={has_github}, avatar={has_avatar}"
                )
        finally:
            browser.close()
    return selected


def run_fetch(
    period: str,
    top_n: int,
    focus: str,
    out_dir: Path,
    dedupe_days: int = 0,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[fetch] Scraping GitHub Trending ({period}, focus={focus}, top_n={top_n})...")
    raw_top10 = scrape_trending(period=period, limit=10)
    enriched = []
    for row in raw_top10:
        print(f"  [gh] Enriching #{row['trending_rank']} {row['full_name']} (+{row['stars_delta']})...")
        enriched.append(enrich_repo(row))

    recent = history.load_recent_selected(days=dedupe_days) if dedupe_days > 0 else set()
    selected = score_and_select(enriched, focus=focus, top_n=top_n, recent_selected=recent)
    collect_media_for_selected(selected, out_dir)

    now = dt.datetime.now()
    if period == "weekly":
        start_d = (now - dt.timedelta(days=7)).strftime("%Y.%m.%d")
        date_range_display = f"{start_d} – {now.strftime('%Y.%m.%d')}"
    else:
        date_range_display = now.strftime("%Y.%m.%d")

    history.append_snapshot(
        period=period,
        raw_top10=enriched,
        selected_repos=[s["full_name"] for s in selected],
    )

    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "date_range_display": date_range_display,
        "period": period,
        "focus": focus,
        "top_n": top_n,
        "raw_top10": enriched,
        "selected": selected,
    }
    (out_dir / "trending_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    facts_lines = [
        f"# GitHub Trending Facts ({period}, {date_range_display}, focus={focus})",
        "",
        f"- Generated (UTC): `{report['generated_at_utc']}`",
        "",
        "## Selected Repositories for Short Video",
        "",
    ]
    for s in selected:
        facts_lines.extend(
            [
                f"### #{s['video_rank']} {s['full_name']}",
                f"- Trending Rank: #{s['trending_rank']} | Growth: `+{s['stars_delta']}` ({s['stars_delta_text']})",
                f"- Total Stars: `{s['stargazers_count']}` | Forks: `{s['forks_count']}` | Language: `{s['language']}` | License: `{s['license_display']}`",
                f"- CLI Quickstart: `{s['cli_snippet'] or 'N/A'}`",
                f"- Homepage: {s['homepage'] or 'N/A'} | Repo URL: {s['url']}",
                f"- Description: {s['description']}",
                "",
            ]
        )
    facts_lines.extend(
        [
            "## Full Top 10 Trending Snapshot",
            "",
            "| Trending # | Repo | Growth | Total Stars | Lang | License | Score |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for r in enriched:
        facts_lines.append(
            f"| {r['trending_rank']} | {r['full_name']} | +{r['stars_delta']} | {r['stargazers_count']} | {r['language']} | {r['license_display']} | {r['selection_score']} |"
        )
    (out_dir / "facts.md").write_text("\n".join(facts_lines), encoding="utf-8")

    src_lines = [
        "# Media & Source Attribution Ledger",
        "",
        "All visuals are real official demos, README images, or Playwright 2x webpage captures attributed below:",
        "",
    ]
    for s in selected:
        m = s["media"]
        src_lines.extend(
            [
                f"## #{s['video_rank']} {s['full_name']} (`{s['license_display']}`)",
                f"- Source Repository: {s['url']}",
                f"- Primary Visual (`{m['primary_type']}`): `{m['primary_path']}`",
                f"- Official Demo Video URL: {m['demo_video_source_url'] or 'N/A'}",
                f"- README Images ({len(m.get('readme_images') or [])}): {', '.join(m.get('readme_images') or ['N/A'])}",
                f"- Official Homepage Screenshot: `{m['homepage_screenshot'] or 'N/A'}`",
                f"- GitHub Repo Screenshot: `{m['github_screenshot'] or 'N/A'}`",
                "",
            ]
        )
    (out_dir / "sources.md").write_text("\n".join(src_lines), encoding="utf-8")
    return report
