"""Small helpers: text normalisation, HTML → text, time handling, URL helpers."""
from __future__ import annotations

import html as htmllib
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

VIENNA = ZoneInfo("Europe/Vienna")

# ----------------------------------------------------------------- time


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        s = s.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def local(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(VIENNA)


def human_age(dt: datetime | None, ref: datetime | None = None) -> str:
    """'3 min ago', '2 h ago', '5 days ago' (short)."""
    if dt is None:
        return ""
    ref = ref or now_utc()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    sec = max(0, int((ref - dt).total_seconds()))
    if sec < 90:
        return "just now"
    if sec < 3600:
        return f"{sec // 60} min ago"
    if sec < 86400 * 2:
        return f"{sec // 3600} h ago"
    return f"{sec // 86400} days ago"


_REL_DE = re.compile(r"vor\s+(\d+)\s+(Minute|Minuten|Stunde|Stunden|Tag|Tagen|Woche|Wochen)", re.I)


def parse_relative_age(text: str, ref: datetime | None = None) -> datetime | None:
    """Parse 'Posted 3 Days Ago', 'Posted Today', 'vor 2 Tagen', 'Heute' etc."""
    if not text:
        return None
    ref = ref or now_utc()
    t = text.strip().lower()
    if "today" in t or "heute" in t or "just posted" in t:
        return ref
    if "yesterday" in t or "gestern" in t:
        return ref - timedelta(days=1)
    m = re.search(r"(\d+)\+?\s*(minute|hour|day|week|month)s?", t)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        mult = {"minute": 60, "hour": 3600, "day": 86400, "week": 7 * 86400, "month": 30 * 86400}[unit]
        return ref - timedelta(seconds=n * mult)
    m = _REL_DE.search(text)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        if unit.startswith("minute"):
            return ref - timedelta(minutes=n)
        if unit.startswith("stunde"):
            return ref - timedelta(hours=n)
        if unit.startswith("tag"):
            return ref - timedelta(days=n)
        if unit.startswith("woche"):
            return ref - timedelta(weeks=n)
    return None


# ----------------------------------------------------------------- text

_WS = re.compile(r"[ \t ]+")
_NL = re.compile(r"\n{3,}")
_JUNK_LINES = {"null", "undefined", "none", "nan"}


def html_to_text(html: str | None, max_len: int | None = None) -> str:
    """Convert HTML to readable plain text, keeping list items and paragraphs."""
    if not html:
        return ""
    if "<" not in html and "&" in html:
        html = htmllib.unescape(html)
    if "&lt;" in html and "<" not in html.replace("&lt;", ""):
        html = htmllib.unescape(html)
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "noscript", "svg", "iframe"]):
        t.decompose()
    for li in soup.find_all("li"):
        li.insert_before("\n• ")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for tag in soup.find_all(["p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "ul", "ol"]):
        tag.insert_after("\n")
    text = soup.get_text("")
    text = "\n".join(_WS.sub(" ", line).strip() for line in text.splitlines())
    text = "\n".join(l for l in text.split("\n") if l.lower() not in _JUNK_LINES)  # "null" placeholders (XING)
    text = _NL.sub("\n\n", text).strip()
    if max_len and len(text) > max_len:
        text = text[:max_len].rsplit(" ", 1)[0] + " …"
    return text


def clip(text: str, n: int) -> str:
    text = text or ""
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def esc(text: str | None) -> str:
    """Escape for Telegram HTML parse mode."""
    return htmllib.escape(text or "", quote=False)


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


_GENDER = re.compile(
    r"\((?:[^)]*?(?:m\s*/\s*w|w\s*/\s*m|m\s*/\s*f|f\s*/\s*m|all\s*genders?|gn\*?|m\s*/\s*d|w\s*/\s*d|d\s*/\s*m|"
    r"x\s*/\s*f|f\s*/\s*x|w\s*/\s*x|m\s*/\s*x|all\s*humans|alle)[^)]*?)\)",
    re.I,
)
_LEGAL = {
    "gmbh", "ag", "kg", "og", "se", "mbh", "ges", "m.b.h", "mbh.", "co", "co.", "inc", "inc.", "ltd", "ltd.",
    "llc", "plc", "sa", "s.a.", "bv", "b.v.", "nv", "e.u.", "eu", "kgaa", "flexco", "gesellschaft", "the",
    "&", "und", "and", "group", "gruppe", "holding", "austria", "österreich", "osterreich", "international",
    "gesmbh", "ges.m.b.h.", "ges.m.b.h", "zweigniederlassung", "wien", "vienna",
}


def norm_title(title: str) -> str:
    t = _GENDER.sub(" ", title or "")
    t = re.sub(r"\b(m|w|d|f|x)\s*/\s*(m|w|d|f|x)(\s*/\s*(m|w|d|f|x))?\b", " ", t, flags=re.I)
    t = strip_accents(t.lower())
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def norm_company(company: str) -> str:
    c = strip_accents((company or "").lower())
    c = re.sub(r"[^a-z0-9&.\s-]+", " ", c)
    tokens = [tok.strip(".-") for tok in c.split()]
    tokens = [tok for tok in tokens if tok and tok not in _LEGAL]
    return " ".join(tokens)


def company_key(company: str) -> str:
    """First significant token of the normalised company name (robust across sources)."""
    n = norm_company(company)
    for tok in n.split():
        if len(tok) >= 2:
            return tok
    return n or (company or "").lower().strip()


def fingerprint(company: str, title: str) -> str:
    return f"{company_key(company)}|{norm_title(title)}"


def slug_candidates(company: str) -> list[str]:
    """Plausible ATS slugs for a company name: 'Erste Digital GmbH' -> ['erstedigital','erste-digital','erste']."""
    n = norm_company(company)
    toks = [re.sub(r"[^a-z0-9]", "", t) for t in n.split()]
    toks = [t for t in toks if t]
    if not toks:
        return []
    cands = ["".join(toks), "-".join(toks), toks[0]]
    if len(toks) >= 2:
        cands.append("".join(toks[:2]))
    seen: list[str] = []
    for c in cands:
        if c and c not in seen and len(c) >= 2:
            seen.append(c)
    return seen


# ----------------------------------------------------------------- urls

_TRACKING = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "trk", "trackingid",
             "refid", "src", "source", "gh_src", "ref", "from"}


def clean_url(url: str) -> str:
    if not url:
        return ""
    try:
        p = urlparse(url.strip())
        q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k.lower() not in _TRACKING]
        return urlunparse((p.scheme or "https", p.netloc, p.path, p.params, urlencode(q), ""))
    except ValueError:
        return url


def host_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except ValueError:
        return ""


JOB_BOARD_HOSTS = (
    "linkedin.com", "indeed.com", "karriere.at", "stepstone", "xing.com", "glassdoor", "hokify", "jobwave",
    "jobs.at", "metajob", "monster", "zbp.at", "willhaben", "derstandard", "kununu.com/jobs", "jobware",
    "t.gohiring.com", "recruitics.com", "iventajobdata", "jobsexperts", "jobs.ams.at", "talent.com", "jooble",
    "careerjet", "adzuna", "joblift", "jobrapido", "jobted",
)


def is_job_board(url: str) -> bool:
    h = host_of(url)
    return any(b in h or b in (url or "") for b in JOB_BOARD_HOSTS)


# ----------------------------------------------------------------- location

_DEFAULT_HOME = (
    r"\b(vienna|wien|1\d{3}\s*wien|wiener\s+neudorf|schwechat|klosterneuburg|m[oö]dling|v[oö]sendorf|"
    r"brunn\s+am\s+gebirge|perchtoldsdorf|gerasdorf|purkersdorf|langenzersdorf|leopoldsdorf|"
    r"innere\s+stadt|donaustadt|floridsdorf|favoriten|leopoldstadt|landstra[sß]e|brigittenau|"
    r"d[oö]bling|meidling|ottakring|hernals|w[aä]hring|penzing|liesing|simmering|margareten|"
    r"mariahilf|neubau|josefstadt|alsergrund|wieden|rudolfsheim)\b"
)


class _HomeRegion:
    """The search area ("home"): a regex over location strings. Default = Vienna and its suburbs/districts;
    replaced at start-up from config.yaml → location.home_patterns (see set_home_patterns). Behaves like a compiled
    pattern (.search), so modules can keep `from .util import VIENNA_RE`."""

    def __init__(self, pattern: str):
        self.set(pattern)

    def set(self, pattern: str) -> None:
        self._re = re.compile(pattern, re.I)

    def search(self, s: str):
        return self._re.search(s)

    @property
    def pattern(self) -> str:
        return self._re.pattern


HOME_RE = _HomeRegion(_DEFAULT_HOME)
VIENNA_RE = HOME_RE  # historical name, used across the sources


def set_home_patterns(patterns: list[str] | None, country: list[str] | None = None) -> None:
    """location.home_patterns: list of regexes (city, suburbs, districts, postcodes) — empty → Vienna default.
    location.country_patterns: words that mean "this country" in a location string (for remote-in-country)."""
    HOME_RE.set(r"\b(" + "|".join(f"(?:{p})" for p in patterns) + r")\b" if patterns else _DEFAULT_HOME)
    if country:
        COUNTRY_RE.set(r"\b(" + "|".join(f"(?:{p})" for p in country) + r")\b")
REMOTE_RE = re.compile(r"\b(remote|home\s*office|homeoffice|work from home|telework|anywhere)\b", re.I)
COUNTRY_RE = _HomeRegion(r"\b(austria|österreich|osterreich|at)\b")
AUSTRIA_RE = COUNTRY_RE  # historical name


def location_ok(location: str, text: str = "") -> bool:
    loc = location or ""
    if VIENNA_RE.search(loc):
        return True
    if REMOTE_RE.search(loc) and (AUSTRIA_RE.search(loc) or "europe" in loc.lower() or "eu" in loc.lower().split()):
        return True
    if not loc.strip() and text and VIENNA_RE.search(text[:3000]):
        return True
    return False


# ---------------------------------------------------------------- language of a posting
_DE_WORDS = set("""der die das und ist nicht mit für fuer auf wir du sie ihr ihre ihren ihrem unser unsere unseren
unserem eine einen einem einer bei von zu zur zum im den dem des sind werden wird als auch oder nach über ueber bzw sowie
deine dein deinen deinem dich dir uns bist hast kannst möchtest freuen bieten suchen erwartet aufgaben mitbringst
kenntnisse erfahrung idealerweise bereich unternehmen team arbeitszeit stunden woche bewerbung bewerben""".split())
_EN_WORDS = set("""the and is are with for on we you your our a to of at by from as be this that or have has
who what which they their us can would should about into within across experience skills team role apply job
responsibilities requirements looking offer work working""".split())
_WORD = re.compile(r"[a-zäöüß]+", re.I)


def detect_lang(text: str) -> tuple[str, int, int]:
    """Very simple stop-word based language guess for German vs English job ads.

    Returns (lang, de_hits, en_hits); lang is 'de' only when German clearly dominates (≥ 80 % of hits and
    at least 25 German hits), 'en' when English clearly dominates, otherwise 'mixed' / 'unknown'.
    Overlapping words ("team") are counted for both, which only makes the verdict more conservative.
    """
    words = [w.lower() for w in _WORD.findall(text or "")]
    de = sum(1 for w in words if w in _DE_WORDS)
    en = sum(1 for w in words if w in _EN_WORDS)
    tot = de + en
    if tot < 25:
        return "unknown", de, en
    if de >= 25 and de / tot >= 0.8:
        return "de", de, en
    if en >= 25 and en / tot >= 0.8:
        return "en", de, en
    return "mixed", de, en


def confident_english(text: str) -> bool:
    """True only when `text` is clearly English. A short snippet (too few stop words for detect_lang to judge)
    counts as English only with several English and (almost) no German function words."""
    lang, de, en = detect_lang(text)
    if lang == "en":
        return True
    return lang == "unknown" and en >= 6 and de * 3 <= en


def quote_in_text(quote: str, text: str) -> bool:
    """True if `quote` (copied by the model) really appears in `text` — tolerant to markdown, case and spacing."""
    def norm(s: str) -> list[str]:
        return [w.lower() for w in re.findall(r"[0-9a-zäöüß]+", s or "", re.I)]
    q = norm(quote)
    if len(q) < 2:
        return False
    t = norm(text)
    tj = " " + " ".join(t) + " "
    if " " + " ".join(q) + " " in tj:
        return True
    ts = set(t)
    hits = sum(1 for w in q if w in ts)
    return hits / len(q) >= 0.8


# ---------------------------------------------------------------- evidence checks (30.10.2026)
_FOLD = str.maketrans({"ä": "a", "ö": "o", "ü": "u", "ß": "s", "é": "e", "è": "e", "á": "a", "à": "a", "ó": "o",
                       "í": "i", "ú": "u", "ç": "c", "ñ": "n"})


def _tokens(s: str) -> list[str]:
    s = (s or "").lower().replace("ae", "a").replace("oe", "o").replace("ue", "u").replace("ss", "s")
    return re.findall(r"[a-z0-9]+", s.translate(_FOLD))


def strict_quote(quote: str, text: str) -> bool:
    """True only if `quote` really is a contiguous passage of `text` (case, punctuation, markdown, umlaut spelling and
    line breaks ignored). '…' / '...' may join several passages — each must be found. A single word counts only if it
    has ≥ 5 letters. Passages of ≥ 8 words tolerate ≤ 10 % differing words (typos, a dropped article) — nothing more.
    Much stricter than quote_in_text (which also accepts scattered words)."""
    if not quote or not text:
        return False
    t = _tokens(text)
    if not t:
        return False
    joined = " " + " ".join(t) + " "
    segs = [s for s in re.split(r"\s*(?:…|\.\.\.+|\[\.\.\.\]|\(\.\.\.\))\s*", quote) if s.strip()]
    if not segs:
        return False
    for seg in segs:
        q = _tokens(seg)
        if not q or (len(q) == 1 and len(q[0]) < 5):
            return False
        if " " + " ".join(q) + " " in joined:
            continue
        if len(q) < 8:
            return False
        n, ok = len(q), False
        allowed = max(1, n // 10)
        first = set(q[:3])
        for i in range(0, len(t) - n + 1):
            if t[i] not in first and t[i + 1] not in first:
                continue
            if sum(1 for a, b in zip(q, t[i:i + n]) if a != b) <= allowed:
                ok = True
                break
        if not ok:
            return False
    return True


_HOURS_NUM = re.compile(r"(\d{1,2})(?:[,.]\d)?\s*(?:[-–—]|bis|to)?\s*(\d{1,2})?(?:[,.]\d)?\s*"
                        r"-?\s*(?:h\b|std\b|stunden|wochenstunden|hours?\b|hrs)", re.I)


def weekly_hours_range(s: str) -> tuple[int, int] | None:
    """'20 h/week' → (20, 20); '20–30 h' → (20, 30); 'ab 25 Stunden' → (25, 25); None if no hours number."""
    best = None
    for m in _HOURS_NUM.finditer(s or ""):
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        if not (4 <= a <= 60):
            continue
        lo, hi = min(a, b), max(a, b)
        best = (lo, hi) if best is None else (min(best[0], lo), max(best[1], hi))
    return best
