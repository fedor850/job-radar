#!/usr/bin/env python3
"""
CV & cover-letter builder: one consistent, ATS-friendly design for every application.

Builds a .docx from a JSON spec using the paragraph/table prototypes of `resume_template.docx` (fonts, spacing,
rules and bullets are copied from it), converts it to PDF with LibreOffice and checks that the CV fits on ONE page.

Usage:
  python3 cv_builder.py cv  spec.json   out_dir/  [--name "Alex_Example_CV_Company"]
  python3 cv_builder.py cl  letter.json out_dir/  [--name "Alex_Example_Cover_Letter_Company"]
  python3 cv_builder.py check spec.json            (only run the content checks, no files)

Exit codes: 0 ok · 2 usage · 3 longer than 1 page · 4 content rule broken (see "RULE:" lines).

Your personal defaults live in `owner.json` next to this script (git-ignored; template: owner.example.json):
name, contact line, file-name prefix and your own content rules (regex → message) — e.g. "never put the LinkedIn
URL in the CV", "never state the grant amount". RULE = hard error (exit 4), WARN = check it. A spec can override
"name" and "contact". PDF export runs LibreOffice in a private temp folder (own profile), so it works even while
LibreOffice is open and leaves no lock files in the output folder.

CV spec (JSON) — see examples/cv_example.json:
{
  "headline": "Finance & Business Analytics",          # line under the name (tailor per role)
  "contact": "City, Country | you@example.com | +00 …", # optional, default from owner.json
  "page_size": "A4",                                   # "A4" (default) or "Letter"
  "sections": [
    {"title": "SUMMARY", "type": "text", "text": "..."},
    {"title": "SKILLS", "type": "lines", "lines": [{"label": "Sales:", "text": "..."}]},
    {"title": "EXPERIENCE", "type": "entries", "entries": [
        {"org": "ACME GMBH", "dates": "Jun. 2025 – Feb. 2026", "role": "Account Manager",
         "location": "Vienna, Austria", "bullets": ["...", {"label": "GPA:", "text": "..."}]}]},
    {"title": "CERTIFICATIONS & LANGUAGES", "type": "bullets", "bullets": [{"label": "Languages:", "text": "..."}]}
  ]
}
Cover-letter spec (JSON) — see examples/cover_letter_example.json:
{
  "date": "24 October 2026",                 # optional, default = today
  "recipient": ["Hiring Team", "Example Bank AG", "Main Street 1, 1010 Vienna"],
  "subject": "Application: Finance Intern (20 h/week)",
  "salutation": "Dear Hiring Team,",
  "paragraphs": ["...", "..."],
  "closing": "Kind regards,"
}
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Emu

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "resume_template.docx"
OWNER_FILE = Path(os.environ.get("CV_OWNER", HERE / "owner.json"))


def _owner() -> dict:
    """Personal defaults (owner.json, git-ignored). Missing file → neutral placeholders."""
    try:
        return json.loads(OWNER_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


OWNER = _owner()
FULL_NAME = OWNER.get("name", "Your Name")
NAME = FULL_NAME.upper()
DEFAULT_CONTACT = OWNER.get("contact", "City, Country | you@example.com | +00 000 000 000")
FILE_PREFIX = OWNER.get("file_prefix", re.sub(r"\W+", "_", FULL_NAME).strip("_") or "CV")

# indices of prototype elements inside the template body (see resume_template.docx)
P_NAME, P_HEADLINE, P_CONTACT = 0, 1, 2
P_GAP_AFTER_HEADER = (3, 4)
P_HEADING, T_RULE, P_SPACER_TINY = 5, 6, 7
P_TEXT = 8
P_SECTION_END = (9, 10)
P_LABEL_LINE = 14
T_LEFT_RIGHT_BOLD = 20  # ORG | dates
T_LEFT_RIGHT_ITALIC = 22  # role | location
P_BULLET = 24
P_BETWEEN_ENTRIES = 29
P_BULLET_LABEL = 60


def _proto(doc) -> list:
    return [copy.deepcopy(el) for el in doc.element.body.iterchildren() if not el.tag.endswith("sectPr")]


def _runs(p):
    return p.findall(qn("w:r"))


def _set_text_runs(p, segments: list[tuple[str, bool | None, bool | None]]):
    """Replace runs of paragraph `p` with segments (text, bold, italic). Formatting is cloned from the first run."""
    runs = _runs(p)
    base = runs[0] if runs else None
    for r in runs:
        p.remove(r)
    for text, bold, italic in segments:
        r = copy.deepcopy(base) if base is not None else p.makeelement(qn("w:r"), {})
        for t in r.findall(qn("w:t")):
            r.remove(t)
        rpr = r.find(qn("w:rPr"))
        if rpr is None:
            rpr = r.makeelement(qn("w:rPr"), {})
            r.insert(0, rpr)
        for tag, on in (("w:b", bold), ("w:bCs", bold), ("w:i", italic), ("w:iCs", italic)):
            if on is None:
                continue
            el = rpr.find(qn(tag))
            if on and el is None:
                rpr.append(rpr.makeelement(qn(tag), {}))
            elif not on and el is not None:
                rpr.remove(el)
        t = r.makeelement(qn("w:t"), {})
        t.text = text
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        r.append(t)
        p.append(r)
    return p


def _set_cell(tbl, col: int, text: str):
    cells = tbl.findall(f".//{qn('w:tc')}")
    p = cells[col].find(qn("w:p"))
    runs = _runs(p)
    if runs:
        _set_text_runs(p, [(text, None, None)])
    else:  # empty cell: borrow formatting of the other cell
        other = cells[1 - col].find(qn("w:p"))
        r = copy.deepcopy(_runs(other)[0])
        p.append(r)
        _set_text_runs(p, [(text, None, None)])
    return tbl


def _segments(item) -> list[tuple[str, bool | None, bool | None]]:
    if isinstance(item, dict):
        segs = []
        if item.get("label"):
            segs.append((item["label"].rstrip() + " ", True, False))
        segs.append((item.get("text", ""), False, False))
        return segs
    return [(str(item), False, False)]


def _apply_page(doc, size: str):
    sec = doc.sections[0]
    if size.upper() == "A4":
        sec.page_width, sec.page_height = Emu(7560310), Emu(10692130)
        # keep the same text width as the original Letter layout (≈ 7.6 in)
        sec.left_margin = sec.right_margin = Emu(int((7560310 - 6949440) / 2))


def build_cv(spec: dict, out_docx: Path) -> Path:
    doc = Document(str(TEMPLATE))
    P = _proto(doc)
    body = doc.element.body
    sect = body.find(qn("w:sectPr"))
    for el in list(body):
        if el is not sect:
            body.remove(el)

    def add(el):
        sect.addprevious(el)

    add(_set_text_runs(copy.deepcopy(P[P_NAME]), [(spec.get("name", FULL_NAME).upper(), None, None)]))
    if spec.get("headline"):
        add(_set_text_runs(copy.deepcopy(P[P_HEADLINE]), [(spec["headline"], None, None)]))
    add(_set_text_runs(copy.deepcopy(P[P_CONTACT]), [(spec.get("contact", DEFAULT_CONTACT), None, None)]))
    for i in P_GAP_AFTER_HEADER:
        add(copy.deepcopy(P[i]))

    for s_idx, sec in enumerate(spec["sections"]):
        add(_set_text_runs(copy.deepcopy(P[P_HEADING]), [(sec["title"].upper(), None, None)]))
        add(copy.deepcopy(P[T_RULE]))
        add(copy.deepcopy(P[P_SPACER_TINY]))
        typ = sec.get("type", "bullets")
        if typ == "text":
            add(_set_text_runs(copy.deepcopy(P[P_TEXT]), [(sec["text"], None, None)]))
        elif typ == "lines":
            for line in sec["lines"]:
                add(_set_text_runs(copy.deepcopy(P[P_LABEL_LINE]), _segments(line)))
        elif typ == "bullets":
            for b in sec["bullets"]:
                proto = P[P_BULLET_LABEL] if isinstance(b, dict) else P[P_BULLET]
                add(_set_text_runs(copy.deepcopy(proto), _segments(b)))
        elif typ == "entries":
            for e_idx, e in enumerate(sec["entries"]):
                if e_idx:
                    add(copy.deepcopy(P[P_BETWEEN_ENTRIES]))
                t1 = copy.deepcopy(P[T_LEFT_RIGHT_BOLD])
                _set_cell(t1, 0, e.get("org", ""))
                _set_cell(t1, 1, e.get("dates", ""))
                add(t1)
                add(copy.deepcopy(P[P_SPACER_TINY]))
                if e.get("role") or e.get("location"):
                    t2 = copy.deepcopy(P[T_LEFT_RIGHT_ITALIC])
                    _set_cell(t2, 0, e.get("role", ""))
                    _set_cell(t2, 1, e.get("location", ""))
                    add(t2)
                    add(copy.deepcopy(P[P_SPACER_TINY]))
                for b in e.get("bullets", []):
                    proto = P[P_BULLET_LABEL] if isinstance(b, dict) else P[P_BULLET]
                    add(_set_text_runs(copy.deepcopy(proto), _segments(b)))
        else:
            raise ValueError(f"unknown section type {typ}")
        if s_idx < len(spec["sections"]) - 1:
            for i in P_SECTION_END:
                add(copy.deepcopy(P[i]))

    _apply_page(doc, spec.get("page_size", "A4"))
    doc.core_properties.author = spec.get("name", FULL_NAME)
    doc.core_properties.title = spec.get("doc_title", f"{spec.get('name', FULL_NAME)} — CV")
    out_docx.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_docx))
    return out_docx


def build_cover_letter(spec: dict, out_docx: Path) -> Path:
    doc = Document(str(TEMPLATE))
    P = _proto(doc)
    body = doc.element.body
    sect = body.find(qn("w:sectPr"))
    for el in list(body):
        if el is not sect:
            body.remove(el)

    def add(el):
        sect.addprevious(el)

    def para(text_segments, size_half_points: int = 21, after: int = 120, align: str | None = None):
        p = copy.deepcopy(P[P_TEXT])
        _set_text_runs(p, text_segments)
        for r in _runs(p):
            rpr = r.find(qn("w:rPr"))
            for tag in ("w:sz", "w:szCs"):
                el = rpr.find(qn(tag))
                if el is None:
                    el = rpr.makeelement(qn(tag), {})
                    rpr.append(el)
                el.set(qn("w:val"), str(size_half_points))
        ppr = p.find(qn("w:pPr"))
        sp = ppr.find(qn("w:spacing"))
        sp.set(qn("w:after"), str(after))
        sp.set(qn("w:line"), "264")
        if align:
            jc = ppr.makeelement(qn("w:jc"), {qn("w:val"): align})
            ppr.append(jc)
        return p

    add(_set_text_runs(copy.deepcopy(P[P_NAME]), [(spec.get("name", FULL_NAME).upper(), None, None)]))
    add(_set_text_runs(copy.deepcopy(P[P_CONTACT]), [(spec.get("contact", DEFAULT_CONTACT), None, None)]))
    add(copy.deepcopy(P[T_RULE]))
    add(para([("", False, False)], after=120))
    today = date.today()
    add(para([(spec.get("date") or f"{today.day} {today:%B %Y}", False, False)], after=200, align="right"))
    for i, line in enumerate(spec.get("recipient", [])):
        add(para([(line, i == 0, False)], after=0))
    add(para([("", False, False)], after=60))
    if spec.get("subject"):
        add(para([(spec["subject"], True, False)], after=200))
    add(para([(spec.get("salutation", "Dear Hiring Team,"), False, False)], after=140))
    for text in spec["paragraphs"]:
        add(para([(text, False, False)], after=140, align="both"))
    add(para([(spec.get("closing", "Kind regards,"), False, False)], after=40))
    add(para([(spec.get("name", FULL_NAME), True, False)], after=0))
    _apply_page(doc, spec.get("page_size", "A4"))
    sec = doc.sections[0]
    sec.top_margin = sec.bottom_margin = Emu(720000)
    doc.core_properties.author = spec.get("name", FULL_NAME)
    doc.core_properties.title = spec.get("doc_title", f"{spec.get('name', FULL_NAME)} — Cover Letter")
    out_docx.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_docx))
    return out_docx




def to_pdf(docx: Path) -> Path:
    """DOCX → PDF with LibreOffice, run in a private temp folder with its own user profile.

    Converting directly inside the output folder leaves .~lock.*# and lu*.tmp files there (on synced/mounted
    folder they cannot be cleaned up) and silently does nothing when a LibreOffice window is already open."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise SystemExit("LibreOffice not found — cannot export PDF")
    target = docx.with_suffix(".pdf")
    with tempfile.TemporaryDirectory(prefix="cvb_") as tmp:
        tmp_path = Path(tmp)
        src = tmp_path / docx.name
        shutil.copy2(docx, src)
        profile = (tmp_path / "lo_profile").resolve().as_uri()
        subprocess.run([soffice, f"-env:UserInstallation={profile}", "--headless", "--norestore",
                        "--convert-to", "pdf", "--outdir", str(tmp_path), str(src)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
        produced = src.with_suffix(".pdf")
        if not produced.exists() or produced.stat().st_size == 0:
            raise SystemExit("LibreOffice did not produce a PDF — check the .docx")
        shutil.copyfile(produced, target)  # copyfile (not copy2): the PDF gets a fresh timestamp
    return target


def pdf_pages(pdf: Path) -> int:
    try:  # exact count when pypdf is available
        from pypdf import PdfReader
        return len(PdfReader(str(pdf)).pages)
    except Exception:  # noqa: BLE001 — fall back to a byte scan (works for LibreOffice output)
        data = pdf.read_bytes()
        m = re.search(rb"/Type\s*/Pages\b[^>]*?/Count\s+(\d+)", data, re.S)
        return int(m.group(1)) if m else len(re.findall(rb"/Type\s*/Page[^s]", data))


# ------------------------------------------------------------------ content checks (rules from the project)
# Content checks. Your own rules come from owner.json: {"rules": [[regex, message], ...], "warnings": [...]}
# RULE = never OK (exit code 4), WARN = check it. The built-in ones below apply to everybody.
_RULES = [(r, m) for r, m in OWNER.get("rules", [])]
_WARN = [(r, m) for r, m in OWNER.get("warnings", [])] + [
    (r"\b(born|date of birth|geburtsdatum|marital status|familienstand)\b",
     "personal data (date of birth, marital status) is not expected in international CVs — remove unless asked"),
]


def _all_text(spec: dict) -> tuple[list[str], list[str]]:
    """(all strings, CV bullet strings)."""
    texts, bullets = [], []

    def walk(x, in_bullets=False):
        if isinstance(x, str):
            texts.append(x)
            if in_bullets:
                bullets.append(x)
        elif isinstance(x, dict):
            for k, v in x.items():
                walk(v, in_bullets or k == "bullets")
        elif isinstance(x, list):
            for v in x:
                walk(v, in_bullets)
    walk(spec)
    return texts, bullets


def check_spec(spec: dict, kind: str) -> tuple[list[str], list[str]]:
    texts, bullets = _all_text(spec)
    blob = "\n".join(texts)
    errors = [f"RULE: {msg}  ←  “{m.group(0)}”" for pat, msg in _RULES for m in [re.search(pat, blob, re.I)] if m]
    warns = [f"WARN: {msg}" for pat, msg in _WARN if re.search(pat, blob, re.I)]
    if kind == "cv":
        first_person = [b for b in bullets if re.search(r"(^|[\s;:,(])(I|my|me)\b", b)]
        if first_person:
            warns.append(f"WARN: {len(first_person)} CV bullet(s) in first person (\"I/my\") — CV bullets start with a "
                         f"past-tense verb: “{first_person[0][:70]}…”")
        long_b = [b for b in bullets if len(b) > 260]
        if long_b:
            warns.append(f"WARN: {len(long_b)} bullet(s) longer than ~2 lines: “{long_b[0][:60]}…”")
    else:
        words = len(re.findall(r"\b[\w'’-]+\b", " ".join(spec.get("paragraphs", []))))
        if not 200 <= words <= 360:
            warns.append(f"WARN: cover letter body has {words} words (target 250–330)")
        else:
            print(f"Words: {words}")
    return errors, warns


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[1] == "check":
        spec = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        kind = "cl" if "paragraphs" in spec else "cv"
        errors, warns = check_spec(spec, kind)
        print("\n".join(errors + warns) or "OK: no rule violations")
        return 4 if errors else 0
    if len(argv) < 4 or argv[1] not in ("cv", "cl"):
        print(__doc__)
        return 2
    kind, spec_path, out_dir = argv[1], Path(argv[2]), Path(argv[3])
    name = None
    if "--name" in argv:
        i = argv.index("--name")
        if i + 1 >= len(argv):
            print("--name needs a value")
            return 2
        name = argv[i + 1]
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    errors, warns = check_spec(spec, kind)
    default = f"{FILE_PREFIX}_CV" if kind == "cv" else f"{FILE_PREFIX}_Cover_Letter"
    out = out_dir / f"{name or default}.docx"
    (build_cv if kind == "cv" else build_cover_letter)(spec, out)
    pdf = to_pdf(out)
    pages = pdf_pages(pdf)
    print(f"DOCX: {out}\nPDF:  {pdf}\nPages: {pages}")
    for line in errors + warns:
        print(line)
    if pages > 1:
        what = "CV" if kind == "cv" else "Cover letter"
        print(f"WARNING: {what} is longer than one page — shorten bullets/paragraphs or remove the least relevant ones.")
        return 3
    return 4 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
