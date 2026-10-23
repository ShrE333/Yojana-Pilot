"""Walk an official site to its scheme pages, then extract each scheme.

Most Indian government sites run on the same platform (S3WaaS), where every scheme lives at
/<lang>/scheme/<name>/ and they are all listed on /<lang>/schemes/. So instead of guessing our way
down from the homepage, we go straight to those listing pages and follow their pagination.

    SiteCrawler(discoverer).crawl(source)      # source = one entry of sources_*.resolved.yaml

Pages are classified in code, before any LLM call:
  listing  -> collect its scheme links and its next pages
  scheme   -> send to the extractor
  neither  -> dropped for free (a fifth of fetched pages, which used to cost tokens)
"""
from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from .fetch import FetchError, Page, host_of
from .source_map import scheme_link_words

# A scheme's own page: /en/scheme/lek-ladki-yojana/ but not the listing /en/schemes/
SCHEME_URL = re.compile(r"/(?:scheme|yojana|yojna)/[^/]{4,}/?$", re.I)
LISTING_URL = re.compile(r"/(?:schemes|yojanas|all-schemes)/?(?:page/\d+/?)?(?:\?|$)", re.I)   # also page 2, 3 ... of a listing
PAGE_LINK = re.compile(r"(?:/page/\d+|[?&]page=\d+|[?&]paged=\d+)", re.I)
LISTING_PATHS = ["/en/schemes/", "/mr/schemes/", "/schemes/", "/hi/schemes/"]

# Words that show a page states who may apply and what they get. Checked before the LLM is called.
ELIGIBILITY_WORDS = ["eligib", "beneficiar", "who can apply", "criteria", "income limit", "age limit",
                     "documents required", "how to apply", "benefit", "assistance", "subsidy", "grant",
                     "पात्रता", "लाभार्थी", "अर्ज", "उत्पन्न", "कागदपत्र", "लाभ", "अनुदान",
                     "पात्र", "आवेदन", "दस्तावेज"]
MIN_SCHEME_TEXT = 400
LISTING_MIN_LINKS = 5
SKIP_EXT = (".jpg", ".jpeg", ".png", ".gif", ".zip", ".doc", ".docx", ".xls", ".xlsx", ".mp4", ".mp3")
# asset folders: a link there is a picture or a stylesheet, never a scheme page
JUNK_PATH = re.compile(r"/(?:images?|img|assets|css|js|fonts|thumbnails?|gallery|wp-content|wp-includes|static)/", re.I)


@dataclass
class UrlMap:
    """What the cheap pass found on one site: no LLM was called."""
    site: str
    pages_fetched: int = 0
    listings: int = 0
    scheme_urls: list = field(default_factory=list)
    errors: list = field(default_factory=list)


@dataclass
class CrawlReport:
    site: str
    pages_fetched: int = 0
    listings: int = 0
    candidates: int = 0
    prefiltered: int = 0          # dropped in code, before any LLM call
    schemes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def looks_like_scheme(text: str, url: str, words: list[str]) -> bool:
    hay = f"{text} {url}".lower()
    return any(w.lower() in hay for w in words)


def worth_extracting(page: Page) -> bool:
    """Does this page actually describe a scheme? Checked in code so we spend no tokens on
    menus, news items and contact pages."""
    if len(page.text) < MIN_SCHEME_TEXT:
        return False
    low = page.text.lower()
    return sum(w in low for w in ELIGIBILITY_WORDS) >= 2


def classify(url: str, links: list[str], depth: int) -> str:
    if SCHEME_URL.search(url):
        return "scheme"
    if LISTING_URL.search(url) or depth == 0 or len(links) >= LISTING_MIN_LINKS:
        return "listing"
    return "scheme"                       # a leaf page that still looked like a scheme link


def scheme_links(links: list[list[str]], site_host: str, words: list[str]) -> list[str]:
    out = []
    for text, url in links:
        if host_of(url) != site_host or url.lower().endswith(SKIP_EXT) or JUNK_PATH.search(url):
            continue
        if SCHEME_URL.search(url) or looks_like_scheme(text, url, words):
            out.append(url)
    return list(dict.fromkeys(out))


def next_pages(links: list[list[str]], site_host: str, current: str) -> list[str]:
    """Pagination links on a listing page ("2", "Next", /page/3/)."""
    out = []
    for _, url in links:
        if host_of(url) == site_host and PAGE_LINK.search(url) and url != current:
            out.append(url)
    return list(dict.fromkeys(out))[:5]


def find_scheme_urls(source: dict, fetcher, max_pages: int = 30, max_depth: int = 3,
                     words: list[str] | None = None, log=lambda m: None) -> UrlMap:
    """Phase 1: walk the site's listing pages and collect every scheme page URL.
    Only fetches pages; never calls the LLM, so it is cheap enough to run over every site first."""
    words = words or scheme_link_words()
    host = host_of(source["start_url"])
    found = UrlMap(site=host)
    starts = start_points(source["start_url"])
    queue: deque[tuple[str, int]] = deque((u, 0) for u in starts)
    seen = {u for u, _ in queue}
    while queue and found.pages_fetched < max_pages:
        url, depth = queue.popleft()
        try:
            page = fetcher.fetch(url)
        except FetchError as e:
            found.errors.append(f"{url}: {e}")
            continue
        found.pages_fetched += 1
        links = scheme_links(page.links, host, words)
        if classify(url, links, depth) == "scheme":
            found.scheme_urls.append(url)
            continue
        found.listings += 1
        for link in links:
            if SCHEME_URL.search(link):
                if link not in seen:
                    seen.add(link)
                    found.scheme_urls.append(link)      # a detail page: no need to fetch it now
            elif depth < max_depth and link not in seen:
                seen.add(link)
                queue.append((link, depth + 1))
        if depth < max_depth:
            for nxt in next_pages(page.links, host, url):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append((nxt, depth + 1))
    found.scheme_urls = list(dict.fromkeys(found.scheme_urls))
    return found


def start_points(start_url: str) -> list[str]:
    """The homepage plus the platform's standard scheme-listing paths."""
    p = urlparse(start_url)
    base = f"{p.scheme}://{p.netloc}"
    return list(dict.fromkeys([start_url] + [urljoin(base, path) for path in LISTING_PATHS]))


class SiteCrawler:
    def __init__(self, discoverer, log=lambda m: None, words: list[str] | None = None):
        self.d = discoverer
        self.log = log
        self.words = words or scheme_link_words()

    def crawl(self, source: dict, max_pages: int = 25, max_depth: int = 3) -> CrawlReport:
        host = host_of(source["start_url"])
        rep = CrawlReport(site=host)
        meta = {"agency_type": source["agency_type"], "agency_name": source["name"],
                "district": source.get("district")}
        queue: deque[tuple[str, int]] = deque((u, 0) for u in start_points(source["start_url"]))
        seen = {u for u, _ in queue}
        from discovery.discover import DiscoveryReport
        drep = DiscoveryReport()
        while queue and rep.pages_fetched < max_pages:
            url, depth = queue.popleft()
            try:
                page = self.d.fetcher.fetch(url)
            except FetchError as e:
                rep.errors.append(f"{url}: {e}")
                continue
            rep.pages_fetched += 1
            links = scheme_links(page.links, host, self.words)
            if classify(url, links, depth) == "listing":
                rep.listings += 1
                if depth < max_depth:
                    for link in links + next_pages(page.links, host, url):
                        if link not in seen:
                            seen.add(link)
                            queue.append((link, depth + 1))
                continue
            if not worth_extracting(page):
                rep.prefiltered += 1
                self.d._remember(url, "not_scheme", note="no eligibility wording")
                continue
            rep.candidates += 1
            sid = self.d.process_url(url, drep, extra=meta)
            if sid:
                rep.schemes.append(sid)
        for sid in drep.new_schemes:
            self.log(f"  + {sid}")
        return rep
