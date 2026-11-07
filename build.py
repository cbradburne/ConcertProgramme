#!/usr/bin/env python3
"""Build the programme website for the University of Leeds International
Concert Series from the event pages on https://concerts.leeds.ac.uk/events/.

What it does:
  1. reads the "What's On" listing to find the upcoming concerts,
  2. downloads each concert's event page and tidies its programme notes into
     artists, repertoire and notes/biographies,
  3. finds each concert's ticket page and photo on Open Up
     (openup.leeds.ac.uk) and its livestream on YouTube,
  4. writes a small static site: today's programme (index.html), What's on
     (events/), one programme per concert (programmes/<name>/), a QR code
     and a printable poster.

The home page contains the next few concerts, and a tiny script chooses the
right one from the visitor's clock, so the same link always shows the
current concert, even between scheduled rebuilds.

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
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, NamedTuple
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
TEMPLATES = ROOT / "templates"

SERIES_NAME = "International Concert Series"
SERIES_FULL_NAME = "University of Leeds International Concert Series"
DEFAULT_VENUE = "Clothworkers Centenary Concert Hall"

SITE = "https://concerts.leeds.ac.uk"
WHATS_ON_URL = SITE + "/events/"
LISTING_URL = SITE + "/events/?q=&post_type=tk_events&date-type={date_type}"
OPENUP = "https://openup.leeds.ac.uk"
OPENUP_WHATS_ON = OPENUP + "/whats-on/"
BOX_OFFICE = "https://buytickets.at/leedsuniversity72"  # the series' Ticket Tailor box office
YOUTUBE_CHANNEL = "https://www.youtube.com/@uolconcerts"
YOUTUBE_LIVE = YOUTUBE_CHANNEL + "/live"  # YouTube sends this to the current or next stream
YOUTUBE_FEED = "https://www.youtube.com/feeds/videos.xml?channel_id=UCkFIqzjc8oUuhMBcEfCGZQA"
INSTAGRAM = "https://www.instagram.com/uolconcerts/"
FACEBOOK = "https://www.facebook.com/UoLConcerts/"
USER_AGENT = "ConcertProgrammeNotes/1.0 (+https://github.com/cbradburne/ConcertProgramme)"
TZ = ZoneInfo("Europe/London")

# Keep showing a concert for this long after it finishes...
AFTER_CONCERT = dt.timedelta(minutes=45)
# ...unless the next concert starts within this long.
BEFORE_NEXT = dt.timedelta(minutes=30)
# Assumed length of a concert when only its start time is published.
DEFAULT_LENGTH = dt.timedelta(hours=2)
# Concerts built into the home page, so it can switch between them by itself.
EMBEDDED_CONCERTS = 4
# Concerts listed under "Coming up" on the home page, and how many are shown.
UPCOMING_LISTED = 12
UPCOMING_SHOWN = 4
# Most programmes built (one page each); the site lists 25 per page.
MAX_PROGRAMMES = 25
# Widths of the converted photos, in pixels.
IMAGE_WIDTHS = (800, 1200)

# Fonts: the University's Adobe Fonts kit (Freight Display, Freight Text and
# Freight Sans), the same one concerts.leeds.ac.uk uses. Freight is licensed
# through Adobe, so it can only be used through a kit like this, not copied.
# Set ADOBE_FONTS_KIT to "" (here or as an environment variable) to use only the
# free fonts (Playfair Display and system fonts), which are also the fallback.
ADOBE_FONTS_KIT = os.environ.get("ADOBE_FONTS_KIT", "vlw5ilb")
if not re.fullmatch(r"[a-z0-9]{0,20}", ADOBE_FONTS_KIT):
    raise SystemExit(f"ADOBE_FONTS_KIT should be an Adobe Fonts kit ID, not {ADOBE_FONTS_KIT!r}")

UOL_LOGO = (ROOT / "assets" / "uol-logo.svg").read_text(encoding="utf-8").replace(
    "<svg ", '<svg class="uol-logo" role="img" aria-label="University of Leeds" focusable="false" ', 1
)
ASSET_VERSION = hashlib.sha1(
    b"".join((TEMPLATES / name).read_bytes() for name in ("site.css", "app.js"))
).hexdigest()[:8]

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
        except urllib.error.HTTPError as exc:
            if 400 <= exc.code < 500 or attempt == attempts:  # e.g. 404: no point retrying
                raise BuildError(f"Could not download {url}: {exc}") from exc
            log.warning("Retrying %s after error: %s", url, exc)
            time.sleep(2**attempt)
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


def typographic(text: str) -> str:
    """Curly apostrophes inside words (Freight shows a straight one as a prime)."""
    return re.sub(r"(?<=\w)'(?=\w)", "’", text)


def tidy_line(line: list[Run]) -> list[Run]:
    """Collapse whitespace, move spaces outside formatting, merge runs."""
    pieces: list[Run] = []
    for run in line:
        text = typographic(re.sub(r"\s+", " ", run.text))
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


def performer_pairs(line: list[Run]) -> list[tuple[str, str]] | None:
    """'<b>Name</b> (instrument) <b>Name</b> (instrument) ...' as name/role pairs."""
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
    return [(m["name"].strip(), m["role"].strip()) for m in items]


# Words that, just before a bold name, say what the person plays or sings.
ROLE_WORDS = frozenset(
    """singer vocalist guitarist dancer pianist violinist violist cellist bassist
    double-bassist percussionist drummer saxophonist trumpeter trombonist hornist
    flautist flutist clarinettist clarinetist oboist bassoonist harpist organist
    harpsichordist accordionist fiddler conductor soprano mezzo-soprano mezzo
    contralto countertenor tenor baritone bass-baritone lutenist theorbist
    recorder-player violist-composer""".split()
)


def is_person_name(text: str) -> bool:
    match = COMPOSER_RE.fullmatch(text)
    return bool(match) and not match["dates"] and 2 <= len(text.split()) <= 5


def mentioned_artists(line: list[Run]) -> list[tuple[str, str]]:
    """Artists named in running text: '<b>Name</b> (accordion)' or 'violinist <b>Name</b>'."""
    found = []
    for index, run in enumerate(line):
        name = run.text.strip()
        if not run.fmt.bold or not is_person_name(name):
            continue
        after = line[index + 1] if index + 1 < len(line) else None
        if after and not after.fmt.bold:
            role = re.match(r"\s*\(([^()\d]{2,60})\)", after.text)
            if role:
                found.append((name, role.group(1).strip()))
                continue
        before = line[index - 1] if index > 0 else None
        if before and not before.fmt.bold:
            word = re.search(r"([A-Za-z-]+)\s*,?\s*$", before.text)
            if word and word.group(1).lower() in ROLE_WORDS:
                found.append((name, word.group(1).lower()))
    return found


def is_heading(lines: list[list[Run]]) -> bool:
    if len(lines) != 1:
        return False
    words = [run for run in lines[0] if run.text.strip()]
    text = line_text(lines[0])
    return bool(words) and all(run.fmt.bold for run in words) and len(text) <= 90


@dataclass
class Notes:
    artists: list[tuple[str, str]] = field(default_factory=list)
    repertoire_html: str = ""  # the works, with any introduction before them
    about_html: str = ""  # everything after the works: notes and biographies


def group_items(items: list[tuple[str, str]]) -> str:
    """Join (kind, html) items, wrapping runs of works / list items in lists."""
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


def read_notes(container: Node | None) -> Notes:
    """Split an event's text into artists, repertoire and notes/biographies."""
    if container is None:
        return Notes()
    items: list[tuple[str, str]] = []  # (kind, html) in reading order
    artists: list[tuple[str, str]] = []
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
                people = None if work else performer_pairs(line)
                if (work or people) and text_lines:
                    items.append(("p", f"<p>{'<br>'.join(text_lines)}</p>"))
                    text_lines = []
                if work:
                    items.append(("work", work))
                elif people:
                    artists.extend(people)
                else:
                    artists.extend(mentioned_artists(line))
                    text_lines.append(render_runs(line))
            if text_lines:
                items.append(("p", f"<p>{'<br>'.join(text_lines)}</p>"))

    while items and items[-1][0] == "hr":
        items.pop()
    unique: dict[str, tuple[str, str]] = {}
    for name, role in artists:
        unique.setdefault(name.casefold(), (name, role))

    last_work = max((i for i, (kind, _) in enumerate(items) if kind == "work"), default=-1)
    after = items[last_work + 1 :]
    while after and after[0][0] == "hr":
        after.pop(0)
    return Notes(
        artists=list(unique.values()),
        repertoire_html=group_items(items[: last_work + 1]),
        about_html=group_items(after),
    )


def render_notes(container: Node | None) -> str:
    """All the notes as one block of HTML (artists, works, then the rest)."""
    notes = read_notes(container)
    parts = []
    if notes.artists:
        parts.append(render_artists(notes.artists))
    parts += [notes.repertoire_html, notes.about_html]
    return "\n".join(part for part in parts if part)


def render_artists(artists: list[tuple[str, str]]) -> str:
    rows = "".join(
        f'<li><span class="name">{html.escape(name)}</span> '
        f'<span class="role">{html.escape(role)}</span></li>'
        for name, role in artists
    )
    return f'<ul class="performers">{rows}</ul>'


# --------------------------------------------------------------------------
# Concerts: listing, dates and times
# --------------------------------------------------------------------------


@dataclass
class Picture:
    files: list[tuple[str, int]]  # (path or URL, width in pixels), smallest first
    width: int = 0
    height: int = 0


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
    admission: str = ""
    doors: str = ""
    ticket_url: str = ""  # straight to this concert's ticket selection at the box office
    openup_url: str = ""  # the event on openup.leeds.ac.uk
    stream_url: str = ""  # this concert's YouTube livestream, when scheduled
    image_urls: list[str] = field(default_factory=list)  # best first
    picture: Picture | None = None
    notes: Notes = field(default_factory=Notes)

    @property
    def slug(self) -> str:
        path = urllib.parse.urlsplit(self.url).path.rstrip("/")
        slug = re.sub(r"[^a-z0-9-]+", "-", path.rsplit("/", 1)[-1].lower()).strip("-")
        return slug or f"event-{self.id}"

    @property
    def booking_url(self) -> str:
        """Ticket buttons go straight to the box office; Open Up only if there's no box office link."""
        return self.ticket_url or self.openup_url


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
                typographic(link.text()),
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


def without_size_suffix(url: str) -> str:
    """The full-size original of a resized WordPress image.

    WordPress names resized copies 'photo-700x350.png'; the original
    'photo.png' is kept alongside. Open Up also serves WebP copies from
    '/wp-content/webp-express/webp-images/doc-root/wp-content/uploads/...png.webp'.
    """
    url = re.sub(
        r"/wp-content/webp-express/webp-images/doc-root/(wp-content/uploads/.+)\.webp$", r"/\1", url
    )
    return re.sub(r"-\d+x\d+(?=\.\w+$)", "", url)


BOX_OFFICE_RE = re.compile(
    r"https?://(?:buytickets\.at|(?:www\.)?tickettailor\.com/events)/(?P<box>[\w-]+)/(?P<event>\d+)"
)


def box_office_link(url: str) -> str:
    """A Ticket Tailor event link, made to open ticket selection directly.

    'buytickets.at/leedsuniversity72/2255364' shows an event page first;
    adding '/select-date' (as Open Up's "Book now" does) goes straight to
    choosing tickets. Returns "" for links that aren't to the box office.
    """
    match = BOX_OFFICE_RE.match(url or "")
    if not match:
        return ""
    return f"https://buytickets.at/{match['box']}/{match['event']}/select-date"


def add_event_details(fetch: Fetch, concert: Concert) -> None:
    root = parse_html(fetch_text(fetch, concert.url))
    title = root.find("h1", cls="page-heading__title")
    if title and title.text():
        concert.title = typographic(title.text())
    for group in root.find_all(cls="uol-info-list__group"):
        term, data = group.find("dt"), group.find("dd")
        if not term or not data:
            continue
        label = term.text().lower()
        if label in ("location", "venue"):
            concert.venue = data.text()
        elif label in ("admission", "tickets", "price"):
            text = data.text()
            concert.admission = text[:1].upper() + text[1:]
        elif label == "doors":
            concert.doors = re.sub(r"^(open\s+)?(from\s+)?", "", data.text(), flags=re.I)
        link = data.find("a")
        if link and not concert.ticket_url:
            concert.ticket_url = box_office_link(link.attrs.get("href", ""))
    for button in root.find_all("a", cls="uol-button"):
        href = clean_href(button.attrs.get("href"))
        if href and not concert.ticket_url and "book" in button.text().lower():
            concert.ticket_url = box_office_link(href) or href
    image = root.find("img", cls="uol-featured-image__img")
    if image and image.attrs.get("src"):
        src = urllib.parse.urljoin(SITE, image.attrs["src"])
        concert.image_urls += [without_size_suffix(src), src]
    concert.notes = read_notes(root.find("div", cls="cms"))


# --------------------------------------------------------------------------
# Open Up (tickets) and YouTube (livestreams)
# --------------------------------------------------------------------------


def normalise_title(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def title_similarity(a: str, b: str) -> float:
    a, b = normalise_title(a), normalise_title(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.9
    words_a, words_b = set(a.split()), set(b.split())
    return len(words_a & words_b) / len(words_a | words_b)


@dataclass
class OpenUpEvent:
    url: str
    title: str
    date: dt.date | None
    time_text: str
    image_url: str


def parse_openup_listing(markup: str) -> list[OpenUpEvent]:
    events: dict[str, OpenUpEvent] = {}
    for card in parse_html(markup).find_all("li", cls="event-item"):
        link, heading = card.find("a"), card.find("h3")
        if not link or not heading or "/events/" not in link.attrs.get("href", ""):
            continue
        date = None
        for span in card.find_all("span"):
            if span.attrs.get("property") == "startDate":
                try:
                    date = dt.datetime.strptime(span.text(), "%d %B %Y").date()
                except ValueError:
                    pass
        time_el = card.find(cls="time")
        image = card.find("img", cls="wp-post-image") or card.find("img")
        url = link.attrs["href"]
        events.setdefault(
            url,
            OpenUpEvent(
                url=url,
                title=heading.text(),
                date=date,
                time_text=time_el.text() if time_el else "",
                image_url=image.attrs.get("src", "") if image else "",
            ),
        )
    return list(events.values())


def match_openup(concert: Concert, events: list[OpenUpEvent]) -> OpenUpEvent | None:
    """The Open Up page for a concert: same date, similar title."""
    best, best_score = None, 0.0
    for event in events:
        if event.date != concert.date:
            continue
        score = title_similarity(event.title, concert.title)
        times = parse_times(event.time_text)
        if times and concert.has_time and times[0] == concert.start.time():
            score += 0.2
        if score > best_score:
            best, best_score = event, score
    return best if best_score >= 0.5 else None


@dataclass
class Video:
    id: str
    title: str
    published: dt.datetime
    views: int


def parse_youtube_feed(xml_text: str) -> list[Video]:
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
        "media": "http://search.yahoo.com/mrss/",
    }
    videos = []
    for entry in ElementTree.fromstring(xml_text).findall("atom:entry", ns):
        video_id = entry.findtext("yt:videoId", "", ns)
        published = entry.findtext("atom:published", "", ns)
        stats = entry.find(".//media:statistics", ns)
        if not video_id or not published:
            continue
        videos.append(
            Video(
                id=video_id,
                title=entry.findtext("atom:title", "", ns),
                published=dt.datetime.fromisoformat(published),
                views=int(stats.get("views", "0")) if stats is not None else 0,
            )
        )
    return videos


def match_stream(concert: Concert, videos: list[Video]) -> Video | None:
    """This concert's livestream: same title, scheduled for it or streamed on the day."""
    for video in videos:
        if title_similarity(video.title, concert.title) < 0.9:
            continue
        scheduled = video.views == 0 and video.published < concert.start
        streamed = concert.start - dt.timedelta(hours=6) <= video.published <= concert.start + dt.timedelta(days=2)
        if scheduled or streamed:
            return video
    return None


def add_links_and_pictures(fetch: Fetch, concerts: list[Concert]) -> None:
    """Ticket pages and photos from Open Up, and livestreams from YouTube.

    These are extras: if either site can't be reached, the page still builds.
    """
    try:
        openup = parse_openup_listing(fetch_text(fetch, OPENUP_WHATS_ON))
    except Exception as exc:
        log.warning("Open Up listing unavailable, using the concert site's links: %s", exc)
        openup = []
    try:
        videos = parse_youtube_feed(fetch_text(fetch, YOUTUBE_FEED))
    except Exception as exc:
        log.warning("YouTube feed unavailable, using the channel's live link: %s", exc)
        videos = []
    for concert in concerts:
        event = match_openup(concert, openup)
        if event:
            concert.openup_url = event.url
            if event.image_url:
                # Open Up keeps a larger photo: drop the "-600x400" from its name.
                # Its small thumbnail is only a last resort.
                concert.image_urls = (
                    [without_size_suffix(event.image_url)] + concert.image_urls + [event.image_url]
                )
        video = match_stream(concert, videos)
        if video:
            concert.stream_url = f"https://www.youtube.com/watch?v={video.id}"


# --------------------------------------------------------------------------
# Images
# --------------------------------------------------------------------------


def prepare_picture(
    fetch: Fetch, urls: list[str], out_dir: Path, cache_dir: Path | None
) -> Picture | None:
    """Small WebP copies (800px and 1200px wide) of the first image that works."""
    urls = list(dict.fromkeys(url for url in urls if url))
    if not urls:
        return None
    try:
        from PIL import Image
    except ImportError:
        log.warning("Pillow is not installed; linking to the original image")
        return Picture(files=[(urls[-1], 0)])

    img_dir = out_dir / "img"
    img_dir.mkdir(parents=True, exist_ok=True)
    for url in urls:
        key = hashlib.sha1(url.encode()).hexdigest()[:16]
        if cache_dir and (cache_dir / f"{key}.missing").exists():
            continue
        cached = sorted(cache_dir.glob(f"{key}-*.webp")) if cache_dir else []
        if cached:
            files = []
            for path in cached:
                shutil.copyfile(path, img_dir / path.name)
                with Image.open(path) as image:
                    files.append((f"img/{path.name}", image.width, image.height))
            files.sort(key=lambda item: item[1])
            return Picture([(p, w) for p, w, _ in files], files[-1][1], files[-1][2])
        try:
            with Image.open(io.BytesIO(fetch(url))) as source:
                original = source.convert("RGB")
        except Exception as exc:
            log.info("Image not available (%s): %s", exc.__class__.__name__, url)
            if cache_dir and isinstance(exc, BuildError):
                cache_dir.mkdir(parents=True, exist_ok=True)
                (cache_dir / f"{key}.missing").touch()
            continue
        widths = sorted({min(width, original.width) for width in IMAGE_WIDTHS})
        files = []
        for width in widths:
            height = round(original.height * width / original.width)
            picture = original if width == original.width else original.resize(
                (width, height), Image.Resampling.LANCZOS
            )
            name = f"{key}-{width}.webp"
            picture.save(img_dir / name, "WEBP", quality=72, method=6)
            if cache_dir:
                cache_dir.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(img_dir / name, cache_dir / name)
            files.append((f"img/{name}", width, height))
        return Picture([(p, w) for p, w, _ in files], files[-1][1], files[-1][2])
    return None


# --------------------------------------------------------------------------
# Page output
# --------------------------------------------------------------------------

esc = html.escape


def fill(template: str, values: dict[str, str]) -> str:
    return re.sub(r"\{\{(\w+)\}\}", lambda m: values[m.group(1)], template)


def template(name: str) -> str:
    return (TEMPLATES / name).read_text(encoding="utf-8")


def format_time(value: dt.datetime) -> str:
    hour = value.hour % 12 or 12
    suffix = "am" if value.hour < 12 else "pm"
    return f"{hour}:{value.minute:02d}{suffix}"


def format_date(value: dt.date) -> str:
    return f"{value:%A} {value.day} {value:%B %Y}"


def short_date(value: dt.date) -> str:
    return f"{value:%a} {value.day} {value:%b}"


def time_range(concert: Concert) -> str:
    if concert.time_text:
        return re.sub(r"\s*-\s*", " – ", concert.time_text)
    return format_time(concert.start) if concert.has_time else ""


def status_label(concert: Concert, now: dt.datetime, default: str = "") -> str:
    """Same rules as statusLabel() in templates/app.js."""
    if concert.start <= now < concert.finish:
        return "On now"
    today = now.astimezone(TZ).date()
    if concert.date == today:
        return "Today"
    if concert.date == today + dt.timedelta(days=1):
        return "Tomorrow"
    return default


def iso(value: dt.datetime) -> str:
    return value.isoformat(timespec="minutes")


def timing_attrs(concert: Concert) -> str:
    return (
        f'data-start="{iso(concert.start)}" data-finish="{iso(concert.finish)}" '
        f'data-until="{iso(concert.until)}"'
    )


def external(url: str) -> str:
    return f'href="{esc(url)}" target="_blank" rel="noopener"'


PLAY_ICON = (
    '<svg class="icon" aria-hidden="true" viewBox="0 0 24 24" width="18" height="18">'
    '<path d="M8 5.5v13l11-6.5z" fill="currentColor"/></svg>'
)
TICKET_ICON = (
    '<svg class="icon" aria-hidden="true" viewBox="0 0 24 24" width="18" height="18">'
    '<path d="M3.5 7.5A1.5 1.5 0 0 1 5 6h14a1.5 1.5 0 0 1 1.5 1.5v2.25a2.25 2.25 0 0 0 0 4.5'
    'v2.25A1.5 1.5 0 0 1 19 18H5a1.5 1.5 0 0 1-1.5-1.5v-2.25a2.25 2.25 0 0 0 0-4.5z" '
    'fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/>'
    '<path d="M14.5 6.5v11" stroke="currentColor" stroke-width="1.7" stroke-dasharray="2 2"/></svg>'
)


def render_picture(picture: Picture | None, root: str, sizes: str, eager: bool = False) -> str:
    if not picture or not picture.files:
        return '<div class="picture-placeholder" aria-hidden="true"></div>'

    def src(path: str) -> str:
        return path if path.startswith("http") else root + path

    attrs = [f'src="{esc(src(picture.files[0][0]))}"']
    if len(picture.files) > 1:
        srcset = ", ".join(f"{esc(src(path))} {width}w" for path, width in picture.files)
        attrs += [f'srcset="{srcset}"', f'sizes="{sizes}"']
    if picture.width:
        attrs.append(f'width="{picture.width}" height="{picture.height}"')
    attrs.append('alt=""')
    attrs.append('fetchpriority="high"' if eager else 'loading="lazy"')
    attrs.append('decoding="async"')
    return f"<img {' '.join(attrs)}>"


def programme_href(concert: Concert, root: str) -> str:
    return f"{root}programmes/{concert.slug}/"


def booking_button(concert: Concert, extra_class: str = "") -> str:
    if not concert.booking_url:
        return ""
    return (
        f'<a class="btn btn-primary {extra_class}" {external(concert.booking_url)}>'
        f"{TICKET_ICON}<span>Book tickets</span></a>"
    )


def render_facts(concert: Concert) -> str:
    rows = [("Date", f'<time datetime="{iso(concert.start)}">{esc(format_date(concert.date))}</time>')]
    if time_range(concert):
        rows.append(("Time", esc(time_range(concert))))
    rows.append(("Venue", esc(concert.venue or DEFAULT_VENUE)))
    if concert.doors:
        rows.append(("Doors", esc(concert.doors)))
    if concert.admission:
        rows.append(("Tickets", esc(concert.admission)))
    items = "".join(f"<div><dt>{term}</dt><dd>{value}</dd></div>" for term, value in rows)
    return f'<dl class="facts">{items}</dl>'


def render_section(concert: Concert, key: str, title: str, body: str) -> str:
    if not body:
        return ""
    section_id = f"{key}-{concert.id}"
    return (
        f'<section class="prog-section prog-{key}" aria-labelledby="{section_id}">'
        f'<h2 class="section-title" id="{section_id}">{title}</h2>\n{body}\n</section>'
    )


def render_next_band(concert: Concert | None, root: str) -> str:
    """The 'Next event' advert, with a ticket button."""
    if concert is None:
        return ""
    when = format_date(concert.date)
    if concert.has_time:
        when += " · " + format_time(concert.start)
    artists = ", ".join(
        name for name, _ in concert.notes.artists[:4] if title_similarity(name, concert.title) < 1
    )
    return f"""
<section class="next-band" aria-labelledby="next-{concert.id}">
  <div class="wide next-inner">
    <a class="next-media" href="{programme_href(concert, root)}" tabindex="-1" aria-hidden="true">{render_picture(concert.picture, root, "(min-width: 760px) 420px, 100vw")}</a>
    <div class="next-text">
      <p class="kicker">Next event</p>
      <h2 class="next-title" id="next-{concert.id}"><a href="{programme_href(concert, root)}">{esc(concert.title)}</a></h2>
      <p class="next-when">{esc(when)}</p>
      {f'<p class="next-artists">{esc(artists)}</p>' if artists else ""}
      {f'<p class="next-price">{esc(concert.admission)}</p>' if concert.admission else ""}
      <div class="actions">
        {booking_button(concert, "btn-on-dark")}
        <a class="btn btn-ghost" href="{programme_href(concert, root)}">Read the programme</a>
      </div>
    </div>
  </div>
</section>"""


def render_programme(
    concert: Concert,
    following: Concert | None,
    root: str,
    now: dt.datetime,
    *,
    switchable: bool = False,
    visible: bool = True,
    eager: bool = False,
) -> str:
    """One programme, laid out the same way for every concert."""
    notes = concert.notes
    default = "Next concert" if switchable else ""
    label = status_label(concert, now, default) if visible else ""
    stream = ""
    if concert.stream_url:
        stream = f'<a class="btn btn-secondary" {external(concert.stream_url)}>{PLAY_ICON}<span>Watch the livestream</span></a>'
    about_title = "Notes &amp; biographies" if notes.repertoire_html else "About the concert"
    sections = "\n".join(
        part
        for part in (
            render_section(concert, "artists", "Artists", render_artists(notes.artists) if notes.artists else ""),
            render_section(concert, "repertoire", "Repertoire", notes.repertoire_html),
            render_section(concert, "about", about_title, notes.about_html),
        )
        if part
    ) or (
        '<p class="pending">The programme for this concert hasn’t been published yet. '
        "Please check back closer to the day.</p>"
    )
    data = f'data-concert="{esc(concert.id)}" ' if switchable else 'data-programme '
    return f"""
<article class="programme" id="concert-{esc(concert.id)}" {data}{timing_attrs(concert)}{"" if visible else " hidden"}>
  <header class="prog-hero">
    <div class="wide hero-inner">
      <figure class="hero-media">{render_picture(concert.picture, root, "(min-width: 960px) 560px, 100vw", eager)}</figure>
      <div class="hero-text">
        <p class="status" data-status data-default="{default}">{label}</p>
        <h1 class="prog-title">{esc(concert.title)}</h1>
        {render_facts(concert)}
        <div class="actions">{booking_button(concert)}{stream}</div>
      </div>
    </div>
  </header>
  <div class="narrow prog-body">
    <p class="etiquette">Please keep your phone on silent and your screen dimmed during the performance.</p>
{sections}
    <p class="source"><a {external(concert.url)}>This concert on the Concert Series website</a></p>
  </div>
  {render_next_band(following, root)}
</article>"""


def render_card(concert: Concert, root: str, now: dt.datetime, first: bool = False) -> str:
    when = short_date(concert.date) + f" {concert.date.year}"
    if concert.has_time:
        when += " · " + format_time(concert.start)
    names = [name for name, _ in concert.notes.artists if title_similarity(name, concert.title) < 1]
    artists = " · ".join(names[:4]) + (f" and {len(names) - 4} more" if len(names) > 4 else "")
    href = programme_href(concert, root)
    return f"""
<li class="card{" is-next" if first else ""}" {timing_attrs(concert)}>
  <a class="card-media" href="{href}" tabindex="-1" aria-hidden="true">{render_picture(concert.picture, root, "(min-width: 1100px) 360px, (min-width: 700px) 50vw, 100vw")}</a>
  <div class="card-body">
    <p class="card-when"><span class="status" data-status data-default="">{status_label(concert, now)}</span><time datetime="{iso(concert.start)}">{esc(when)}</time></p>
    <h2 class="card-title"><a href="{href}">{esc(concert.title)}</a></h2>
    {f'<p class="card-artists">{esc(artists)}</p>' if artists else ""}
    {f'<p class="card-price">{esc(concert.admission)}</p>' if concert.admission else ""}
    <div class="actions">
      {booking_button(concert, "btn-sm")}
      <a class="btn btn-quiet btn-sm" href="{href}">Programme</a>
    </div>
  </div>
</li>"""


def render_upcoming_item(concert: Concert, root: str, visible: bool) -> str:
    when = short_date(concert.date)
    if concert.has_time:
        when += " · " + format_time(concert.start)
    return (
        f'<li data-upcoming="{esc(concert.id)}" {timing_attrs(concert)}{"" if visible else " hidden"}>'
        f'<a href="{programme_href(concert, root)}"><span class="u-when">{esc(when)}</span>'
        f'<span class="u-title">{esc(concert.title)}</span></a></li>'
    )


def font_links(root: str) -> str:
    """Load Freight from Adobe Fonts, or preload the free fallback heading font."""
    if ADOBE_FONTS_KIT:
        return (
            '<link rel="preconnect" href="https://use.typekit.net" crossorigin>\n'
            f'<link rel="stylesheet" href="https://use.typekit.net/{ADOBE_FONTS_KIT}.css">'
        )
    return (
        f'<link rel="preload" href="{root}assets/fonts/playfair-display-latin-600-normal.woff2" '
        'as="font" type="font/woff2" crossorigin>'
    )


def render_page(
    *,
    root: str,
    title: str,
    description: str,
    main: str,
    now: dt.datetime,
    active: str = "",
    after_main: str = "",
    body_class: str = "",
) -> str:
    local_now = now.astimezone(TZ)
    generated = f"{local_now:%a} {local_now.day} {local_now:%b %Y}, " + format_time(local_now)
    values = {
        "root": root,
        "home": root or "./",
        "nav_programme": ' aria-current="page"' if active == "programme" else "",
        "nav_events": ' aria-current="page"' if active == "events" else "",
        "youtube_live": YOUTUBE_LIVE,
        "youtube": YOUTUBE_CHANNEL,
        "instagram": INSTAGRAM,
        "facebook": FACEBOOK,
        "openup": OPENUP + "/",
        "box_office": BOX_OFFICE,
        "concert_site": SITE + "/",
        "uol_logo": UOL_LOGO,
        "generated": generated,
    }
    return fill(
        template("base.html"),
        {
            **values,
            "title": esc(title),
            "description": esc(description),
            "body_class": body_class,
            "generated_iso": iso(now),
            "asset_version": ASSET_VERSION,
            "font_links": font_links(root),
            "header": fill(template("header.html"), values),
            "footer": fill(template("footer.html"), values),
            "main": main,
            "after_main": after_main,
        },
    )


def build_home(concerts: list[Concert], now: dt.datetime) -> str:
    current = current_concert(concerts, now)
    embedded = concerts[:EMBEDDED_CONCERTS]
    articles = "\n".join(
        render_programme(
            concert,
            concerts[index + 1] if index + 1 < len(concerts) else None,
            "",
            now,
            switchable=True,
            visible=concert is current,
        )
        for index, concert in enumerate(embedded)
    )
    later = [c for c in concerts if current is None or c.start >= current.start]
    later = [c for c in later if c is not current][:UPCOMING_LISTED]
    skip = 1 if current else 0  # the first is already advertised as "Next event"
    upcoming = "\n".join(
        render_upcoming_item(c, "", skip <= index < skip + UPCOMING_SHOWN)
        for index, c in enumerate(later)
    )
    empty = f"""
<section class="narrow empty" id="empty"{" hidden" if current else ""}>
  <p class="status">Programme notes</p>
  <h1 class="prog-title">No concert today</h1>
  <p>Notes for the next concert will appear here automatically. In the meantime, see what’s coming up.</p>
  <p><a class="btn btn-primary" href="events/">What’s on</a></p>
</section>"""
    coming_up = f"""
<aside class="coming-up" id="upcoming" data-show="{UPCOMING_SHOWN}" aria-labelledby="upcoming-heading"{"" if len(later) > skip else " hidden"}>
  <div class="narrow">
    <h2 class="section-title" id="upcoming-heading">Later in the season</h2>
    <ol class="upcoming-list">
{upcoming}
    </ol>
    <a class="more-link" href="events/">All upcoming concerts</a>
  </div>
</aside>"""
    return render_page(
        root="",
        title=f"Today’s programme · {SERIES_FULL_NAME}",
        description="Programme notes for today’s concert in the University of Leeds International Concert Series.",
        main=articles + empty,
        after_main=coming_up,
        now=now,
        active="programme",
        body_class="page-home",
    )


def build_events(concerts: list[Concert], now: dt.datetime) -> str:
    cards = "\n".join(render_card(c, "../", now, first=index == 0) for index, c in enumerate(concerts))
    main = f"""
<section class="wide page-intro">
  <p class="kicker">{SERIES_NAME}</p>
  <h1 class="page-title">What’s on</h1>
  <p class="lead">Concerts at the Clothworkers Centenary Concert Hall. Choose a concert to read its programme or book tickets.</p>
</section>
<section class="wide">
  <ol class="cards" id="event-list">
{cards}
  </ol>
  <p class="empty-list" id="no-events"{" hidden" if concerts else ""}>No concerts are listed just now. Please check back soon.</p>
</section>"""
    return render_page(
        root="../",
        title=f"What’s on · {SERIES_FULL_NAME}",
        description="Upcoming concerts in the University of Leeds International Concert Series, with programmes and tickets.",
        main=main,
        now=now,
        active="events",
        body_class="page-events",
    )


def build_programme_page(concert: Concert, following: Concert | None, now: dt.datetime) -> str:
    return render_page(
        root="../../",
        title=f"{concert.title} · Programme · {SERIES_FULL_NAME}",
        description=f"Programme for {concert.title}, {format_date(concert.date)}.",
        main=render_programme(concert, following, "../../", now, eager=True),
        now=now,
        body_class="page-programme",
    )


def build_not_found(site_url: str, now: dt.datetime) -> str:
    main = """
<section class="narrow empty">
  <p class="status">Page not found</p>
  <h1 class="prog-title">This page isn’t here</h1>
  <p>Programmes are removed once a concert has finished. Today’s programme and all upcoming concerts are a tap away.</p>
  <p class="actions"><a class="btn btn-primary" href="{root}">Today’s programme</a> <a class="btn btn-quiet" href="{root}events/">What’s on</a></p>
</section>""".replace("{root}", site_url)
    return render_page(root=site_url, title=f"Page not found · {SERIES_FULL_NAME}",
                       description="Page not found.", main=main, now=now)


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
    # omitsize: no fixed width/height, just a viewBox, so the code scales to fill its box.
    qr.save(buffer, kind="svg", scale=1, border=2, xmldecl=False, svgclass="qr-code",
            lineclass=None, omitsize=True)
    poster = fill(
        template("poster.html"),
        {
            "qr_svg": buffer.getvalue().decode("utf-8"),
            "site_url": esc(site_url),
            "site_url_short": esc(re.sub(r"^https?://|/$", "", site_url)),
            "uol_logo": UOL_LOGO,
            "font_links": font_links(""),
        },
    )
    (out_dir / "poster.html").write_text(poster, encoding="utf-8")


def copy_assets(out_dir: Path) -> None:
    assets = out_dir / "assets"
    if assets.exists():
        shutil.rmtree(assets)
    shutil.copytree(ROOT / "assets", assets)
    shutil.copyfile(TEMPLATES / "site.css", assets / "site.css")
    shutil.copyfile(TEMPLATES / "app.js", assets / "app.js")


def write_fingerprint(out_dir: Path) -> None:
    """A hash of the site (ignoring 'updated at' lines) so unchanged sites aren't republished."""
    digest = hashlib.sha256()
    for path in sorted(p for p in out_dir.rglob("*") if p.is_file()):
        if path.name == "build-hash.txt":
            continue
        data = path.read_bytes()
        if path.suffix == ".html":
            data = b"\n".join(line for line in data.split(b"\n") if b"data-generated" not in line)
        digest.update(str(path.relative_to(out_dir)).encode() + b"\0" + data)
    (out_dir / "build-hash.txt").write_text(digest.hexdigest() + "\n", encoding="utf-8")


def build(
    out_dir: Path,
    fetch: Fetch = fetch_url,
    now: dt.datetime | None = None,
    site_url: str | None = None,
    cache_dir: Path | None = None,
    images: bool = True,
) -> list[Concert]:
    now = now or dt.datetime.now(dt.timezone.utc)
    concerts = upcoming_concerts(fetch, now)[:MAX_PROGRAMMES]
    log.info("Found %d upcoming concert(s)", len(concerts))

    if out_dir.exists() and any(out_dir.iterdir()):
        if not (out_dir / "build-hash.txt").exists():
            raise BuildError(f"{out_dir} isn't empty and wasn't made by this script; choose another --out folder.")
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for concert in concerts:
        add_event_details(fetch, concert)
    add_links_and_pictures(fetch, concerts)
    for concert in concerts:
        if images:
            concert.picture = prepare_picture(fetch, concert.image_urls, out_dir, cache_dir)
        log.info(
            "  %s  %-45s tickets: %-10s stream: %s",
            concert.start.strftime("%a %d %b %H:%M"),
            concert.title[:45],
            "box office" if concert.ticket_url else ("Open Up" if concert.openup_url else "-"),
            "yes" if concert.stream_url else "-",
        )

    current = current_concert(concerts, now)
    log.info("Showing now: %s", current.title if current else "(no upcoming concert)")
    copy_assets(out_dir)
    (out_dir / "index.html").write_text(build_home(concerts, now), encoding="utf-8")
    (out_dir / "events").mkdir()
    (out_dir / "events" / "index.html").write_text(build_events(concerts, now), encoding="utf-8")
    for index, concert in enumerate(concerts):
        following = concerts[index + 1] if index + 1 < len(concerts) else None
        page_dir = out_dir / "programmes" / concert.slug
        page_dir.mkdir(parents=True, exist_ok=True)
        (page_dir / "index.html").write_text(
            build_programme_page(concert, following, now), encoding="utf-8"
        )
    if site_url:
        (out_dir / "404.html").write_text(build_not_found(site_url, now), encoding="utf-8")
        write_qr_and_poster(site_url, out_dir)
    write_fingerprint(out_dir)
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
    parser.add_argument("--site-url", help="public address of the site (for the QR code)")
    parser.add_argument("--cache", type=Path, help="folder to keep converted images between runs")
    parser.add_argument("--no-images", action="store_true", help="leave out photos")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    site_url = args.site_url or default_site_url()
    if site_url:
        # GitHub reports a new custom domain as http:// until its certificate is
        # ready; the QR code should always use the secure address.
        site_url = re.sub(r"^http://", "https://", site_url)
        if not site_url.endswith("/"):
            site_url += "/"
    try:
        build(args.out, site_url=site_url, cache_dir=args.cache, images=not args.no_images)
    except BuildError as exc:
        log.error("Build failed, the published site has not been changed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
