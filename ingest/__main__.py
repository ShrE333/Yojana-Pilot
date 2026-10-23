"""Bulk-build the scheme catalog from official websites, before anyone chats.

    python -m ingest --state maharashtra                  # central + Maharashtra, all topics
    python -m ingest --state maharashtra --topics farmer,women --max-searches 10
    python -m ingest --names "PM Vishwakarma" "Lek Ladki Yojana"
    python -m ingest --stats                              # what is in the catalog now
    python -m ingest --probe-sources mh                   # find usable Maharashtra dept/ZP/board sites
    python -m ingest --map-urls mh                        # PHASE 1: find every scheme page (no LLM, free)
    python -m ingest --extract-queued --limit 200          # PHASE 2: read queued pages with the LLM

Each search uses one SerpAPI credit (free plan: 250 a month). Results, pages and
processed URLs are cached, so re-running costs nothing for work already done.
Scheme names found on listing pages are searched next ("follow mentions").
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter

from discovery.catalog import load_catalog
from discovery.discover import Discoverer, DiscoveryReport
from ingest.fetch import load_policy

TOPICS = {
    "farmer": "farmers agriculture",
    "labour": "agricultural labourers unorganised workers",
    "construction": "construction workers welfare board",
    "women": "women",
    "widow": "widow pension",
    "senior": "senior citizens old age pension",
    "disability": "persons with disabilities",
    "student": "students scholarship",
    "girl": "girl child",
    "sc_st": "scheduled caste scheduled tribe",
    "obc": "OBC backward classes",
    "minority": "minority communities",
    "housing": "housing for poor",
    "health": "health insurance poor families",
    "business": "self employment loan subsidy",
    "skill": "skill training youth unemployed",
    "maternity": "pregnant women maternity benefit",
    "fisher": "fishermen",
    "artisan": "artisans handicraft weavers",
}


def build_queries(state: str | None, topics: list[str]) -> list[str]:
    suffix = load_policy()["search_suffix"]
    where = [f"{state.replace('_', ' ').title()} government"] if state else []
    where.append("central government")
    return [f"{w} scheme for {TOPICS[t]} eligibility {suffix}" for t in topics for w in where]


def print_stats() -> None:
    from store import db
    s = db.stats()
    print(f"Catalog: {s['schemes']} schemes ({s['by_origin']}), {s['rules']} rules, "
          f"{s['districts']} districts, {db.queued_count()} pages queued")
    print("By state:", ", ".join(f"{k} {v}" for k, v in list(s["by_state"].items())[:8]))


def _pick_sources(args, code):
    from core.fields import check_value
    from ingest.source_map import load_sources
    sources = list(load_sources(code))
    if args.district:
        district = check_value("district", args.district)
        sources = [s for s in sources if s.get("district") == district] + \
                  ([] if args.only_districts else [s for s in sources if not s.get("district")])
    elif args.only_districts:
        sources = [s for s in sources if s.get("district")]
    return sources


def map_urls(args) -> int:
    """Phase 1: fetch listing pages on every mapped site and queue the scheme pages they link to.
    No LLM is called, so this is free and shows how many schemes are actually reachable."""
    import json

    from ingest.crawl import find_scheme_urls
    from ingest.fetch import Fetcher
    from store import db
    sources = _pick_sources(args, args.map_urls)
    if not sources:
        print(f"No resolved sources. Run: python -m ingest --probe-sources {args.map_urls}")
        return 1
    fetcher = Fetcher()
    total_found = total_new = 0
    print(f"Phase 1 on {min(len(sources), args.max_sites)} site(s). No LLM, no search credits.\n")
    print(f"{'site':42}{'pages':>7}{'lists':>7}{'schemes':>9}{'new':>6}  errors")
    for src in sources[: args.max_sites]:
        found = find_scheme_urls(src, fetcher, max_pages=args.pages_per_site)
        note = json.dumps({"agency_type": src["agency_type"], "agency_name": src["name"],
                           "district": src.get("district")}, ensure_ascii=False)
        new = db.queue_urls([(u, note) for u in found.scheme_urls])
        total_found += len(found.scheme_urls)
        total_new += new
        print(f"{found.site[:40]:42}{found.pages_fetched:>7}{found.listings:>7}"
              f"{len(found.scheme_urls):>9}{new:>6}  {len(found.errors)}", flush=True)
    print(f"\n{total_found} scheme pages found, {total_new} newly queued, "
          f"{db.queued_count()} waiting in total.")
    print(f"Next: python -m ingest --extract-queued --limit 100")
    return 0


def extract_queued(args) -> int:
    """Phase 2: read queued scheme pages with the LLM. Stop any time; the queue remembers."""
    import json

    from store import db
    waiting = db.queued_count()
    if not waiting:
        print("Nothing queued. Run --map-urls first.")
        return 0
    batch = db.queued_urls(args.limit)
    d = Discoverer(log=print)
    report = DiscoveryReport()
    print(f"{waiting} pages queued; reading {len(batch)} now with {d.llm.name}\n")
    for i, row in enumerate(batch, 1):
        meta = json.loads(row["note"]) if row["note"] else {}
        d.process_url(row["url"], report, extra=meta)
        if i % 10 == 0:
            print(f"  ... {i}/{len(batch)} pages, {len(report.new_schemes)} new schemes", flush=True)
    print(f"\nDone: {len(report.new_schemes)} new, {len(report.updated_schemes)} updated, "
          f"{len(report.skipped)} skipped. {db.queued_count()} still queued.")
    print_stats()
    return 0


def crawl_map(args) -> int:
    from ingest.crawl import SiteCrawler
    from ingest.source_map import load_sources
    sources = _pick_sources(args, args.source_map)
    if not sources:
        print(f"No resolved sources. Run: python -m ingest --probe-sources {args.source_map}")
        return 1
    d = Discoverer(log=print)
    crawler = SiteCrawler(d, log=print)
    total = 0
    for src in sources[: args.max_sites]:
        print(f"Crawling {src['name']} ({src['host']})")
        rep = crawler.crawl(src, max_pages=args.pages_per_site)
        total += len(rep.schemes)
        print(f"  {rep.pages_fetched} pages, {rep.listings} listings, {rep.candidates} candidate pages, "
              f"{len(rep.schemes)} schemes, {len(rep.errors)} errors")
    print(f"\nDone: {total} scheme pages processed from {min(len(sources), args.max_sites)} sites")
    print_stats()
    return 0


def main() -> int:
    for stream in (sys.stdout,):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="python -m ingest")
    ap.add_argument("--state", help="state enum name, e.g. maharashtra")
    ap.add_argument("--topics", default="all", help=f"comma list or 'all': {', '.join(TOPICS)}")
    ap.add_argument("--names", nargs="*", default=[], help="specific scheme names to look up")
    ap.add_argument("--max-searches", type=int, default=20, help="paid searches this run (cached are free)")
    ap.add_argument("--max-pages", type=int, default=40)
    ap.add_argument("--no-follow", action="store_true", help="do not search names found on listing pages")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--probe-sources", metavar="MAP", help="probe a source map's candidate domains, e.g. mh")
    ap.add_argument("--source-map", metavar="MAP", help="crawl a resolved source map, e.g. mh")
    ap.add_argument("--district", help="with --source-map: only this district's sites (+ state agencies)")
    ap.add_argument("--only-districts", action="store_true", help="with --source-map: skip state agencies")
    ap.add_argument("--map-urls", metavar="MAP", help="PHASE 1: find scheme page URLs, no LLM")
    ap.add_argument("--extract-queued", action="store_true", help="PHASE 2: read queued pages with the LLM")
    ap.add_argument("--limit", type=int, default=100, help="with --extract-queued: how many pages this run")
    ap.add_argument("--max-sites", type=int, default=10)
    ap.add_argument("--pages-per-site", type=int, default=15)
    args = ap.parse_args()
    if args.stats:
        print_stats()
        return 0
    if args.probe_sources:
        from ingest.source_map import probe
        print(f"Probing candidate sites for '{args.probe_sources}' (homepage + robots.txt)...")
        ok = probe(args.probe_sources)
        print(f"{len(ok)} usable sites written to ingest/sources_{args.probe_sources}.resolved.yaml")
        return 0
    if args.map_urls:
        return map_urls(args)
    if args.extract_queued:
        return extract_queued(args)
    if args.source_map:
        return crawl_map(args)

    topics = list(TOPICS) if args.topics == "all" else [t.strip() for t in args.topics.split(",")]
    unknown = [t for t in topics if t not in TOPICS]
    if unknown:
        ap.error(f"unknown topics {unknown}")
    d = Discoverer(log=print)
    report = DiscoveryReport()
    queries = d.name_queries(args.names) + ([] if args.names and args.topics == "all" else build_queries(args.state, topics))
    print(f"{len(queries)} queries planned; limits: {args.max_searches} searches, {args.max_pages} pages")
    d.ingest_queries(queries, args.max_searches, args.max_pages, per_query=3, report=report)
    if not args.no_follow and report.mentioned:
        follow = d.name_queries(list(dict.fromkeys(report.mentioned)))
        if follow:
            print(f"following {len(follow)} scheme names found on listing pages")
            left = args.max_searches - d.search.live_calls
            d.ingest_queries(follow, max(0, left), args.max_pages, per_query=1, report=report)
    print(f"\nDone: {report.searches} searches ({d.search.live_calls} used credits), "
          f"{report.pages_fetched} pages, {len(report.new_schemes)} new, {len(report.updated_schemes)} updated, "
          f"{len(report.skipped)} skipped")
    for url, why in report.skipped[:15]:
        print(f"  skipped {url[:80]}: {why[:100]}")
    print_stats()
    return 0


if __name__ == "__main__":
    sys.exit(main())
