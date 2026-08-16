#!/usr/bin/env python3
"""
Keep the "Conference and Departmental Talks Summary" paragraph in main.tex in
sync with the actual entries listed under the Departmental Talks and
Conferences sections.

Usage:
    python update_talk_stats.py

No network access required -- this only counts \\cventry lines already
present in main.tex. Safe to run on every push (see build-cv.yml), so the
summary numbers never go stale when talks are added by hand or via Overleaf.
"""

import re
from pathlib import Path

TEX_FILE = Path(__file__).parent / "main.tex"

_CVENTRY_LINE_RE = re.compile(r"^\\cventry\{", re.MULTILINE)
_SPACER_RE = re.compile(r"^\\cventry(\{\})+\s*$")


def count_entries(block, invited_only=False):
    n = 0
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("\\cventry{"):
            continue
        if _SPACER_RE.fullmatch(line):
            continue
        if invited_only and "invited review" not in line.lower():
            continue
        n += 1
    return n


def extract_block(text, start_pat, end_pat):
    m = re.search(start_pat + r"(.*?)" + end_pat, text, re.DOTALL)
    if not m:
        raise RuntimeError(f"Could not find block bounded by {start_pat!r} .. {end_pat!r}")
    return m.group(1)


SUMMARY_RE = re.compile(
    r"(I have given )\d+( talks in departments[^.]*?and at )\d+"
    r"( international conferences since \d+\. This includes )\d+"
    r"( invited review talks at large international conferences\.)"
)


def update_summary(text, n_dept, n_conf, n_invited):
    replacement = (
        rf"\g<1>{n_dept}\g<2>{n_conf}\g<3>{n_invited}\g<4>"
    )
    new_text, n = SUMMARY_RE.subn(replacement, text)
    if n != 1:
        raise RuntimeError(f"Expected exactly one talks-summary match, found {n}")
    return new_text


if __name__ == "__main__":
    text = TEX_FILE.read_text(encoding="utf-8")

    dept_block = extract_block(text, r"\\section\{Departmental Talks\}", r"\\section\{Conferences\}")
    conf_block = extract_block(text, r"\\section\{Conferences\}\{\}", r"%\\section\{Awards\}")

    n_dept    = count_entries(dept_block)
    n_conf    = count_entries(conf_block)
    n_invited = count_entries(conf_block, invited_only=True)

    print(f"Departmental talks: {n_dept}")
    print(f"Conferences: {n_conf}")
    print(f"Invited review talks: {n_invited}")

    new_text = update_summary(text, n_dept, n_conf, n_invited)
    if new_text != text:
        TEX_FILE.write_text(new_text, encoding="utf-8")
        print(f"Written -> {TEX_FILE}")
    else:
        print("Summary already up to date.")
