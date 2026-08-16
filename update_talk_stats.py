#!/usr/bin/env python3
"""
Keep the "Conference and Departmental Talks Summary" paragraph in sync with
the actual entries listed under the Departmental Talks and Conferences
sections of the long CV (main_long.tex).

Usage:
    python update_talk_stats.py

No network access required -- this only counts \\cventry lines already
present in main_long.tex. Safe to run on every push (see build-cv.yml), so
the summary numbers never go stale when talks are added by hand or via
Overleaf. The computed counts are written into the summary sentence in both
main_long.tex (which also has the itemised lists) and main.tex (the short
CV, which quotes the same summary sentence but has no itemised lists).
"""

import re
from pathlib import Path

LONG_TEX_FILE = Path(__file__).parent / "main_long.tex"
TEX_FILES = [LONG_TEX_FILE, Path(__file__).parent / "main.tex"]

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
        print(f"  warning: expected 1 talks-summary match, found {n} (left unchanged)")
        return text
    return new_text


if __name__ == "__main__":
    long_text = LONG_TEX_FILE.read_text(encoding="utf-8")

    dept_block = extract_block(long_text, r"\\section\{Departmental Talks\}", r"\\section\{Conferences\}")
    conf_block = extract_block(long_text, r"\\section\{Conferences\}\{\}", r"%\\section\{Awards\}")

    n_dept    = count_entries(dept_block)
    n_conf    = count_entries(conf_block)
    n_invited = count_entries(conf_block, invited_only=True)

    print(f"Departmental talks: {n_dept}")
    print(f"Conferences: {n_conf}")
    print(f"Invited review talks: {n_invited}")

    for tex_file in TEX_FILES:
        if not tex_file.exists():
            print(f"[skip] {tex_file} does not exist")
            continue
        text = tex_file.read_text(encoding="utf-8")
        new_text = update_summary(text, n_dept, n_conf, n_invited)
        if new_text != text:
            tex_file.write_text(new_text, encoding="utf-8")
            print(f"Written -> {tex_file}")
        else:
            print(f"Already up to date -> {tex_file}")
