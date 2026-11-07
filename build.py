#!/usr/bin/env python3
"""Build an always-current programme notes page for the University of Leeds
Concert Series, using the event pages on https://concerts.leeds.ac.uk/events/.

What it does:
  1. reads the "What's On" listing to find the upcoming concerts,
  2. downloads the event pages of the next few concerts,
  3. cleans their programme notes into simple, readable HTML,
  4. writes a small static site (index.html, images, QR code, poster).

The page contains the next few concerts, and a tiny script chooses the right
one from the visitor's clock. It therefore moves from one concert to the next
at the right moment, even between scheduled rebuilds.

Usage:
    python build.py --out _site --site-url https://example.github.io/repo/
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import io
import logging
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, NamedTuple
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
TEMPLATES = ROOT / "templates"

SITE = "https://concerts.leeds.ac.uk"
WHATS_ON_URL = SITE + "/events/"
LISTING_URL = SITE + "/events/?q=&post_type=tk_events&date-type={date_type}"
USER_AGENT = "ConcertProgrammeNotes/1.0 (+https://github.com/cbradburne/ConcertProgramme)"
TZ = ZoneInfo("Europe/London")

# Keep showing a concert for this long after it finishes...
AFTER_CONCERT = dt.timedelta(minutes=45)
# ...unless the next concert starts within this long.
BEFORE_NEXT = dt.timedelta(minutes=30)
# Assumed length of a concert when only its start time is published.
DEFAULT_LENGTH = dt.timedelta(hours=2)
# Concerts whose full notes are built into the page (current + next ones).
EMBEDDED_CONCERTS = 4
# Concerts listed under "Coming up", and how many of them are shown at once.
UPCOMING_LISTED = 12
UPCOMING_SHOWN = 4
# Width of the converted header images, in pixels.
IMAGE_WIDTH = 800

log = logging.getLogger("programme")

Fetch = Callable[[str], bytes]


class BuildError(Exception):
    """The site could not be read or understood; the page is not rebuilt."""


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------


def fetch_url(url: str, attempts: int = 3) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en-GB,en"}
    )
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if attempt == attempts:
                raise BuildError(f"Could not download {url}: {exc}") from exc
            log.warning("Retrying %s after error: %s", url, exc)
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def fetch_text(fetch: Fetch, url: str) -> str:
    return fetch(url).decode("utf-8", "replace")


# --------------------------------------------------------------------------
# A small, forgiving HTML tree (the site's HTML is not always well formed)
# --------------------------------------------------------------------------

VOID_TAGS = frozenset(
    "area base br col embed hr img input link meta param source track wbr".split()
)
# Containers that a stray inline end tag (e.g. </p> or </strong>) never closes.
SCOPE_TAGS = frozenset(
    "html body main article section aside header footer nav div table tr td th "
    "ul ol li dl dt dd form figure blockquote".split()
)
# A new <p> closes an open <p>, a new <li> closes an open <li>, and so on.
AUTO_CLOSE = {
    "p": {"p"},
    "li": {"li"},
    "dt": {"dt", "dd"},
    "dd": {"dt", "dd"},
    "tr": {"tr"},
    "td": {"td", "th"},
    "th": {"td", "th"},
    "option": {"option"},
}
CLOSES_P = frozenset(
    "address article aside blockquote div dl fieldset figure footer form h1 h2 h3 "
    "h4 h5 h6 header hr main nav ol pre section table ul".split()
)


class Node:
    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag: str, attrs: dict[str, str] | None = None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list[Node | str] = []

    def has_class(self, name: str) -> bool:
        return name in self.attrs.get("class", "").split()

    def iter(self):
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.iter()

    def find_all(self, tag: str | None = None, cls: str | None = None) -> list[Node]:
        return [
            node
            for node in self.iter()
            if (tag is None or node.tag == tag) and (cls is None or node.has_class(cls))
        ]

    def find(self, tag: str | None = None, cls: str | None = None) -> Node | None:
        for node in self.iter():
            if (tag is None or node.tag == tag) and (cls is None or node.has_class(cls)):
                return node
        return None

    def text(self) -> str:
        parts: list[str] = []

        def collect(node: Node) -> None:
            for child in node.children:
                if isinstance(child, str):
                    parts.append(child)
                elif child.tag not in ("script", "style"):
                    collect(child)

        collect(self)
        return " ".join("".join(parts).split())


class TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#document")
        self.stack: list[Node] = [self.root]

    def handle_starttag(self, tag, attrs):
        if tag in AUTO_CLOSE:
            self._close(AUTO_CLOSE[tag], scoped=True)
        if tag in CLOSES_P:
            self._close({"p"}, scoped=True)
        node = Node(tag, {name: value or "" for name, value in attrs})
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = Node(tag, {name: value or "" for name, value in attrs})
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        # Container end tags close everything inside them; inline end tags
        # never reach outside the nearest container.
        self._close({tag}, scoped=tag not in SCOPE_TAGS)

    def handle_data(self, data):
        self.stack[-1].children.append(data)

    def _close(self, tags: set[str], scoped: bool) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            current = self.stack[index].tag
            if current in tags:
                del self.stack[index:]
                return
            if scoped and current in SCOPE_TAGS:
                return


def parse_html(markup: str) -> Node:
    builder = TreeBuilder()
    builder.feed(markup)
    builder.close()
    return builder.root


# --------------------------------------------------------------------------
# Programme notes: turn the site's markup into clean, consistent HTML
# --------------------------------------------------------------------------


class Fmt(NamedTuple):
    bold: bool = False
    italic: bool = False
    href: str | None = None


PLAIN = Fmt()


@dataclass
class Run:
    text: str
    fmt: Fmt = PLAIN


BR = None  # marks a line break inside a paragraph

SKIP_TAGS = frozenset(
    "script style noscript iframe object embed video audio svg form button select "
    "textarea img picture source template canvas input map".split()
)
BLOCK_TAGS = frozenset(
    "p div h5 h6 blockquote section article header footer figure figcaption table "
    "thead tbody tfoot tr td th dl dt dd pre address center main aside nav".split()
)
HEADING_TAGS = frozenset("h1 h2 h3 h4".split())
BOLD_TAGS = frozenset("b strong".split())
ITALIC_TAGS = frozenset("i em cite".split())


def clean_href(href: str | None) -> str | None:
    """Keep only ordinary web/mail/phone links; unwrap Outlook 'safe links'."""
    if not href:
        return None
    href = href.strip()
    parts = urllib.parse.urlsplit(href)
    if parts.netloc.endswith("safelinks.protection.outlook.com"):
        target = urllib.parse.parse_qs(parts.query).get("url", [None])[0]
        return clean_href(target)
    if parts.scheme in ("http", "https", "mailto", "tel"):
        return href
    if not parts.scheme and href.startswith("/"):
        return urllib.parse.urljoin(SITE, href)
    return None


def picture_link_label(href: str, image: Node | None) -> str | None:
    host = urllib.parse.urlsplit(href).netloc.lower()
    if any(name in host for name in ("youtube.", "youtu.be", "vimeo.")):
        return "Watch the video"
    alt = image.attrs.get("alt", "").strip() if image else ""
    return alt or None


class Block(NamedTuple):
    kind: str  # "p", "h", "ul", "ol" or "hr"
    lines: list[list[Run]]


class NotesReader:
    """Walks the notes markup and collects blocks of formatted text runs."""

    def __init__(self) -> None:
        self.blocks: list[Block] = []
        self.runs: list[Run | None] = []

    def read(self, node: Node) -> list[Block]:
        self.walk(node, PLAIN, ordered=False)
        self.flush()
        return self.blocks

    def walk(self, node: Node, fmt: Fmt, ordered: bool) -> None:
        for child in node.children:
            if isinstance(child, str):
                self.runs.append(Run(child, fmt))
                continue
            tag = child.tag
            if tag in SKIP_TAGS:
                continue
            if tag == "br":
                self.runs.append(BR)
            elif tag == "hr":
                self.flush()
                self.blocks.append(Block("hr", []))
            elif tag in HEADING_TAGS:
                self.flush()
                self.walk(child, PLAIN, ordered)
                self.flush("h")
            elif tag in ("ul", "ol"):
                self.flush()
                self.walk(child, PLAIN, ordered=tag == "ol")
                self.flush()
            elif tag == "li":
                self.flush()
                self.walk(child, PLAIN, ordered)
                self.flush("ol" if ordered else "ul")
            elif tag in BLOCK_TAGS:
                # Formatting never leaks from one paragraph into the next.
                self.flush()
                self.walk(child, PLAIN, ordered)
                self.flush()
            elif tag in BOLD_TAGS:
                self.walk(child, fmt._replace(bold=True), ordered)
            elif tag in ITALIC_TAGS:
                self.walk(child, fmt._replace(italic=True), ordered)
            elif tag == "a":
                href = clean_href(child.attrs.get("href")) or fmt.href
                if href and not child.text() and child.find("img"):
                    # A picture that links somewhere (e.g. a video thumbnail).
                    label = picture_link_label(href, child.find("img"))
                    if label:
                        self.flush()
                        self.runs.append(Run(label, PLAIN._replace(href=href)))
                        self.flush()
                    continue
                self.walk(child, fmt._replace(href=href), ordered)
            else:  # span, font, u, small, ...: keep the text, drop the styling
                self.walk(child, fmt, ordered)

    def flush(self, kind: str = "p") -> None:
        runs, self.runs = self.runs, []
        lines: list[list[Run]] = [[]]
        for run in runs:
            if run is BR:
                lines.append([])
            else:
                lines[-1].append(run)
        lines = [tidy_line(line) for line in lines]
        if kind in ("h", "ul", "ol"):
            lines = [line for line in lines if has_text(line)]
            if lines:
                self.blocks.append(Block(kind, lines))
            return
        # A blank line inside a paragraph starts a new paragraph.
        paragraph: list[list[Run]] = []
        for line in lines + [[]]:
            if has_text(line):
                paragraph.append(line)
            elif paragraph:
                self.blocks.append(Block("p", paragraph))
                paragraph = []


def has_text(line: list[Run]) -> bool:
    """True if the line has real words, not just spaces or stray punctuation."""
    text = "".join(run.text for run in line)
    return bool(re.search(r"\w", text))


def tidy_line(line: list[Run]) -> list[Run]:
    """Collapse whitespace, move spaces outside formatting, merge runs."""
    pieces: list[Run] = []
    for run in line:
        text = re.sub(r"\s+", " ", run.text)
        if not text:
            continue
        if run.fmt != PLAIN and text.strip() != text:
            core = text.strip()
            if text[0] == " ":
                pieces.append(Run(" "))
            if core:
                pieces.append(Run(core, run.fmt))
            if text[-1] == " ":
                pieces.append(Run(" "))
        else:
            pieces.append(Run(text, run.fmt))

    merged: list[Run] = []
    for run in pieces:
        if merged and merged[-1].text.endswith(" ") and run.text.startswith(" "):
            run = Run(run.text[1:], run.fmt)
        if not run.text:
            continue
        if merged and merged[-1].fmt == run.fmt:
            merged[-1] = Run(merged[-1].text + run.text, run.fmt)
        else:
            merged.append(run)

    if merged:
        merged[0] = Run(merged[0].text.lstrip(), merged[0].fmt)
        merged[-1] = Run(merged[-1].text.rstrip(), merged[-1].fmt)
    return [run for run in merged if run.text]


def line_text(line: list[Run]) -> str:
    return "".join(run.text for run in line)


def slice_runs(line: list[Run], start: int, end: int | None = None) -> list[Run]:
    """The part of a line between two character offsets of its plain text."""
    total = len(line_text(line))
    end = total if end is None else end
    result: list[Run] = []
    position = 0
    for run in line:
        run_start, run_end = position, position + len(run.text)
        position = run_end
        lo, hi = max(start, run_start), min(end, run_end)
        if lo < hi:
            result.append(Run(run.text[lo - run_start : hi - run_start], run.fmt))
    return tidy_line(result)


def unbold(line: list[Run]) -> list[Run]:
    return tidy_line([Run(run.text, run.fmt._replace(bold=False)) for run in line])


def render_runs(line: list[Run]) -> str:
    out = []
    for run in line:
        piece = html.escape(run.text, quote=False)
        if run.fmt.italic:
            piece = f"<em>{piece}</em>"
        if run.fmt.bold:
            piece = f"<strong>{piece}</strong>"
        if run.fmt.href:
            href = html.escape(run.fmt.href)
            piece = f'<a href="{href}" target="_blank" rel="noopener">{piece}</a>'
        out.append(piece)
    return "".join(out)


# "Johann Sebastian Bach" / "Bach (1685-1750)" - a name, perhaps with dates.
COMPOSER_RE = re.compile(
    r"(?P<name>[^\W\d_][^\W\d_ .,'’&\-]*(?:[ .,'’&\-]+[^\W\d_][^\W\d_.'’\-]*)*\.?)"
    r"\s*(?P<dates>\([^()]{1,40}\))?"
)
# What may sit between the composer and the work: dates, "arr. Someone".
WORK_RE = re.compile(
    r"(?P<detail>(?:\s*\([^()]{1,60}\)|\s*arr(?:anged)?\.?(?:\s+by)?\s+[^()\-–—]{1,80}?)*)"
    r"\s*[-–—]\s*(?P<work>\S.*)"
)
PERFORMER_RE = re.compile(r"\s*(?P<name>[^()]{2,60}?)\s*\((?P<role>[^()]{1,60})\)\s*")


def as_work(line: list[Run]) -> str | None:
    """'<b>Composer</b> (dates) - Work' as a programme entry, else None."""
    if not line or not line[0].fmt.bold or line[0].fmt.href:
        return None
    composer = COMPOSER_RE.fullmatch(line[0].text)
    if not composer or len(composer["name"].split()) > 7:
        return None
    rest = line_text(line)[len(line[0].text) :]
    match = WORK_RE.fullmatch(rest)
    if not match:
        return None
    offset = len(line[0].text)
    work = slice_runs(line, offset + match.start("work"))
    work = unbold(work)
    details = " ".join(
        part for part in (composer["dates"], " ".join(match["detail"].split())) if part
    )
    detail_html = f' <span class="detail">{html.escape(details)}</span>' if details else ""
    return (
        f'<li><span class="composer">{html.escape(composer["name"])}</span>'
        f'{detail_html}<span class="work">{render_runs(work)}</span></li>'
    )


def as_performers(line: list[Run]) -> str | None:
    """'<b>Name</b> (instrument) <b>Name</b> (instrument) ...' as a list."""
    text = line_text(line)
    items = list(PERFORMER_RE.finditer(text))
    if len(items) < 2 or "".join(m.group(0) for m in items) != text:
        return None
    bold = {run.text.strip() for run in line if run.fmt.bold}
    names = [m["name"].strip() for m in items]
    if any(re.search(r"[\d.:;!?]", name) or len(name.split()) > 6 for name in names):
        return None
    if sum(name in bold for name in names) * 2 < len(names):
        return None
    rows = "".join(
        f'<li><span class="name">{html.escape(m["name"].strip())}</span> '
        f'<span class="role">{html.escape(m["role"].strip())}</span></li>'
        for m in items
    )
    return f'<ul class="performers">{rows}</ul>'


def is_heading(lines: list[list[Run]]) -> bool:
    if len(lines) != 1:
        return False
    words = [run for run in lines[0] if run.text.strip()]
    text = line_text(lines[0])
    return bool(words) and all(run.fmt.bold for run in words) and len(text) <= 90


def render_notes(container: Node | None) -> str:
    if container is None:
        return ""
    items: list[tuple[str, str]] = []  # (kind, html) in reading order
    for block in NotesReader().read(container):
        if block.kind == "hr":
            items.append(("hr", "<hr>"))
        elif block.kind == "h":
            lines = [render_runs(unbold(line)) for line in block.lines]
            items.append(("h", f"<h3>{'<br>'.join(lines)}</h3>"))
        elif block.kind in ("ul", "ol"):
            items.append((block.kind, f"<li>{'<br>'.join(map(render_runs, block.lines))}</li>"))
        elif is_heading(block.lines) and not as_work(block.lines[0]):
            items.append(("h", f"<h3>{render_runs(unbold(block.lines[0]))}</h3>"))
        else:
            text_lines: list[str] = []
            for line in block.lines:
                work = as_work(line)
                people = None if work else as_performers(line)
                if (work or people) and text_lines:
                    items.append(("p", f"<p>{'<br>'.join(text_lines)}</p>"))
                    text_lines = []
                if work:
                    items.append(("work", work))
                elif people:
                    items.append(("people", people))
                else:
                    text_lines.append(render_runs(line))
            if text_lines:
                items.append(("p", f"<p>{'<br>'.join(text_lines)}</p>"))

    # Group consecutive programme entries / list items into lists.
    out: list[str] = []
    wrappers = {"work": '<ul class="repertoire">', "ul": "<ul>", "ol": "<ol>"}
    closers = {"work": "</ul>", "ul": "</ul>", "ol": "</ol>"}
    open_kind = None
    for kind, markup in items:
        if kind != open_kind and open_kind:
            out.append(closers[open_kind])
            open_kind = None
        if kind in wrappers and kind != open_kind:
            out.append(wrappers[kind])
            open_kind = kind
        out.append(markup)
    if open_kind:
        out.append(closers[open_kind])
    return "\n".join(out)


# --------------------------------------------------------------------------
# Concerts: listing, dates and times
# --------------------------------------------------------------------------


@dataclass
class Concert:
    id: str
    title: str
    url: str
    date: dt.date
    time_text: str
    start: dt.datetime
    finish: dt.datetime
    has_time: bool = True
    until: dt.datetime | None = None
    venue: str = ""
    image_url: str = ""
    image_src: str = ""
    notes_html: str = ""


TIME_RE = re.compile(
    r"(?<![\d:.])(?P<h>\d{1,2})(?:[:.](?P<m>\d{2}))?\s*(?P<ampm>[ap]\.?m\.?)?(?![\d])"
    r"|(?P<word>\bnoon\b|\bmidday\b|\bmidnight\b)",
    re.IGNORECASE,
)


def parse_times(text: str) -> list[dt.time]:
    """'1:05pm - 1:55pm' -> [13:05, 13:55]. Ignores bare numbers."""
    found: list[list] = []  # [hour, minute, "am"/"pm"/None]
    for match in TIME_RE.finditer(text):
        if match["word"]:
            word = match["word"].lower()
            found.append([0 if word == "midnight" else 12, 0, None])
            continue
        if match["m"] is None and match["ampm"] is None:
            continue
        ampm = match["ampm"].lower().replace(".", "") if match["ampm"] else None
        found.append([int(match["h"]), int(match["m"] or 0), ampm])
    # "7:30 - 9:30pm": an unmarked time takes the marker of the next one.
    for index in range(len(found) - 2, -1, -1):
        if found[index][2] is None and found[index + 1][2] and found[index][0] <= 12:
            found[index][2] = found[index + 1][2]
    times = []
    for hour, minute, ampm in found:
        if ampm and hour <= 12:
            hour = hour % 12 + (12 if ampm == "pm" else 0)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            times.append(dt.time(hour, minute))
    return times


def make_concert(id_: str, title: str, url: str, date: dt.date, time_text: str) -> Concert:
    times = parse_times(time_text)
    if times:
        start = dt.datetime.combine(date, times[0], tzinfo=TZ)
        if len(times) > 1:
            finish = dt.datetime.combine(date, times[1], tzinfo=TZ)
            if finish <= start:
                finish += dt.timedelta(days=1)
        else:
            finish = start + DEFAULT_LENGTH
        has_time = True
    else:  # no time published: treat as the whole day
        start = dt.datetime.combine(date, dt.time(0, 0), tzinfo=TZ)
        finish = dt.datetime.combine(date, dt.time(23, 59), tzinfo=TZ)
        has_time = False
    return Concert(
        id=id_,
        title=title,
        url=url,
        date=date,
        time_text=time_text,
        start=start,
        finish=finish,
        has_time=has_time,
    )


def parse_listing(markup: str) -> list[Concert]:
    root = parse_html(markup)
    recognised = any(
        node.attrs.get("name") == "post_type" and node.attrs.get("value") == "tk_events"
        for node in root.find_all("input")
    )
    if not recognised:
        raise BuildError("The What's On page layout was not recognised.")
    concerts = []
    for article in root.find_all("article"):
        article_id = article.attrs.get("id", "")
        heading = article.find(cls="search-results-item__title")
        link = heading.find("a") if heading else None
        time_el = article.find("time")
        if not article_id.startswith("event-") or not link or not time_el:
            continue
        try:
            date = dt.date.fromisoformat(time_el.attrs.get("datetime", "")[:10])
        except ValueError:
            log.warning("Skipping %s: unreadable date", article_id)
            continue
        meta = article.find("dd", cls="search-results-item__meta__data")
        meta_text = meta.text() if meta else ""
        date_text = time_el.text()
        if meta_text.startswith(date_text):
            meta_text = meta_text[len(date_text) :]
        time_text = meta_text.strip(" ,")
        concerts.append(
            make_concert(
                article_id.removeprefix("event-"),
                link.text(),
                urllib.parse.urljoin(SITE, link.attrs.get("href", "")),
                date,
                time_text,
            )
        )
    return concerts


def assign_until(concerts: list[Concert]) -> None:
    """Work out until when each concert is the one shown on the page."""
    for index, concert in enumerate(concerts):
        until = concert.finish + AFTER_CONCERT
        if index + 1 < len(concerts):
            next_start = concerts[index + 1].start
            until = min(until, max(concert.finish, next_start - BEFORE_NEXT))
        concert.until = until


def current_concert(concerts: list[Concert], now: dt.datetime) -> Concert | None:
    return next((c for c in concerts if c.until and c.until > now), None)


def upcoming_concerts(fetch: Fetch, now: dt.datetime) -> list[Concert]:
    """Upcoming concerts in date order, including any on today."""
    by_url: dict[str, Concert] = {}
    # "current" lists future events; this month's listing also has today's.
    for date_type in ("current", now.astimezone(TZ).strftime("%Y.%m")):
        url = LISTING_URL.format(date_type=date_type)
        for concert in parse_listing(fetch_text(fetch, url)):
            by_url.setdefault(concert.url, concert)
    concerts = sorted(by_url.values(), key=lambda c: (c.start, c.finish, c.title))
    assign_until(concerts)
    return [c for c in concerts if c.until > now]


def add_event_details(fetch: Fetch, concert: Concert) -> None:
    root = parse_html(fetch_text(fetch, concert.url))
    title = root.find("h1", cls="page-heading__title")
    if title and title.text():
        concert.title = title.text()
    for group in root.find_all(cls="uol-info-list__group"):
        term, data = group.find("dt"), group.find("dd")
        if term and data and term.text().lower() in ("location", "venue"):
            concert.venue = data.text()
    image = root.find("img", cls="uol-featured-image__img")
    if image and image.attrs.get("src"):
        concert.image_url = urllib.parse.urljoin(SITE, image.attrs["src"])
    concert.notes_html = render_notes(root.find("div", cls="cms"))


# --------------------------------------------------------------------------
# Images
# --------------------------------------------------------------------------


def prepare_image(fetch: Fetch, url: str, out_dir: Path, cache_dir: Path | None) -> str:
    """Return the src to use for an image: a small local WebP when possible."""
    try:
        from PIL import Image
    except ImportError:
        log.warning("Pillow is not installed; linking to the original image")
        return url

    name = hashlib.sha1(url.encode()).hexdigest()[:16] + ".webp"
    target = out_dir / "img" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / name if cache_dir else None
    if cached and cached.exists():
        shutil.copyfile(cached, target)
        return f"img/{name}"
    try:
        with Image.open(io.BytesIO(fetch(url))) as source:
            picture = source.convert("RGB")
        if picture.width > IMAGE_WIDTH:
            height = round(picture.height * IMAGE_WIDTH / picture.width)
            picture = picture.resize((IMAGE_WIDTH, height), Image.Resampling.LANCZOS)
        picture.save(target, "WEBP", quality=72, method=6)
    except Exception as exc:  # a missing picture should never stop the page
        log.warning("Could not prepare image %s: %s", url, exc)
        return ""
    if cached:
        cached.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(target, cached)
    return f"img/{name}"


# --------------------------------------------------------------------------
# Page output
# --------------------------------------------------------------------------


def fill(template: str, values: dict[str, str]) -> str:
    return re.sub(r"\{\{(\w+)\}\}", lambda m: values[m.group(1)], template)


def format_time(value: dt.datetime) -> str:
    hour = value.hour % 12 or 12
    suffix = "am" if value.hour < 12 else "pm"
    return f"{hour}:{value.minute:02d}{suffix}"


def format_date(value: dt.date) -> str:
    return f"{value:%A} {value.day} {value:%B %Y}"


def status_label(concert: Concert, now: dt.datetime) -> str:
    """Same rules as the page script (templates/app.js)."""
    if concert.start <= now < concert.finish:
        return "On now"
    today = now.astimezone(TZ).date()
    if concert.date == today:
        return "Today"
    if concert.date == today + dt.timedelta(days=1):
        return "Tomorrow"
    return "Next concert"


def iso(value: dt.datetime) -> str:
    return value.isoformat(timespec="minutes")


def render_concert(concert: Concert, visible: bool, now: dt.datetime) -> str:
    esc = html.escape
    when = format_date(concert.date)
    if concert.time_text:
        when += " · " + re.sub(r"\s+-\s+", " – ", concert.time_text)
    image = ""
    if concert.image_src:
        image = (
            f'<figure class="hero"><img src="{esc(concert.image_src)}" alt="" '
            f'width="{IMAGE_WIDTH}" height="{IMAGE_WIDTH // 2}" loading="lazy" '
            'decoding="async"></figure>'
        )
    notes = concert.notes_html or (
        "<p class=\"pending\">The programme notes for this concert haven't been "
        "published yet. Please check back closer to the concert.</p>"
    )
    venue = f'<p class="venue">{esc(concert.venue)}</p>' if concert.venue else ""
    return f"""
<article class="concert" id="concert-{esc(concert.id)}" data-concert="{esc(concert.id)}"
  data-start="{iso(concert.start)}" data-finish="{iso(concert.finish)}"
  data-until="{iso(concert.until)}"{"" if visible else " hidden"}>
  <header class="concert-head">
    <p class="status" data-status>{status_label(concert, now) if visible else ""}</p>
    <h1 class="title">{esc(concert.title)}</h1>
    <div class="meta">
      <p class="when"><time datetime="{iso(concert.start)}">{esc(when)}</time></p>
      {venue}
    </div>
  </header>
  {image}
  <p class="etiquette">Please keep your phone on silent and your screen dimmed during the performance.</p>
  <div class="notes">
{notes}
  </div>
  <p class="source"><a href="{esc(concert.url)}">This concert on the Concert Series website</a></p>
</article>"""


def render_upcoming(concert: Concert, visible: bool) -> str:
    esc = html.escape
    when = f"{concert.date:%a} {concert.date.day} {concert.date:%b}"
    if concert.has_time:
        when += " · " + format_time(concert.start)
    return (
        f'<li data-upcoming="{esc(concert.id)}" data-start="{iso(concert.start)}" '
        f'data-until="{iso(concert.until)}"{"" if visible else " hidden"}>'
        f'<a href="{esc(concert.url)}"><span class="u-when">{esc(when)}</span>'
        f'<span class="u-title">{esc(concert.title)}</span></a></li>'
    )


def build_page(concerts: list[Concert], now: dt.datetime) -> str:
    current = current_concert(concerts, now)
    embedded = concerts[:EMBEDDED_CONCERTS]
    later = [c for c in concerts if current is None or c.start >= current.start]
    later = [c for c in later if c is not current][:UPCOMING_LISTED]
    local_now = now.astimezone(TZ)
    return fill(
        (TEMPLATES / "page.html").read_text(encoding="utf-8"),
        {
            "css": (TEMPLATES / "style.css").read_text(encoding="utf-8"),
            "js": (TEMPLATES / "app.js").read_text(encoding="utf-8"),
            "concerts": "\n".join(render_concert(c, c is current, now) for c in embedded),
            "empty_hidden": " hidden" if current else "",
            "upcoming": "\n".join(
                render_upcoming(c, index < UPCOMING_SHOWN) for index, c in enumerate(later)
            ),
            "upcoming_hidden": "" if later else " hidden",
            "upcoming_shown": str(UPCOMING_SHOWN),
            "whats_on": WHATS_ON_URL,
            "generated": f"{local_now:%a} {local_now.day} {local_now:%b %Y}, "
            + format_time(local_now),
            "generated_iso": iso(now),
        },
    )


def write_qr_and_poster(site_url: str, out_dir: Path) -> None:
    try:
        import segno
    except ImportError:
        log.warning("segno is not installed; skipping the QR code and poster")
        return
    qr = segno.make(site_url, error="m")
    qr.save(out_dir / "qr.svg", scale=10, border=2, dark="#000000", light="#ffffff")
    qr.save(out_dir / "qr.png", scale=16, border=2)
    buffer = io.BytesIO()
    qr.save(buffer, kind="svg", scale=10, border=2, xmldecl=False, svgclass="qr", lineclass=None)
    poster = fill(
        (TEMPLATES / "poster.html").read_text(encoding="utf-8"),
        {
            "qr_svg": buffer.getvalue().decode("utf-8"),
            "site_url": html.escape(site_url),
            "site_url_short": html.escape(re.sub(r"^https?://|/$", "", site_url)),
        },
    )
    (out_dir / "poster.html").write_text(poster, encoding="utf-8")


def build(
    out_dir: Path,
    fetch: Fetch = fetch_url,
    now: dt.datetime | None = None,
    site_url: str | None = None,
    cache_dir: Path | None = None,
    images: bool = True,
) -> list[Concert]:
    now = now or dt.datetime.now(dt.timezone.utc)
    concerts = upcoming_concerts(fetch, now)
    log.info("Found %d upcoming concert(s)", len(concerts))

    out_dir.mkdir(parents=True, exist_ok=True)
    for concert in concerts[:EMBEDDED_CONCERTS]:
        add_event_details(fetch, concert)
        if images and concert.image_url:
            concert.image_src = prepare_image(fetch, concert.image_url, out_dir, cache_dir)
        log.info("  %s  %s", concert.start.strftime("%a %d %b %H:%M"), concert.title)

    current = current_concert(concerts, now)
    log.info("Showing now: %s", current.title if current else "(no upcoming concert)")
    (out_dir / "index.html").write_text(build_page(concerts, now), encoding="utf-8")
    if site_url:
        write_qr_and_poster(site_url, out_dir)
    return concerts


def default_site_url() -> str | None:
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repository:
        owner, name = repository.split("/", 1)
        return f"https://{owner.lower()}.github.io/{name}/"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=ROOT / "_site", help="output folder")
    parser.add_argument("--site-url", help="public address of the page (for the QR code)")
    parser.add_argument("--cache", type=Path, help="folder to keep converted images between runs")
    parser.add_argument("--no-images", action="store_true", help="leave out header images")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    site_url = args.site_url or default_site_url()
    if site_url and not site_url.endswith("/"):
        site_url += "/"
    try:
        build(args.out, site_url=site_url, cache_dir=args.cache, images=not args.no_images)
    except BuildError as exc:
        log.error("Build failed, the published page has not been changed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
