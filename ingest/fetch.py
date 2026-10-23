"""Polite fetcher for official scheme pages: robots.txt, rate limit, disk cache, HTML/PDF to text.

    fetcher = Fetcher()
    page = fetcher.fetch("https://agriwelfare.gov.in/...")   # -> Page, or raises FetchError

Rules (see ingest/sources.yaml):
- blocked domains (e.g. myscheme.gov.in, whose terms forbid bots) are never requested
- robots.txt is honoured; if robots.txt cannot be read (other than 404) the site is skipped
- at most one request per domain every N seconds
- pages are cached in cache/web/ for `cache_days`; tests pass their own httpx transport
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
import yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "cache" / "web"


class FetchError(RuntimeError):
    """The page could not or may not be fetched."""


@dataclass
class Page:
    url: str
    final_url: str
    content_type: str          # "html" | "pdf"
    title: str
    text: str
    content_hash: str
    fetched_at: str
    links: list = field(default_factory=list)      # [[anchor text, absolute url], ...] for HTML pages


@lru_cache(maxsize=None)
def load_policy() -> dict:
    return yaml.safe_load((Path(__file__).parent / "sources.yaml").read_text(encoding="utf-8"))


def host_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def is_blocked(url: str) -> bool:
    host = host_of(url)
    return any(host == d or host.endswith("." + d) for d in load_policy()["blocked_domains"])


def is_official(url: str) -> bool:
    host = host_of(url)
    if host.endswith(tuple(load_policy()["official_suffixes"])):
        return True
    from .source_map import extra_official_domains      # government bodies outside .gov.in
    return any(host == d or host.endswith("." + d) for d in extra_official_domains())


def html_to_text(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "lxml")
    title = (soup.title.get_text(" ", strip=True) if soup.title else "")[:200]
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "form", "svg", "iframe"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    return title, _tidy(text)


def html_links(html: str, base_url: str, limit: int = 400) -> list[list[str]]:
    soup = BeautifulSoup(html, "lxml")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = urljoin(base_url, href).split("#")[0]
        if url in seen or not url.startswith("http"):
            continue
        seen.add(url)
        out.append([a.get_text(" ", strip=True)[:150], url])
        if len(out) >= limit:
            break
    return out


def pdf_to_text(data: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    return _tidy("\n".join((p.extract_text() or "") for p in reader.pages[:40]))


def _tidy(text: str) -> str:
    text = re.sub(r"[ \t ]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text).strip()


class Fetcher:
    def __init__(self, client: httpx.Client | None = None, cache_dir: Path | None = CACHE_DIR,
                 sleep=time.sleep):
        cfg = load_policy()["fetch"]
        self.cfg = cfg
        self.client = client or httpx.Client(
            headers={"User-Agent": cfg["user_agent"]}, timeout=cfg["timeout_seconds"],
            follow_redirects=True)
        self.cache_dir = cache_dir
        self.sleep = sleep
        self._robots: dict[str, RobotFileParser | None] = {}
        self._last_hit: dict[str, float] = {}

    # ---------- politeness ----------
    def _wait_turn(self, host: str) -> None:
        gap = self.cfg["min_seconds_between_requests_per_domain"]
        last = self._last_hit.get(host)
        if last is not None and time.monotonic() - last < gap:
            self.sleep(gap - (time.monotonic() - last))
        self._last_hit[host] = time.monotonic()

    def allowed(self, url: str) -> bool:
        if is_blocked(url):
            return False
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots:
            rp = RobotFileParser()
            try:
                self._wait_turn(parsed.netloc)
                r = self.client.get(origin + "/robots.txt")
                if r.status_code == 404:
                    rp.parse([])                      # no robots.txt: everything allowed
                elif r.status_code == 200:
                    rp.parse(r.text.splitlines())
                else:
                    rp = None                         # 401/403/5xx: treat the site as off-limits
            except httpx.HTTPError:
                rp = None
            self._robots[origin] = rp
        rp = self._robots[origin]
        return rp is not None and rp.can_fetch(self.cfg["user_agent"], url)

    # ---------- cache ----------
    def _cache_path(self, url: str) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".json")

    def _from_cache(self, url: str) -> Page | None:
        path = self._cache_path(url)
        if not path or not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        age = datetime.now(timezone.utc) - datetime.fromisoformat(data["fetched_at"])
        if age > timedelta(days=self.cfg["cache_days"]):
            return None
        return Page(**data)

    def _to_cache(self, page: Page) -> None:
        path = self._cache_path(page.url)
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(asdict(page), ensure_ascii=False), encoding="utf-8")

    # ---------- fetch ----------
    def fetch(self, url: str) -> Page:
        cached = self._from_cache(url)
        if cached:
            return cached
        if is_blocked(url):
            raise FetchError(f"blocked by source policy: {host_of(url)}")
        if not self.allowed(url):
            raise FetchError(f"disallowed by robots.txt or robots.txt unreadable: {url}")
        self._wait_turn(urlparse(url).netloc)
        try:
            r = self.client.get(url)
        except httpx.HTTPError as e:
            raise FetchError(f"{type(e).__name__}: {e}") from e
        if r.status_code != 200:
            raise FetchError(f"HTTP {r.status_code} for {url}")
        if len(r.content) > self.cfg["max_bytes"]:
            raise FetchError(f"too large ({len(r.content)} bytes): {url}")
        final_url = str(r.url)
        if is_blocked(final_url):
            raise FetchError(f"redirected to blocked domain: {final_url}")
        ctype = r.headers.get("content-type", "").lower()
        if "pdf" in ctype or final_url.lower().endswith(".pdf"):
            try:
                kind, title, text = "pdf", "", pdf_to_text(r.content)
            except Exception as e:  # malformed PDFs are common on old portals
                raise FetchError(f"unreadable PDF: {e}") from e
        elif "html" in ctype or not ctype:
            kind = "html"
            title, text = html_to_text(r.text)
            links = html_links(r.text, final_url)
        else:
            raise FetchError(f"unsupported content type {ctype!r}")
        text = text[: self.cfg["max_text_chars"]]
        if len(text) < 200 and not (kind == "html" and len(links) >= 3):
            # listing pages carry little text but many links; the crawler still needs them.
            # Pages that reach the LLM are gated separately by ingest.crawl.worth_extracting.
            raise FetchError(f"too little text ({len(text)} chars); page may need JavaScript")
        page = Page(url=url, final_url=final_url, content_type=kind, title=title, text=text,
                    content_hash=hashlib.sha256(text.encode()).hexdigest()[:16],
                    fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    links=links if kind == "html" else [])
        self._to_cache(page)
        return page
