#!/usr/bin/env python3
"""
Regenerate the Publication List section of main_long.tex from ADS / Sci-X,
and keep the summary stats in main.tex (the short CV) in sync.

Usage:
    python generate_publications.py                # ADS, falling back to Sci-X
    python generate_publications.py --provider scix   # force Sci-X only
    python generate_publications.py --provider ads    # force ADS only
    python generate_publications.py --compare         # fetch both, report diffs, write nothing

Fetches every paper in the bibliography library, then rewrites the two
auto-generated blocks in main_long.tex (first-author and other publications)
in place, keeping everything else in the file untouched. Also refreshes the
h-index / total citations / first-author-count numbers quoted in the
research summary of both main_long.tex and main.tex (which has no full
publication list, just that summary sentence).

Sci-X (https://scixplorer.org) is the NASA-funded successor to ADS and
exposes the same API shape at a different host, so it is used as an
automatic fallback if the ADS request fails (e.g. once ADS is retired).

Requirements:
    pip install requests
"""

import argparse
import html
import os
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

import requests

# ── Config ────────────────────────────────────────────────────────────────────
PROVIDERS = {
    "ads": {
        "base":    "https://api.adsabs.harvard.edu/v1",
        "key":     os.getenv("ADS_API_KEY", "m2WhxZnX56sVSewEwcEORL9szXB1JBm7GwE3hW1k"),
        "library": os.getenv("ADS_LIBRARY_ID", "PK0RWOWOTIKWfo-5Fck9sg"),
    },
    "scix": {
        "base":    "https://scixplorer.org/v1",
        "key":     os.getenv("SCIX_API_KEY"),
        "library": os.getenv("SCIX_LIBRARY_ID", "Ziwc_DaXT5KtyLxi8e9FQA"),
    },
}
# Order in which providers are tried when none is forced explicitly.
PROVIDER_PRIORITY = ["ads", "scix"]

# The full publication list lives only in the long CV; the short CV just
# quotes the summary stats (h-index / citations / first-author count).
TEX_FILES = [
    Path(__file__).parent / "main_long.tex",
    Path(__file__).parent / "main.tex",
]

FIRST_AUTHOR_RE = re.compile(
    r"^Winter,\s+(Andrew(?:\s+J\.?)*\.?|A\.(?:\s*J\.?)?)\s*$", re.IGNORECASE
)

MAX_SHOW = 3  # authors shown before "et al." in \cventry author lists

# doctype groups (mirrors the website generator)
ARTICLE_TYPES    = {"article", "eprint", "bookreview", "erratum", "techreport", "intechreport"}
CONFERENCE_TYPES = {"inproceedings", "proceedings", "abstract", "talk"}
PROPOSAL_TYPES   = {"proposal"}

JOURNAL_SHORT = {
    "monthly notices of the royal astronomical society": "MNRAS",
    "mnras": "MNRAS",
    "the astrophysical journal": "ApJ",
    "astrophysical journal": "ApJ",
    "the astrophysical journal letters": "ApJL",
    "astrophysical journal letters": "ApJL",
    "the astrophysical journal supplement series": "ApJS",
    "the astronomical journal": "AJ",
    "astronomical journal": "AJ",
    "astronomy & astrophysics": "A&A",
    "astronomy and astrophysics": "A&A",
    "a&a": "A&A",
    "nature astronomy": "Nature Astronomy",
    "nature": "Nature",
    "european physical journal plus": "EPJ+",
    "the european physical journal plus": "EPJ+",
    "publications of the astronomical society of australia": "PASA",
    "publications of the astronomical society of the pacific": "PASP",
    "the open journal of astrophysics": "OJAp",
    "open journal of astrophysics": "OJAp",
    "arxiv e-prints": "arXiv",
}

MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# ── Markers delimiting the auto-generated blocks in main.tex ───────────────────
START_FIRST = "% ADS-AUTOGEN:FIRST-AUTHOR:START"
END_FIRST   = "% ADS-AUTOGEN:FIRST-AUTHOR:END"
START_OTHER = "% ADS-AUTOGEN:OTHER:START"
END_OTHER   = "% ADS-AUTOGEN:OTHER:END"


# ── ADS / Sci-X helpers ─────────────────────────────────────────────────────
# Both services expose the same Solr-backed API shape (Sci-X is built on the
# ADS codebase), so a single set of functions works for either, parameterised
# by a "provider" dict of {base, key, library}.

def api_headers(provider):
    return {
        "Authorization": f"Bearer {provider['key']}",
        "Accept": "application/json",
    }


def fetch_all_bibcodes(provider):
    bibcodes, start, page_size = [], 0, 2000
    while True:
        r = requests.get(
            f"{provider['base']}/biblib/libraries/{provider['library']}",
            headers=api_headers(provider),
            params={"rows": page_size, "start": start},
            timeout=60,
        )
        r.raise_for_status()
        data  = r.json()
        batch = data.get("documents", [])
        bibcodes.extend(batch)
        total = data.get("metadata", {}).get("num_documents", len(bibcodes))
        print(f"  bibcodes: {len(bibcodes)}/{total}")
        if len(bibcodes) >= total or not batch:
            break
        start += page_size
    return bibcodes


def fetch_metadata(provider, bibcodes):
    docs, batch_size = [], 100
    for i in range(0, len(bibcodes), batch_size):
        batch = bibcodes[i : i + batch_size]
        q = " OR ".join(f'bibcode:"{bc}"' for bc in batch)
        r = requests.get(
            f"{provider['base']}/search/query",
            headers=api_headers(provider),
            params={
                "q": q,
                "fl": "bibcode,title,author,author_count,pub,volume,page,"
                      "page_range,year,pubdate,citation_count,doi,doctype",
                "rows": batch_size,
            },
            timeout=60,
        )
        r.raise_for_status()
        docs.extend(r.json().get("response", {}).get("docs", []))
        print(f"  metadata: {len(docs)} fetched")
    return docs


def fetch_metrics(provider, bibcodes):
    """Returns (h_index, total_citations); falls back to (None, None) on failure."""
    try:
        r = requests.post(
            f"{provider['base']}/metrics",
            headers={**api_headers(provider), "Content-Type": "application/json"},
            json={"bibcodes": bibcodes},
            timeout=60,
        )
        r.raise_for_status()
        d = r.json()
        h_index    = d.get("indicators", {}).get("h")
        total_cite = d.get("citation stats", {}).get("total number of citations")
        return h_index, total_cite
    except requests.RequestException as exc:
        print(f"  metrics fetch failed ({exc}), skipping stats update")
        return None, None


def fetch_all(provider):
    """Fetch bibcodes, metadata and metrics for a single provider."""
    bibcodes = fetch_all_bibcodes(provider)
    print(f"Fetching metadata for {len(bibcodes)} papers...")
    docs = fetch_metadata(provider, bibcodes)
    print("Fetching citation metrics...")
    h_index, total_citations = fetch_metrics(provider, bibcodes)
    return bibcodes, docs, h_index, total_citations


def fetch_with_fallback(order):
    """Try providers in `order`, returning the first that succeeds.

    Returns (provider_name, bibcodes, docs, h_index, total_citations).
    """
    errors = {}
    for name in order:
        provider = PROVIDERS[name]
        if not provider["key"]:
            print(f"[{name}] no API key configured, skipping")
            continue
        print(f"[{name}] fetching library {provider['library']}...")
        try:
            bibcodes, docs, h_index, total_citations = fetch_all(provider)
            return name, bibcodes, docs, h_index, total_citations
        except requests.RequestException as exc:
            print(f"[{name}] failed: {exc}")
            errors[name] = str(exc)
    raise RuntimeError(f"All providers failed: {errors}")


# ── Formatting helpers ───────────────────────────────────────────────────────

_UNICODE_DASHES = ["‐", "‑", "‒", "–", "—", "−", "―"]
_BOX_DRAWING_RANGE = range(0x2500, 0x2580)  # ADS titles sometimes use these as hyphens

# Greek letters occasionally appearing verbatim in ADS titles (e.g. "λ Orionis")
_GREEK_MACROS = {
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "varepsilon",
    "ζ": "zeta", "η": "eta", "θ": "theta", "ι": "iota", "κ": "kappa",
    "λ": "lambda", "μ": "mu", "ν": "nu", "ξ": "xi", "π": "pi", "ρ": "rho",
    "σ": "sigma", "τ": "tau", "υ": "upsilon", "φ": "varphi", "χ": "chi",
    "ψ": "psi", "ω": "omega",
    "Γ": "Gamma", "Δ": "Delta", "Θ": "Theta", "Λ": "Lambda", "Ξ": "Xi",
    "Π": "Pi", "Σ": "Sigma", "Υ": "Upsilon", "Φ": "Phi", "Ψ": "Psi", "Ω": "Omega",
}

_TEX_SPECIAL = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


_SUB_RE = re.compile(r"<SUB>(.*?)</SUB>", re.IGNORECASE)
_SUP_RE = re.compile(r"<SUP>(.*?)</SUP>", re.IGNORECASE)
_TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")
# Private-use placeholders so sub/sup markers survive the character-by-character
# tex-special-char escaping below, then get expanded to real math mode afterwards.
_SUB_OPEN, _SUB_CLOSE = "", ""
_SUP_OPEN, _SUP_CLOSE = "", ""


def tex_escape(s):
    s = unicodedata.normalize("NFKC", s or "")
    # ADS titles occasionally leak HTML-entity-encoded XML (e.g. a stray
    # "&lt;xref .../&gt;" footnote marker) -- unescape entities first so the
    # tag-stripping pass below can actually see and remove the tag.
    s = html.unescape(s)
    # ADS titles sometimes embed HTML-ish sub/superscript tags (e.g. "CO<SUB>2</SUB>").
    s = _SUB_RE.sub(lambda m: f"{_SUB_OPEN}{m.group(1)}{_SUB_CLOSE}", s)
    s = _SUP_RE.sub(lambda m: f"{_SUP_OPEN}{m.group(1)}{_SUP_CLOSE}", s)
    s = _TAG_RE.sub("", s)
    for dash in _UNICODE_DASHES:
        s = s.replace(dash, "-")
    s = "".join(
        "-" if (unicodedata.category(ch) == "Pd" or ord(ch) in _BOX_DRAWING_RANGE) else ch
        for ch in s
    )
    escaped = "".join(_TEX_SPECIAL.get(ch, ch) for ch in s)
    for ch, macro in _GREEK_MACROS.items():
        escaped = escaped.replace(ch, f"${{\\{macro}}}$")
    escaped = escaped.replace(_SUB_OPEN, "$_{").replace(_SUB_CLOSE, "}$")
    escaped = escaped.replace(_SUP_OPEN, "$^{").replace(_SUP_CLOSE, "}$")
    return escaped


def parse_year(pubdate):
    try:
        return int(pubdate[:4])
    except (TypeError, ValueError):
        return 0


def parse_month_year(pubdate):
    try:
        y, m = int(pubdate[:4]), int(pubdate[5:7])
    except (TypeError, ValueError, IndexError):
        return "n.d."
    mon = MONTHS[m] if 1 <= m <= 12 else ""
    return f"{mon} {y % 100:02d}".strip()


def is_first_author(doc):
    authors = doc.get("author") or []
    return bool(authors and FIRST_AUTHOR_RE.match(authors[0]))


def categorise(doc):
    dt = (doc.get("doctype") or "article").lower()
    if dt in CONFERENCE_TYPES:
        return "conference"
    if dt in PROPOSAL_TYPES:
        return "proposal"
    return "article"


def author_display(raw):
    """Convert 'Last, First Middle' -> 'F. M. Last', tex-escaped."""
    if "," in raw:
        last, first = raw.split(",", 1)
        inits = " ".join(p[0] + "." for p in first.strip().split() if p)
        return tex_escape(f"{inits} {last.strip()}")
    return tex_escape(raw.strip())


def format_authors(doc):
    """Return a tex author string, bolding whichever entry is Winter, A*.

    - If Winter is within the first MAX_SHOW authors: show up to MAX_SHOW
      authors (his name bolded); "\\&" joins an exact pair, otherwise commas;
      append ", et al." if there are more authors beyond MAX_SHOW.
    - Otherwise: show the first two authors, an ellipsis, Winter's name
      (bolded), then "et al.".
    """
    authors  = doc.get("author") or []
    nauthors = int(doc.get("author_count") or len(authors))

    me_idx, me_raw = None, None
    for i, a in enumerate(authors):
        if FIRST_AUTHOR_RE.match(a):
            me_idx, me_raw = i, a
            break

    if me_idx is not None and me_idx < MAX_SHOW:
        shown = authors[:MAX_SHOW]
        parts = []
        for i, a in enumerate(shown):
            display = author_display(a)
            if i == me_idx:
                display = f"\\textbf{{{display}}}"
            parts.append(display)
        if nauthors == 2:
            return " \\& ".join(parts)
        joined = ", ".join(parts)
        if nauthors > MAX_SHOW:
            joined += ", \\textit{et al.}"
        return joined
    else:
        parts = [author_display(a) for a in authors[:2]]
        joined = ", ".join(parts)
        me_disp = f"\\textbf{{{author_display(me_raw)}}}" if me_raw else ""
        return f"{joined}, \\dots {me_disp} \\textit{{et al.}}"


def shorten_journal(raw):
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    raw = (raw or "").strip()
    return JOURNAL_SHORT.get(raw.lower(), raw)


def format_journal(doc):
    """Return e.g. 'MNRAS \\textbf{521}:1646-1673' or an arXiv id fallback."""
    volume = doc.get("volume")
    if volume:
        journal = tex_escape(shorten_journal(doc.get("pub")))
        page_range = doc.get("page_range")
        page = page_range if page_range else ((doc.get("page") or [""])[0])
        page = tex_escape(page)
        return f"{journal} \\textbf{{{volume}}}:{page}"

    # No volume (e.g. arXiv-only entries) -> fall back to the arXiv id.
    for doi in doc.get("doi") or []:
        m = re.match(r"10\.48550/arXiv\.(.+)", doi)
        if m:
            return f"arXiv:{m.group(1)}"
    return tex_escape(doc.get("bibcode", ""))


def build_entry(doc):
    title = (doc.get("title") or ["(no title)"])[0] \
        if isinstance(doc.get("title"), list) else (doc.get("title") or "(no title)")
    title = tex_escape(title)
    cites = int(doc.get("citation_count") or 0)
    date  = parse_month_year(doc.get("pubdate", ""))
    cite_str = f" -- Citations: {cites}" if cites else ""

    return (
        f"\\cventry{{{date}}}{{\\textnormal{{\\textit{{{title}{cite_str}}}"
        f" \\newline {format_authors(doc)} -- {format_journal(doc)}}}}}{{}}{{}}{{}}{{}}"
    )


def build_block(docs):
    ordered = sorted(docs, key=lambda d: d.get("pubdate", ""), reverse=True)
    return "\n".join(build_entry(d) for d in ordered)


# ── main.tex patching ────────────────────────────────────────────────────────

def replace_between(text, start_marker, end_marker, new_body):
    """Replace the block between markers, if present.

    Files without the markers (e.g. the short CV, which has no full
    publication list) are left untouched rather than erroring.
    """
    if start_marker not in text:
        return text
    pattern = re.compile(
        re.escape(start_marker) + r".*?" + re.escape(end_marker), re.DOTALL
    )
    replacement = f"{start_marker}\n{new_body}\n{end_marker}"
    new_text, n = pattern.subn(lambda _m: replacement, text)
    if n != 1:
        raise RuntimeError(f"Expected exactly one {start_marker}...{end_marker} block, found {n}")
    return new_text


def update_summary_stats(text, h_index, total_citations, n_first):
    pattern = re.compile(
        r"h-index of \d+ and a total of \d+ citations, with \d+ first author articles"
    )
    if h_index is None or total_citations is None:
        return text
    replacement = (
        f"h-index of {h_index} and a total of {total_citations} citations, "
        f"with {n_first} first author articles"
    )
    new_text, n = pattern.subn(replacement, text)
    if n != 1:
        print(f"  warning: expected 1 summary-stats match, found {n} (left unchanged)")
        return text
    return new_text


# ── Cross-provider comparison (sanity check, e.g. ADS vs Sci-X) ────────────────

def compare_providers(name_a, name_b):
    """Fetch both providers and print a diagnostic diff. Writes nothing."""
    results = {}
    for name in (name_a, name_b):
        provider = PROVIDERS[name]
        if not provider["key"]:
            print(f"[{name}] no API key configured -- cannot compare")
            return
        print(f"[{name}] fetching library {provider['library']}...")
        bibcodes, docs, h_index, total_citations = fetch_all(provider)
        results[name] = {
            "bibcodes": set(bibcodes),
            "docs": {d["bibcode"]: d for d in docs if d.get("bibcode")},
            "h_index": h_index,
            "total_citations": total_citations,
        }

    a, b = results[name_a], results[name_b]
    print(f"\n=== {name_a} vs {name_b} ===")
    print(f"  {name_a}: {len(a['bibcodes'])} docs, h-index {a['h_index']}, {a['total_citations']} total citations")
    print(f"  {name_b}: {len(b['bibcodes'])} docs, h-index {b['h_index']}, {b['total_citations']} total citations")

    only_a = a["bibcodes"] - b["bibcodes"]
    only_b = b["bibcodes"] - a["bibcodes"]
    if only_a:
        print(f"\n  In {name_a} only ({len(only_a)}): {sorted(only_a)}")
    if only_b:
        print(f"  In {name_b} only ({len(only_b)}): {sorted(only_b)}")
    if not only_a and not only_b:
        print("\n  Bibcode sets match exactly.")

    common = a["bibcodes"] & b["bibcodes"]
    cite_diffs = []
    field_diffs = []
    for bc in sorted(common):
        da, db = a["docs"][bc], b["docs"][bc]
        ca, cb = int(da.get("citation_count") or 0), int(db.get("citation_count") or 0)
        if abs(ca - cb) > 2:  # allow small drift from differing indexing lag
            cite_diffs.append((bc, ca, cb))
        for field in ("title", "pub", "volume", "doctype"):
            va, vb = da.get(field), db.get(field)
            if va != vb:
                field_diffs.append((bc, field, va, vb))

    if cite_diffs:
        print(f"\n  Citation-count mismatches (>2 apart) for {len(cite_diffs)} papers:")
        for bc, ca, cb in cite_diffs:
            print(f"    {bc}: {name_a}={ca}  {name_b}={cb}")
    else:
        print("\n  Citation counts agree (within +/-2) for all shared papers.")

    if field_diffs:
        print(f"\n  Metadata field mismatches ({len(field_diffs)}):")
        for bc, field, va, vb in field_diffs:
            print(f"    {bc} [{field}]: {name_a}={va!r}  {name_b}={vb!r}")
    else:
        print("  No title/journal/volume/doctype mismatches for shared papers.")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["ads", "scix"],
                         help="Force a single provider instead of trying ADS then falling back to Sci-X.")
    parser.add_argument("--compare", action="store_true",
                         help="Fetch from both ADS and Sci-X and print a diagnostic diff; writes nothing.")
    args = parser.parse_args()

    if args.compare:
        compare_providers("ads", "scix")
        sys.exit(0)

    order = [args.provider] if args.provider else PROVIDER_PRIORITY
    provider_name, bibcodes, docs, h_index, total_citations = fetch_with_fallback(order)
    print(f"Using provider: {provider_name}")

    articles = [d for d in docs if categorise(d) == "article"]
    first_author_docs = [d for d in articles if is_first_author(d)]
    other_docs         = [d for d in articles if not is_first_author(d)]
    print(f"  {len(first_author_docs)} first-author articles, {len(other_docs)} other articles")

    first_block = build_block(first_author_docs)
    other_block = build_block(other_docs)

    for tex_file in TEX_FILES:
        if not tex_file.exists():
            print(f"[skip] {tex_file} does not exist")
            continue
        text = tex_file.read_text(encoding="utf-8")
        new_text = replace_between(text, START_FIRST, END_FIRST, first_block)
        new_text = replace_between(new_text, START_OTHER, END_OTHER, other_block)
        new_text = update_summary_stats(new_text, h_index, total_citations, len(first_author_docs))
        if new_text != text:
            tex_file.write_text(new_text, encoding="utf-8")
            print(f"Written -> {tex_file}")
        else:
            print(f"No change -> {tex_file}")
