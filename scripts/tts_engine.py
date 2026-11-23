"""TTS synthesis (Edge-TTS with WordBoundary exact timing, DashScope fallback) & Human Voice Override."""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from config import FFMPEG_EXE, FFPROBE_EXE

DEFAULT_EDGE_VOICE = "zh-CN-YunxiNeural"
DEFAULT_EDGE_RATE = "+15%"
DEFAULT_DASHSCOPE_VOICE = "longxiaochun_v2"
HUMAN_AUDIO_EXTS = (".wav", ".mp3", ".m4a", ".flac", ".aac")

# Atomic token matcher: keeps SKILL.md, .NET, Apache 2.0, modern-web-guidance-src, and CJK chars intact
_TOKEN_RE = re.compile(
    r"\.?[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*"  # English words, .NET, versions (2.0), filenames (SKILL.md), repo names
    r"|[\u4e00-\u9fff]"                        # Individual CJK character
    r"|[，。！？；：、,.!?;:()（）《》“”\"'—–\-/]+"  # Punctuation
)
_CLAUSE_BREAK_PUNCT = set("，。！？；：,!?;:")
_HARD_PUNCT = set("。！？!?；;")


def probe_duration(path: Path) -> float:
    res = subprocess.run(
        [
            str(FFPROBE_EXE),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    return max(0.2, float(res.stdout.strip()))


def normalize_to_wav(src: Path, dest_wav: Path, pad_sec: float = 0.06) -> float:
    subprocess.run(
        [
            str(FFMPEG_EXE),
            "-y",
            "-i",
            str(src),
            "-af",
            f"aresample=44100,apad=pad_dur={pad_sec:.2f}",
            "-ac",
            "1",
            "-ar",
            "44100",
            "-c:a",
            "pcm_s16le",
            str(dest_wav),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return probe_duration(dest_wav)


def _norm_chars(text: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text.lower())


def _display_units(text: str) -> float:
    units = 0.0
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            units += 1.0
        elif ch.isspace():
            units += 0.35
        else:
            units += 0.56
    return units


def _join_tokens(tokens: list[str]) -> str:
    """Join mixed Chinese + atomic English tokens cleanly without unwanted spaces around dots or hyphens."""
    out = ""
    for tok in tokens:
        if not tok:
            continue
        if out and out[-1].isascii() and out[-1].isalnum() and tok[0].isascii() and tok[0].isalnum():
            out += " " + tok
        elif out and (
            ("\u4e00" <= out[-1] <= "\u9fff" and tok[0].isascii() and tok[0].isalnum())
            or (out[-1].isascii() and out[-1].isalnum() and "\u4e00" <= tok[0] <= "\u9fff")
        ):
            # Clean tight spacing between CJK and English technical terms (optional space keeps mobile lines compact)
            out += tok
        else:
            out += tok
    return out.strip()


def _merge_atomic_phrases(tokens: list[str]) -> list[str]:
    """Keep short multi-word technical terms (e.g. 'Claude Code', 'Apache 2.0') together when adjacent."""
    multi_phrases = (
        ("Claude", "Code"),
        ("Claude", "Sonnet"),
        ("Claude", "Opus"),
        ("Apache", "2.0"),
        ("Open", "Code", "Review"),
        ("Modern", "Web", "Guidance"),
        ("Web", "Guidance"),
        ("GitHub", "Trending"),
        ("VS", "Code"),
        ("Code", "Review"),
        ("Agent", "Skills"),
    )
    out: list[str] = []
    i = 0
    n = len(tokens)
    while i < n:
        matched = False
        for phrase in multi_phrases:
            plen = len(phrase)
            if i + plen <= n and all(tokens[i + k].lower() == phrase[k].lower() for k in range(plen)):
                out.append(" ".join(tokens[i : i + plen]))
                i += plen
                matched = True
                break
        if not matched:
            out.append(tokens[i])
            i += 1
    return out


_SOFT_BREAK_AFTER = set("的了地着和与及或、")
_SOFT_BREAK_BEFORE = set("并而却但又再还将就把被让使给从用向把在五个四三两一十")
_CJK_NUM_CHARS = set("零一二两三四五六七八九十百千万亿")
_AVOID_SPLIT_WORDS = (
    "爆火", "开源", "安全", "开发", "方法论", "研究员", "创作者", "开发者",
    "工作流", "知识库", "数据源", "生态", "宿主", "代码", "工具", "项目",
    "救星", "首选", "必备", "思考", "猎手", "平台", "摘要", "推荐",
    "第一", "第二", "第三", "第四", "第五", "第六", "第七", "第八", "第九", "第十",
    "留意", "常用", "用到", "整理", "非常", "直接", "可以", "进行", "支持", "使用",
    "模型", "系统", "接口", "框架", "插件", "部署", "测试", "生成", "自动", "效率",
    "方案", "文档", "架构", "本地", "服务", "团队", "用户", "功能", "能力", "数据",
    "终端", "桌面", "语音", "视频", "网页", "搜索", "技能", "命令", "漏洞", "训练",
    "推理", "微调", "智能体", "多模态", "自动化", "客户端", "可视化", "上下文",
)


def _find_natural_split_idx(token_strs: list[str], cum_units: list[float], total_u: float) -> int:
    """Pick a balanced Chinese syntactic split index so cues never tear multi-char words apart."""
    n = len(token_strs)
    if n < 4:
        return max(1, n // 2)
    target = total_u * 0.52
    best_idx = max(1, n // 2)
    best_score = 1e9

    joined_all = "".join(token_strs)
    # Build character start offsets per token to check _AVOID_SPLIT_WORDS spans
    char_starts: list[int] = []
    pos = 0
    for s in token_strs:
        char_starts.append(pos)
        pos += len(s)

    bad_cut_chars: set[int] = set()
    for w in _AVOID_SPLIT_WORDS:
        start_search = 0
        while True:
            hit = joined_all.find(w, start_search)
            if hit == -1:
                break
            for k in range(hit + 1, hit + len(w)):
                bad_cut_chars.add(k)
            start_search = hit + 1

    for idx in range(2, n - 1):
        left_u = cum_units[idx - 1]
        right_u = total_u - left_u
        if left_u < 4.5 or right_u < 3.5:
            continue
        prev_t = token_strs[idx - 1]
        next_t = token_strs[idx]
        # Never cut inside consecutive Chinese number words or guarded compound words
        if prev_t in _CJK_NUM_CHARS and next_t in _CJK_NUM_CHARS:
            continue
        if char_starts[idx] in bad_cut_chars:
            continue

        score = abs(left_u - target)
        if prev_t in _SOFT_BREAK_AFTER:
            score -= 3.2
        elif next_t in _SOFT_BREAK_BEFORE or (next_t.isascii() and next_t[0].isalnum() and prev_t not in "第每这那几两"):
            score -= 2.2
        if right_u < 4.5:
            score += 2.5
        if score < best_score:
            best_score = score
            best_idx = idx
    return best_idx


def _merge_short_orphan_cues(cues: list[dict[str, Any]], max_merged_units: float = 16.8) -> list[dict[str, Any]]:
    """Prevent tiny 1-word orphan cues (e.g. 'Agent', 'CLI', 'SDK') from flashing alone."""
    if len(cues) <= 1:
        return cues
    merged: list[dict[str, Any]] = []
    for cue in cues:
        txt = cue["text"].strip()
        if not txt:
            continue
        if merged and _display_units(txt) <= 4.2:
            prev = merged[-1]
            gap = float(cue["start_sec"]) - float(prev["end_sec"])
            comb = _join_tokens([prev["text"], txt])
            if gap <= 0.45 and _display_units(comb) <= max_merged_units:
                prev["text"] = comb
                prev["end_sec"] = max(float(prev["end_sec"]), float(cue["end_sec"]))
                continue
        merged.append(dict(cue))

    # Second pass: if the very first cue is tiny and next cue can absorb it
    if len(merged) >= 2 and _display_units(merged[0]["text"]) <= 3.6:
        gap = float(merged[1]["start_sec"]) - float(merged[0]["end_sec"])
        comb = _join_tokens([merged[0]["text"], merged[1]["text"]])
        if gap <= 0.45 and _display_units(comb) <= max_merged_units:
            merged[1]["text"] = comb
            merged[1]["start_sec"] = merged[0]["start_sec"]
            merged.pop(0)
    return merged


def _split_long_token_chunk(tok_items: list[Any], max_units: float, get_str) -> list[list[Any]]:
    """Recursively split a single punctuation-free clause cleanly at syntactic boundaries if > max_units."""
    full_str = _join_tokens([get_str(x) for x in tok_items])
    if _display_units(full_str) <= max_units or len(tok_items) < 4:
        return [tok_items]
    strs = [get_str(x) for x in tok_items]
    cum_u: list[float] = []
    for i in range(len(tok_items)):
        cum_u.append(_display_units(_join_tokens(strs[: i + 1])))
    split_i = _find_natural_split_idx(strs, cum_u, cum_u[-1])
    left = tok_items[:split_i]
    right = tok_items[split_i:]
    out: list[list[Any]] = []
    out.extend(_split_long_token_chunk(left, max_units, get_str))
    out.extend(_split_long_token_chunk(right, max_units, get_str))
    return out


def split_subtitle_clauses_safe(text: str, max_units: float = 16.0) -> list[str]:
    """Dot-safe clause splitter: splits at punctuation and syntactic midpoints cleanly."""
    tokens = _merge_atomic_phrases(_TOKEN_RE.findall(text.strip()))
    if not tokens:
        return [text.strip()]

    raw_groups: list[list[str]] = []
    buf: list[str] = []

    def flush() -> None:
        nonlocal buf
        if buf:
            raw_groups.append(buf)
        buf = []

    for tok in tokens:
        is_punct = all(c in _CLAUSE_BREAK_PUNCT or c in "、（）《》“”\"'—–-/" for c in tok) and not any(
            c.isalnum() or ("\u4e00" <= c <= "\u9fff") for c in tok
        )
        if is_punct:
            if tok in _CLAUSE_BREAK_PUNCT:
                if tok in "！？!?":
                    buf.append(tok)
                flush()
            elif buf:
                buf.append(tok)
            continue
        buf.append(tok)
    flush()

    clauses: list[str] = []
    for grp in raw_groups:
        for chunk in _split_long_token_chunk(grp, max_units, lambda t: t):
            s = _join_tokens(chunk).rstrip("，。；：、,;:")
            if s:
                clauses.append(s)

    if len(clauses) >= 2:
        compact: list[str] = []
        for cl in clauses:
            if compact and _display_units(cl) <= 4.2:
                cand = _join_tokens([compact[-1], cl])
                if _display_units(cand) <= 16.8:
                    compact[-1] = cand
                    continue
            compact.append(cl)
        clauses = compact
    return clauses or [text.strip()]


def build_word_aligned_cues(
    text: str,
    duration_sec: float,
    word_events: list[dict[str, Any]],
    max_units: float = 16.0,
) -> list[dict[str, Any]]:
    """Align spoken_text tokens onto real Edge-TTS WordBoundary timestamps without breaking English tokens."""
    tokens = _merge_atomic_phrases(_TOKEN_RE.findall(text.strip()))
    if not tokens:
        return []

    wb_list = [e for e in word_events if e.get("type") == "WordBoundary" and _norm_chars(e.get("text", ""))]
    if not wb_list:
        return build_proportional_cues(text, duration_sec)

    # Flatten WordBoundary events into character-level timing slots
    char_slots: list[tuple[str, float, float]] = []
    for ev in wb_list:
        nc = _norm_chars(ev["text"])
        s = float(ev["offset_sec"])
        e = max(s + 0.04, s + float(ev["duration_sec"]))
        span = e - s
        n = len(nc)
        for i, ch in enumerate(nc):
            cs = s + span * (i / n)
            ce = s + span * ((i + 1) / n)
            char_slots.append((ch, cs, ce))

    wb_stream = "".join(c[0] for c in char_slots)
    cursor = 0
    matched_tokens: list[dict[str, Any]] = []

    for tok in tokens:
        nc = _norm_chars(tok)
        is_punct = not nc
        if is_punct:
            matched_tokens.append({"tok": tok, "is_punct": True, "start": None, "end": None})
            continue

        # Search forward within small window to stay resilient to minor punctuation/spacing normalization
        found_idx = wb_stream.find(nc, cursor, min(len(wb_stream), cursor + 24))
        if found_idx == -1 and len(nc) > 2:
            # Partial prefix fallback if Edge-TTS read a symbol differently
            found_idx = wb_stream.find(nc[:2], cursor, min(len(wb_stream), cursor + 16))
        if found_idx != -1:
            end_idx = min(len(char_slots) - 1, found_idx + len(nc) - 1)
            t_start = char_slots[found_idx][1]
            t_end = char_slots[end_idx][2]
            cursor = found_idx + len(nc)
            matched_tokens.append({"tok": tok, "is_punct": False, "start": t_start, "end": t_end})
        else:
            # Interpolate smoothly at current cursor time
            fallback_t = char_slots[min(cursor, len(char_slots) - 1)][1] if char_slots else 0.0
            matched_tokens.append({"tok": tok, "is_punct": False, "start": fallback_t, "end": fallback_t + 0.16})

    # Group matched tokens by punctuation clauses first, then split long clauses cleanly at word boundaries
    cues: list[dict[str, Any]] = []
    raw_groups: list[list[dict[str, Any]]] = []
    buf: list[dict[str, Any]] = []

    def _commit_chunk(chunk: list[dict[str, Any]]) -> None:
        words_only = [b for b in chunk if not b["is_punct"] and b["start"] is not None]
        raw_str = _join_tokens([b["tok"] for b in chunk]).rstrip("，。；：、,;:")
        if raw_str and words_only:
            s_t = float(words_only[0]["start"])
            e_t = float(words_only[-1]["end"])
            cues.append({"text": raw_str, "start_sec": round(s_t, 3), "end_sec": round(max(s_t + 0.22, e_t), 3)})

    for item in matched_tokens:
        tok = item["tok"]
        if item["is_punct"]:
            if tok in _CLAUSE_BREAK_PUNCT:
                if tok in "！？!?":
                    buf.append(item)
                if buf:
                    raw_groups.append(buf)
                    buf = []
            elif buf:
                buf.append(item)
            continue
        buf.append(item)
    if buf:
        raw_groups.append(buf)

    for grp in raw_groups:
        for chunk in _split_long_token_chunk(grp, max_units, lambda b: b["tok"]):
            _commit_chunk(chunk)

    cues = _merge_short_orphan_cues(cues, max_merged_units=16.8)

    # Enforce strict monotonic non-overlapping timestamps within [0, duration_sec]
    max_t = max(0.3, duration_sec - 0.03)
    for i, c in enumerate(cues):
        if i == 0:
            c["start_sec"] = round(max(0.0, c["start_sec"]), 3)
        else:
            # Bridge tiny inter-word breath gaps cleanly up to 0.15s, or clamp overlaps
            if c["start_sec"] < cues[i - 1]["end_sec"]:
                c["start_sec"] = cues[i - 1]["end_sec"]
        c["end_sec"] = round(min(max_t, max(c["start_sec"] + 0.20, c["end_sec"])), 3)

    return cues or build_proportional_cues(text, duration_sec)


def build_proportional_cues(text: str, duration_sec: float) -> list[dict[str, Any]]:
    """Proportional dot-safe fallback aligner for non-WordBoundary engines (human_override / DashScope)."""
    clauses = split_subtitle_clauses_safe(text)
    if not clauses:
        return []
    usable_start = 0.04
    usable_end = max(0.3, duration_sec - 0.06)
    weights = [max(2.0, _display_units(c)) for c in clauses]
    total_w = sum(weights)
    span = max(0.25, usable_end - usable_start)
    cur = usable_start
    cues: list[dict[str, Any]] = []
    for c, w in zip(clauses, weights):
        seg_dur = span * (w / total_w)
        end_t = round(cur + seg_dur, 3)
        cues.append({"text": c, "start_sec": round(cur, 3), "end_sec": end_t})
        cur = end_t
    return cues


async def _synth_edge_async(text: str, voice: str, rate: str, out_mp3: Path) -> list[dict[str, Any]]:
    import edge_tts

    comm = edge_tts.Communicate(text, voice=voice, rate=rate, boundary="WordBoundary")
    events: list[dict[str, Any]] = []
    with out_mp3.open("wb") as fh:
        async for chunk in comm.stream():
            ctype = chunk.get("type")
            if ctype == "audio":
                fh.write(chunk["data"])
            elif ctype in ("SentenceBoundary", "WordBoundary"):
                offset_sec = float(chunk.get("offset") or 0) / 10_000_000.0
                dur_sec = float(chunk.get("duration") or 0) / 10_000_000.0
                events.append(
                    {"type": ctype, "offset_sec": offset_sec, "duration_sec": dur_sec, "text": chunk.get("text", "")}
                )
    return events


def _synth_dashscope(text: str, out_mp3: Path, voice: str = DEFAULT_DASHSCOPE_VOICE) -> None:
    import dashscope
    from dashscope.audio.tts_v2 import SpeechSynthesizer

    key = (os.environ.get("DASHSCOPE_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("DASHSCOPE_API_KEY not configured")
    dashscope.api_key = key
    synth = SpeechSynthesizer(model="cosyvoice-v2", voice=voice)
    audio_bytes = synth.call(text)
    if not audio_bytes:
        raise RuntimeError("DashScope returned empty audio")
    out_mp3.write_bytes(audio_bytes)


def _synth_sapi_windows(text: str, out_wav: Path) -> None:
    escaped = text.replace("'", "''")
    path_str = str(out_wav).replace("'", "''")
    ps = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.Rate = 2; "
        f"$s.SetOutputToWaveFile('{path_str}'); "
        f"$s.Speak('{escaped}'); "
        "$s.Dispose()"
    )
    res = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, check=False)
    if res.returncode != 0 or not out_wav.is_file():
        raise RuntimeError(f"Windows SAPI TTS failed: {res.stderr or res.stdout}")


def _find_human_override(voice_dir: Path | None, seg_id: str) -> Path | None:
    if not voice_dir or not voice_dir.is_dir():
        return None
    for ext in HUMAN_AUDIO_EXTS:
        exact = voice_dir / f"{seg_id}{ext}"
        if exact.is_file() and exact.stat().st_size > 1000:
            return exact
    prefix = "_".join(seg_id.split("_")[:2])
    for cand in sorted(voice_dir.iterdir()):
        if cand.is_file() and cand.suffix.lower() in HUMAN_AUDIO_EXTS and cand.stem.startswith(prefix) and cand.stat().st_size > 1000:
            return cand
    return None


def synthesize_storyboard_audio(
    storyboard: dict[str, Any],
    out_dir: Path,
    tts_engine: str = "edge",
    edge_voice: str = DEFAULT_EDGE_VOICE,
    edge_rate: str = DEFAULT_EDGE_RATE,
    voice_dir: Path | None = None,
) -> dict[str, Any]:
    audio_dir = out_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    segments_out: list[dict[str, Any]] = []
    total_dur = 0.0

    for seg in storyboard["segments"]:
        seg_id = seg["id"]
        text = seg["spoken_text"]
        wav_path = audio_dir / f"{seg_id}.wav"
        tmp_raw = audio_dir / f"{seg_id}_raw.mp3"

        override = _find_human_override(voice_dir, seg_id)
        if override:
            print(f"[voice-override] Using human recording for {seg_id}: {override.name}")
            dur = normalize_to_wav(override, wav_path)
            cues = build_proportional_cues(text, dur)
            used_engine = "human_override"
        else:
            events: list[dict[str, Any]] = []
            used_engine = tts_engine

            if tts_engine == "dashscope":
                try:
                    _synth_dashscope(text, tmp_raw)
                    dur = normalize_to_wav(tmp_raw, wav_path)
                    cues = build_proportional_cues(text, dur)
                except Exception as exc:
                    print(f"[tts-warn] DashScope failed on {seg_id} ({exc}), falling back to edge-tts...")
                    events = asyncio.run(_synth_edge_async(text, edge_voice, edge_rate, tmp_raw))
                    dur = normalize_to_wav(tmp_raw, wav_path)
                    cues = build_word_aligned_cues(text, dur, events)
                    used_engine = "edge_fallback"
            else:
                try:
                    events = asyncio.run(_synth_edge_async(text, edge_voice, edge_rate, tmp_raw))
                    dur = normalize_to_wav(tmp_raw, wav_path)
                    cues = build_word_aligned_cues(text, dur, events)
                    used_engine = "edge_word_aligned"
                except Exception as exc:
                    print(f"[tts-warn] Edge-TTS failed on {seg_id} ({exc}), trying DashScope fallback...")
                    try:
                        _synth_dashscope(text, tmp_raw)
                        dur = normalize_to_wav(tmp_raw, wav_path)
                        cues = build_proportional_cues(text, dur)
                        used_engine = "dashscope_fallback"
                    except Exception as exc2:
                        print(f"[tts-warn] DashScope fallback failed ({exc2}), using Windows SAPI...")
                        sapi_wav = audio_dir / f"{seg_id}_sapi.wav"
                        _synth_sapi_windows(text, sapi_wav)
                        dur = normalize_to_wav(sapi_wav, wav_path)
                        sapi_wav.unlink(missing_ok=True)
                        cues = build_proportional_cues(text, dur)
                        used_engine = "sapi_fallback"

            if tmp_raw.exists():
                tmp_raw.unlink(missing_ok=True)

        total_dur += dur
        print(f"[tts] {seg_id} ({used_engine}): {dur:.2f}s, {len(cues)} cues")
        segments_out.append(
            {
                "id": seg_id,
                "engine": used_engine,
                "wav_path": str(wav_path),
                "duration_sec": round(dur, 3),
                "cues": cues,
            }
        )

    manifest = {
        "rate": edge_rate,
        "total_duration_sec": round(total_dur, 3),
        "segments": segments_out,
    }
    (audio_dir / "audio_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest
