"""
build_edl.py — STEP 2/3/4 (Edit Decision List)

Reads temp/NN.srt and produces temp/NN_edl.json:
  - topic_summary, royal_members_mentioned, tone
  - hook (<=30s, sentence-safe)
  - keep_segments (50-60% retention, silences/intro removed, chronological)
  - narration_blocks (after_hook / mid / closing) with context + member folders

This is a HEURISTIC auto-EDL so the pipeline can run fully automatically.
When driven by Claude (the skill), a higher-quality EDL can be written to the
same path and the rest of the pipeline will use it unchanged.

Usage:
    python scripts/build_edl.py temp/01.srt input_videos/01.mp4
"""
from __future__ import annotations
import re
import sys
from pathlib import Path
from typing import List, Dict

from common import (parse_srt, Cue, cue_ends_sentence, nearest_safe_end,
                    nearest_safe_start, save_json, TEMP)

# --------------------------------------------------------------------------- #
#  Member detection  (name/alias -> canonical folder-subject)
# --------------------------------------------------------------------------- #
MEMBER_ALIASES: Dict[str, List[str]] = {
    "harry":   ["harry", "prince harry", "duke of sussex"],
    "meghan":  ["meghan", "markle", "duchess of sussex"],
    "charles": ["charles", "king charles", "prince charles"],
    "camilla": ["camilla", "queen camilla"],
    "william": ["william", "prince william", "prince of wales"],
    "kate":    ["kate", "catherine", "kate middleton", "princess of wales"],
    "andrew":  ["andrew", "prince andrew", "duke of york", "fergie", "sarah ferguson"],
    "anne":    ["princess anne", "anne"],
    "edward":  ["prince edward", "edward"],
    "sophie":  ["sophie", "duchess of edinburgh"],
    "Diana":   ["diana", "princess diana"],
}

# member -> its own folder PLUS group folders that literally contain that member
MEMBER_FOLDERS: Dict[str, List[str]] = {
    "meghan":  ["meghan", "Harry with Meghan"],
    "harry":   ["harry", "Harry with Meghan", "Harry with William"],
    "charles": ["charles", "Queen Camilla with Charles"],
    "camilla": ["camilla", "Queen Camilla with Charles"],
    "william": ["william", "kate with william", "Harry with William"],
    "kate":    ["kate", "kate with william"],
    "andrew":  ["andrew", "Palace", "general_royal"],
    "anne":    ["anne", "general_royal"],
    "edward":  ["edward", "general_royal"],
    "sophie":  ["sophie", "general_royal"],
    "Diana":   ["Diana"],
}

DRAMA_WORDS = [
    "shock", "shocking", "divorce", "split", "secret", "affair", "royal",
    "accuse", "accusation", "claim", "reveal", "revealed", "leak", "email",
    "clash", "feud", "attack", "slam", "blast", "furious", "betray", "scandal",
    "exclusive", "bombshell", "confront", "explosive", "war", "crisis",
    "stripped", "banned", "snub", "humiliat", "confirm", "confession",
]
TONE_MAP = [
    ("clash",    ["clash", "feud", "war", "confront", "attack", "slam", "blast"]),
    ("shocking", ["shock", "bombshell", "explosive", "stunned", "jaw"]),
    ("drama",    ["divorce", "split", "affair", "betray", "tears", "emotional"]),
    ("suspense", ["secret", "reveal", "leak", "email", "mystery", "hidden"]),
]

SILENCE_GAP = 2.5          # gap (s) between cues treated as removable dead air
INTRO_MAX = 12.0           # first N seconds considered a possible generic intro
MAX_BLOCK = 175.0          # hard max per main block (< 180)
TARGET_BLOCK = 150.0       # aim per main block


# --------------------------------------------------------------------------- #
def detect_members(text: str) -> List[str]:
    t = text.lower()
    found = []
    for member, aliases in MEMBER_ALIASES.items():
        if any(re.search(r"\b" + re.escape(a) + r"\b", t) for a in aliases):
            found.append(member)
    return found


def drama_score(text: str) -> int:
    t = text.lower()
    return sum(t.count(w) for w in DRAMA_WORDS)


def detect_tone(full_text: str) -> str:
    t = full_text.lower()
    best, best_n = "drama", -1
    for tone, words in TONE_MAP:
        n = sum(t.count(w) for w in words)
        if n > best_n:
            best, best_n = tone, n
    return best


# --------------------------------------------------------------------------- #
def pick_hook(cues: List[Cue], duration: float) -> Dict:
    """Strongest ~20-30s window, preferably in first 40%, sentence-safe ends."""
    if not cues:
        return {"start": 0.0, "end": min(25.0, duration), "reason": "fallback"}
    early_limit = duration * 0.40
    best = None
    for i, c in enumerate(cues):
        window_end = c.start + 28.0
        score = 0
        j = i
        while j < len(cues) and cues[j].start < window_end:
            score += drama_score(cues[j].text)
            j += 1
        # bias toward the early part of the video
        if c.start <= early_limit:
            score += 2
        if best is None or score > best[0]:
            best = (score, i)
    start_i = best[1]
    start = cues[start_i].start
    raw_end = start + 28.0
    end = nearest_safe_end([c for c in cues if c.start >= start], min(raw_end, duration))
    if end <= start:
        end = min(start + 25.0, duration)
    # never exceed 30s
    if end - start > 30.0:
        end = nearest_safe_end([c for c in cues if start <= c.start], start + 29.5)
    return {"start": round(start, 3), "end": round(end, 3),
            "reason": "highest drama-keyword density near start"}


# --------------------------------------------------------------------------- #
def build_keep_segments(cues: List[Cue], duration: float, hook: Dict) -> List[Dict]:
    """Drop long silences + likely intro, keep chronological, target 50-60%."""
    if not cues:
        return [{"start": 0.0, "end": duration}]

    # 1) find removable silence gaps -> split points
    segments: List[List[Cue]] = []
    cur: List[Cue] = []
    prev_end = None
    for c in cues:
        if prev_end is not None and (c.start - prev_end) > SILENCE_GAP:
            if cur:
                segments.append(cur)
            cur = []
        cur.append(c)
        prev_end = c.end
    if cur:
        segments.append(cur)

    # 2) drop a generic intro segment at the very start
    def seg_start(s): return s[0].start
    def seg_end(s):   return s[-1].end
    if segments and seg_start(segments[0]) < 1.0 and \
       (seg_end(segments[0]) - seg_start(segments[0])) < INTRO_MAX and \
       drama_score(" ".join(c.text for c in segments[0])) == 0:
        segments = segments[1:] or segments

    # 3) score each segment; drop weakest until retention <= 60%
    scored = []
    for s in segments:
        dur = seg_end(s) - seg_start(s)
        score = drama_score(" ".join(c.text for c in s)) + 0.5  # keep-bias
        scored.append({"seg": s, "dur": dur, "score": score / max(dur, 1)})

    total = sum(x["dur"] for x in scored)
    hi = 0.60 * duration
    lo = 0.50 * duration
    # remove lowest-value segments while above 60% target
    scored_sorted = sorted(scored, key=lambda x: x["score"])
    kept = list(scored)
    idx = 0
    while sum(x["dur"] for x in kept) > hi and idx < len(scored_sorted):
        weakest = scored_sorted[idx]
        if len(kept) > 1:
            kept.remove(weakest)
        idx += 1
    # don't go below 50% by over-trimming (we only removed above 60%, so fine)

    # 4) back to chronological, snap to sentence boundaries
    kept_segs = sorted([x["seg"] for x in kept], key=seg_start)
    out = []
    for s in kept_segs:
        st = nearest_safe_start(cues, seg_start(s))
        en = nearest_safe_end(cues, seg_end(s))
        if en - st > 1.0:
            out.append({"start": round(st, 3), "end": round(en, 3)})
    return out or [{"start": 0.0, "end": duration}]


# --------------------------------------------------------------------------- #
def split_into_blocks(keep: List[Dict]) -> List[List[Dict]]:
    """Group keep_segments into main blocks each < 180s."""
    blocks: List[List[Dict]] = []
    cur: List[Dict] = []
    cur_dur = 0.0
    for seg in keep:
        d = seg["end"] - seg["start"]
        if cur and cur_dur + d > TARGET_BLOCK:
            blocks.append(cur)
            cur, cur_dur = [], 0.0
        cur.append(seg)
        cur_dur += d
    if cur:
        blocks.append(cur)
    return blocks


# --------------------------------------------------------------------------- #
def context_around(cues: List[Cue], t: float, before=True, span=25.0) -> str:
    if before:
        chunk = [c.text for c in cues if t - span <= c.end <= t + 0.5]
    else:
        chunk = [c.text for c in cues if t - 0.5 <= c.start <= t + span]
    return " ".join(chunk).strip()[:400]


def members_for(text: str) -> List[str]:
    folders: List[str] = []
    for m in detect_members(text):
        for f in MEMBER_FOLDERS.get(m, [m]):
            if f not in folders:
                folders.append(f)
    return folders or ["general_royal"]


# --- template narration (heuristic fallback) ------------------------------- #
def narr_after_hook(members, topic) -> str:
    who = members[0] if members else "the royals"
    return (f"So how did it come to this? What you just saw is only the "
            f"beginning of the story around {topic}. Stay with me, because the "
            f"details that follow change how all of this looks. "
            f"Tell me in the comments — do you think {who.title()} saw this coming?")


def narr_mid(ctx_before, ctx_after) -> str:
    return ("But that is not where it ends. What happens next pushes this even "
            "further — and it sets up exactly what you are about to hear.")


def narr_closing(topic, members) -> str:
    names = ", ".join(m.title() for m in members[:3]) if members else "the Royal Family"
    return (
        f"So where does all of this leave things? When you put every piece "
        f"together, the picture around {topic} becomes hard to ignore. "
        f"Each claim, each reaction, and each silence tells its own part of the "
        f"story, and together they point to a moment that could reshape how the "
        f"public sees {names}. The stakes here are bigger than a single headline. "
        f"This is about trust, about image, and about a family that has spent "
        f"generations trying to control its own narrative. What we are watching "
        f"now is that control being tested in real time. Some will defend them, "
        f"others will say the damage is already done, and the truth most likely "
        f"sits somewhere in between. But one thing is clear — this story is far "
        f"from over, and the next chapter may be the most important one yet. "
        f"If you want to keep following every twist as it unfolds, make sure you "
        f"subscribe so you never miss an update on {names}."
    )


# --------------------------------------------------------------------------- #
def build_edl(srt_path: str, video_path: str) -> Dict:
    cues = parse_srt(srt_path)
    from probe_video import probe
    info = probe(video_path)
    duration = info["duration_sec"] or (cues[-1].end if cues else 0.0)
    full_text = " ".join(c.text for c in cues)

    hook = pick_hook(cues, duration)
    members_all = detect_members(full_text)
    tone = detect_tone(full_text)
    topic = (full_text[:120] + "…") if full_text else "this royal story"

    keep = build_keep_segments(cues, duration, hook)
    blocks = split_into_blocks(keep)

    narration_blocks: List[Dict] = []
    # after-hook narration
    narration_blocks.append({
        "position": "after_hook",
        "after_time": hook["end"],
        "text": narr_after_hook(members_all, "this royal story"),
        "members": members_for(full_text[:300]),
        "context_before": context_around(cues, hook["end"], before=True),
        "context_after": context_around(cues, blocks[0][0]["start"] if blocks else hook["end"], before=False),
        "comment_bait": "Do you think they saw this coming?",
    })
    # mid narrations between blocks
    for bi in range(len(blocks) - 1):
        t = blocks[bi][-1]["end"]
        nxt = blocks[bi + 1][0]["start"]
        ctx_b = context_around(cues, t, before=True)
        ctx_a = context_around(cues, nxt, before=False)
        narration_blocks.append({
            "position": "mid",
            "after_time": round(t, 3),
            "text": narr_mid(ctx_b, ctx_a),
            "members": members_for(ctx_b + " " + ctx_a),
            "context_before": ctx_b,
            "context_after": ctx_a,
            "comment_bait": "",
        })
    # closing narration
    last_end = blocks[-1][-1]["end"] if blocks else duration
    narration_blocks.append({
        "position": "closing",
        "after_time": round(last_end, 3),
        "text": narr_closing("this royal story", members_all),
        "members": members_for(full_text) if members_all else ["general_royal"],
        "context_before": context_around(cues, last_end, before=True),
        "context_after": "",
        "comment_bait": "",
    })

    edl = {
        "video": Path(video_path).name,
        "video_path": str(Path(video_path).resolve()),
        "duration_sec": round(duration, 3),
        "topic_summary": full_text[:200],
        "royal_members_mentioned": members_all,
        "tone": tone,
        "hook": hook,
        "keep_segments": keep,
        "_main_blocks": blocks,   # helper for timeline builder
        "narration_blocks": narration_blocks,
    }
    # retention sanity note
    kept = (hook["end"] - hook["start"]) + sum(s["end"] - s["start"] for s in keep)
    edl["_retention_ratio"] = round(kept / duration, 3) if duration else 0
    return edl


def main():
    if len(sys.argv) < 3:
        print("usage: python scripts/build_edl.py <srt> <video>", file=sys.stderr)
        sys.exit(2)
    edl = build_edl(sys.argv[1], sys.argv[2])
    nn = Path(sys.argv[2]).stem
    save_json(edl, TEMP / f"{nn}_edl.json")
    print(f"EDL -> temp/{nn}_edl.json  (retention={edl['_retention_ratio']}, "
          f"blocks={len(edl['_main_blocks'])}, narr={len(edl['narration_blocks'])})")


if __name__ == "__main__":
    main()
