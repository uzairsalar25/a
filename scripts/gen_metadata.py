"""
gen_metadata.py — STEP 10 (YouTube metadata draft)

Reads temp/NN_edl.json (topic, members, tone) and writes output/NN_metadata.txt
with 3 titles, a description, hashtags and tags.

Heuristic draft — never blocks the render. When run via Claude (the skill) the
same file can be overwritten with a stronger, story-aware draft.

Usage:
    python scripts/gen_metadata.py 01
"""
from __future__ import annotations
import sys
from pathlib import Path

from common import load_json, TEMP, OUTPUT, log_error

NAME_PRETTY = {
    "harry": "Prince Harry", "meghan": "Meghan Markle", "charles": "King Charles",
    "camilla": "Queen Camilla", "william": "Prince William", "kate": "Kate Middleton",
    "andrew": "Prince Andrew", "anne": "Princess Anne", "edward": "Prince Edward",
    "sophie": "Duchess Sophie", "Diana": "Princess Diana",
}
TONE_WORD = {"clash": "CLASH", "shocking": "SHOCK", "drama": "DRAMA", "suspense": "SECRET"}


def titles(members, tone):
    names = [NAME_PRETTY.get(m, m.title()) for m in members[:2]] or ["The Royal Family"]
    lead = " & ".join(names) if len(names) > 1 else names[0]
    kw = TONE_WORD.get(tone, "NEW")
    c = [
        f"{lead} {kw}: The Story Everyone Is Talking About",
        f"{lead} Faces NEW Questions — What Really Happened?",
        f"{lead}: The ONE Revelation That's Changing Everything",
        f"{lead} {kw} — Inside the Latest Royal Development",
        f"Is This the End for {lead}? The Truth Behind the Headlines",
    ]
    return c[:3]


def description(members, topic):
    names = ", ".join(NAME_PRETTY.get(m, m.title()) for m in members[:3]) or "the Royal Family"
    p1 = f"This video breaks down the latest developments involving {names}."
    p2 = (f"We look at what has been said, what it could mean, and why so many "
          f"people are reacting. Every claim, timeline detail and response is "
          f"examined so you can decide what to believe.")
    p3 = ("Nothing here is presented as confirmed fact where the story itself "
          "describes reports, claims or speculation.")
    return f"{p1}\n\n{p2}\n\n{p3}"


def hashtags(members):
    tags = []
    for m in members[:2]:
        n = NAME_PRETTY.get(m, m.title()).replace(" ", "")
        tags.append("#" + n)
    tags.append("#RoyalFamily")
    return " ".join(dict.fromkeys(tags))[:120]


def tag_list(members):
    base = ["royal family", "royal news", "british royal family", "royal family news"]
    for m in members:
        pretty = NAME_PRETTY.get(m, m.title()).lower()
        base.append(pretty)
        if " " in pretty:
            base.append(pretty.split()[-1])
    seen, out = set(), []
    for t in base:
        if t not in seen:
            seen.add(t); out.append(t)
    return ", ".join(out[:18])


def generate(nn: str) -> Path:
    edl = load_json(TEMP / f"{nn}_edl.json")
    members = edl.get("royal_members_mentioned", [])
    tone = edl.get("tone", "drama")
    topic = edl.get("topic_summary", "")
    t = titles(members, tone)
    body = (
        f"VIDEO: {nn}_edited.mp4\n\n"
        f"RECOMMENDED TITLE:\n{t[0]}\n\n"
        f"ALT TITLE 1:\n{t[1]}\n\n"
        f"ALT TITLE 2:\n{t[2]}\n\n"
        f"DESCRIPTION:\n{description(members, topic)}\n\n"
        f"HASHTAGS:\n{hashtags(members)}\n\n"
        f"TAGS:\n{tag_list(members)}\n"
    )
    out = OUTPUT / f"{nn}_metadata.txt"
    out.write_text(body, encoding="utf-8")
    return out


def main():
    if len(sys.argv) < 2:
        print("usage: python scripts/gen_metadata.py <NN>", file=sys.stderr)
        sys.exit(2)
    try:
        out = generate(sys.argv[1])
        print(f"metadata -> {out}")
    except Exception as e:
        log_error(f"metadata generation failed for {sys.argv[1]}: {e}")


if __name__ == "__main__":
    main()
