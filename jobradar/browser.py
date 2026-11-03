"""Headless-browser reader for postings that a plain HTTP request cannot read in full.

Some sites render the ad with JavaScript or keep the rest of it behind a "show more" / "mehr anzeigen" button.
`render(url)` opens the page in headless Chromium (already installed for the WU ZBP source), presses such buttons
and returns the rendered HTML and text. One browser at a time (a launch costs ~100 MB and ~5 s); returns None
when the browser is not available or the page does not open. Aggregators that block data-centre IPs
(Jooble, Careerjet, Adzuna redirects, LinkedIn, Indeed) are skipped: a browser does not get past their checks.
"""
from __future__ import annotations

import logging
import threading

log = logging.getLogger("jobradar.browser")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0.0.0 Safari/537.36")
SKIP_HOSTS = ("linkedin.com", "jooble.org", "careerjet.", "jobviewtrack.com", "adzuna.", "indeed.com",
              "glassdoor.", "stepstone.", "google.", "facebook.com")
_LOCK = threading.Lock()

# In-page script: press every visible "show more" style control (twice: pressing one can reveal another)
_EXPAND_JS = r"""
() => {
  const label = new RegExp('^(' + [
    'show more', 'see more', 'read more', 'view more', 'show full (description|job|text)', 'more details',
    'full description', 'mehr anzeigen', 'mehr lesen', 'weiterlesen', 'weiter lesen', 'mehr erfahren',
    'alles anzeigen', 'vollst[aä]ndig anzeigen', 'voir plus', 'lire la suite', 'mehr', 'more'
  ].join('|') + ')\\s*[›»▼▾⌄…]*$', 'i');
  const skip = /cookie|consent|privacy|datenschutz|newsletter|login|anmelden|werbe|advert|partner|store and|personali[sz]ed/i;
  let n = 0;
  for (let round = 0; round < 2; round++) {
    const els = document.querySelectorAll('button, summary, [role="button"], [aria-expanded="false"], a[href^="#"], a[href^="javascript"]');
    for (const el of els) {
      const t = ((el.innerText || '') + ' ' + (el.getAttribute('aria-label') || '')).replace(/\s+/g, ' ').trim();
      if (!t || t.length > 40 || skip.test(t) || !label.test(t)) continue;
      if (el.closest('[class*="cookie" i], [id*="cookie" i], [class*="consent" i], [id*="consent" i], [role="dialog"]')) continue;
      if (!el.offsetParent) continue;
      try { el.click(); n++; } catch (e) {}
      if (n >= 8) return n;
    }
  }
  return n;
}
"""


def available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


def render(url: str, timeout: int = 35) -> dict | None:
    """Open `url`, press "show more" buttons, return {"url", "html", "text", "clicked"} or None."""
    if not url or not url.startswith("http") or any(h in url.lower() for h in SKIP_HOSTS) or not available():
        return None
    from playwright.sync_api import sync_playwright

    with _LOCK:
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
                try:
                    ctx = browser.new_context(locale="de-AT", user_agent=UA)
                    pg = ctx.new_page()
                    pg.set_default_timeout(10000)
                    pg.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
                    try:
                        pg.wait_for_load_state("networkidle", timeout=7000)
                    except Exception:  # noqa: BLE001 - pages with a permanent connection never go idle
                        pg.wait_for_timeout(1500)
                    last, same = -1, 0
                    for _ in range(12):  # single-page apps: wait until the drawn text stops growing
                        n = int(pg.evaluate("document.body ? document.body.innerText.length : 0") or 0)
                        same = same + 1 if n == last and n > 600 else 0
                        if same >= 2:
                            break
                        last = n
                        pg.wait_for_timeout(1000)
                    clicked = int(pg.evaluate(_EXPAND_JS) or 0)
                    if clicked:
                        pg.wait_for_timeout(800)
                    return {"url": pg.url, "html": pg.content(), "text": pg.inner_text("body"), "clicked": clicked}
                finally:
                    browser.close()
        except Exception as e:  # noqa: BLE001
            log.info("browser read of %s failed: %s", url[:100], str(e)[:120])
            return None
