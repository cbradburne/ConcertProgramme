"""Tests for build.py, using saved copies of the real pages.

Run with:  python -m unittest discover -s tests
"""

import datetime as dt
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def london(year, month, day, hour=0, minute=0):
    return dt.datetime(year, month, day, hour, minute, tzinfo=build.TZ)


def notes_for(slug):
    root = build.parse_html(fixture(f"event-{slug}.html"))
    return build.read_notes(root.find("div", cls="cms"))


class FakeSite:
    """Serves the saved pages instead of the live websites."""

    def __init__(self, listing=None, broken=()):
        self.listing = listing
        self.broken = set(broken)
        self.requested = []

    def __call__(self, url):
        self.requested.append(url)
        if any(part in url for part in self.broken):
            raise build.BuildError(f"Could not download {url}")
        if url == build.OPENUP_WHATS_ON:
            name = "openup-whats-on.html"
        elif url == build.YOUTUBE_FEED:
            name = "youtube-feed.xml"
        elif "date-type=" in url:
            if self.listing is not None:
                return self.listing.encode()
            month = re.search(r"date-type=(\d{4})\.(\d{2})", url)
            name = f"listing-{month[1]}-{month[2]}.html" if month else "listing-current.html"
        elif url.startswith(build.SITE + "/events/"):
            name = "event-" + url.rstrip("/").rsplit("/", 1)[-1] + ".html"
        else:
            raise build.BuildError(f"Not available offline: {url}")
        path = FIXTURES / name
        if not path.exists():
            raise build.BuildError(f"Not available offline: {url}")
        return path.read_bytes()


class TimeParsingTests(unittest.TestCase):
    def test_site_formats(self):
        cases = {
            "1:05pm - 1:55pm": [dt.time(13, 5), dt.time(13, 55)],
            "7:30pm": [dt.time(19, 30)],
            "7:30 - 9:30pm": [dt.time(19, 30), dt.time(21, 30)],
            "11am – 12.30pm": [dt.time(11, 0), dt.time(12, 30)],
            "12:15pm": [dt.time(12, 15)],
            "open from 18:45pm": [dt.time(18, 45)],
            "19:30": [dt.time(19, 30)],
            "noon": [dt.time(12, 0)],
            "": [],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(build.parse_times(text), expected)

    def test_ignores_dates_and_plain_numbers(self):
        self.assertEqual(build.parse_times("Friday 13 November 2026"), [])

    def test_concert_without_end_time_gets_default_length(self):
        concert = build.make_concert("1", "X", "u", dt.date(2026, 11, 20), "7:30pm")
        self.assertEqual(concert.start, london(2026, 11, 20, 19, 30))
        self.assertEqual(concert.finish, london(2026, 11, 20, 21, 30))

    def test_concert_without_any_time_covers_the_day(self):
        concert = build.make_concert("1", "X", "u", dt.date(2026, 11, 20), "")
        self.assertFalse(concert.has_time)
        self.assertEqual(concert.start, london(2026, 11, 20))

    def test_summer_time(self):
        concert = build.make_concert("1", "X", "u", dt.date(2026, 6, 5), "1:05pm - 1:55pm")
        self.assertEqual(concert.start.isoformat(), "2026-06-05T13:05:00+01:00")


class ListingTests(unittest.TestCase):
    def test_reads_upcoming_concerts(self):
        concerts = build.parse_listing(fixture("listing-current.html"))
        self.assertEqual(len(concerts), 10)
        first = concerts[0]
        self.assertEqual(first.id, "5520")
        self.assertEqual(first.title, "Toby Shaer, Archie Moss and Jack Warnock")
        self.assertEqual(first.slug, "toby-shaer-archie-moss-and-jack-warnock")
        self.assertEqual(first.start, london(2026, 11, 13, 13, 5))
        self.assertEqual(first.finish, london(2026, 11, 13, 13, 55))
        self.assertEqual(first.time_text, "1:05pm - 1:55pm")

    def test_unrecognised_page_is_an_error(self):
        with self.assertRaises(build.BuildError):
            build.parse_listing("<html><body><p>Maintenance</p></body></html>")

    def test_merges_listings_without_duplicates(self):
        concerts = build.upcoming_concerts(FakeSite(), london(2026, 11, 7, 12))
        urls = [c.url for c in concerts]
        self.assertEqual(len(urls), len(set(urls)))
        self.assertEqual(len(concerts), 10)
        self.assertEqual([c.start for c in concerts], sorted(c.start for c in concerts))

    def test_event_details(self):
        concert = build.parse_listing(fixture("listing-current.html"))[0]
        build.add_event_details(FakeSite(), concert)
        self.assertEqual(concert.venue, "Clothworkers Centenary Concert Hall")
        self.assertEqual(concert.admission, "Pay what you can")
        self.assertEqual(concert.doors, "12:15pm")
        self.assertEqual(concert.ticket_url, "https://buytickets.at/leedsuniversity72/2255364/select-date")
        self.assertTrue(concert.image_urls[0].endswith("Toby-Shaer-Archie-Moss-Jack-Warnock-event-header.png"))


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.concerts = build.upcoming_concerts(FakeSite(), london(2026, 11, 7, 12))

    def showing(self, when):
        concert = build.current_concert(self.concerts, when)
        return concert.title if concert else None

    def test_next_concert_shown_in_advance(self):
        self.assertEqual(self.showing(london(2026, 11, 7, 12)), "Toby Shaer, Archie Moss and Jack Warnock")

    def test_lunchtime_concert_stays_until_45_minutes_after(self):
        self.assertEqual(self.showing(london(2026, 11, 13, 14, 39)), "Toby Shaer, Archie Moss and Jack Warnock")
        self.assertEqual(self.showing(london(2026, 11, 13, 14, 41)), "Dongyang Gozupa")

    def test_evening_concert_then_next_week(self):
        self.assertEqual(self.showing(london(2026, 11, 13, 21, 29)), "Dongyang Gozupa")
        self.assertEqual(self.showing(london(2026, 11, 13, 21, 31)), "Connaught Brass")

    def test_switches_early_when_next_concert_is_soon(self):
        day = dt.date(2026, 11, 13)
        first = build.make_concert("1", "A", "a", day, "1:05pm - 1:55pm")
        second = build.make_concert("2", "B", "b", day, "2:30pm")
        build.assign_until([first, second])
        self.assertEqual(first.until, london(2026, 11, 13, 14, 0))

    def test_nothing_left(self):
        self.assertIsNone(self.showing(london(2027, 6, 1)))


class NotesTests(unittest.TestCase):
    def test_artists_repertoire_and_notes_are_separated(self):
        notes = notes_for("ensemble-augelletti")
        self.assertEqual(
            notes.artists,
            [
                ("Olwen Foulkes", "recorders"),
                ("Ellen Bundy", "violin"),
                ("Kate Conway", "viola da gamba"),
                ("Toby Carr", "theorbo"),
                ("Benedict Williams", "harpsichord"),
            ],
        )
        self.assertIn("<h3>Jewels that brightly burn</h3>", notes.repertoire_html)
        self.assertEqual(notes.repertoire_html.count('<span class="composer">'), 7)
        self.assertIn(
            '<span class="composer">Johann Sebastian Bach</span> '
            '<span class="detail">(1685-1750) arr. Ensemble Augelletti</span>'
            '<span class="work">Sonata in G minor, BWV1029</span>',
            notes.repertoire_html,
        )
        self.assertNotIn("composer", notes.about_html)
        self.assertIn("Ensemble Augelletti specialise", notes.about_html)

    def test_artists_named_in_biographies(self):
        self.assertEqual(
            notes_for("toby-shaer-archie-moss-and-jack-warnock").artists,
            [
                ("Archie Moss", "accordion"),
                ("Toby Shaer", "fiddle, flute, whistles, cittern"),
                ("Jack Warnock", "guitar, vocals"),
            ],
        )
        self.assertEqual(
            notes_for("flamenco").artists,
            [("Monica Garcia", "singer"), ("Adrián Solá", "guitarist"), ("Marcos Morales", "dancer")],
        )
        self.assertEqual(notes_for("yue-qi").artists, [("Yue Qi", "violist")])

    def test_no_repertoire_means_everything_is_about_the_concert(self):
        notes = notes_for("toby-shaer-archie-moss-and-jack-warnock")
        self.assertEqual(notes.repertoire_html, "")
        self.assertIn("are a dynamic trio", notes.about_html)

    def test_works_in_separate_paragraphs_form_one_list(self):
        notes = notes_for("matteo-cimatti-nicola-eimer")
        self.assertEqual(notes.repertoire_html.count('<ul class="repertoire">'), 1)
        self.assertEqual(notes.repertoire_html.count('<span class="composer">'), 2)

    def test_mixed_paragraphs_are_split(self):
        notes = notes_for("student-showcase-62")
        self.assertIn(
            "<p>Performed by <strong>Lydia-Faith Smith</strong> (vocals) and "
            "<strong>Daniel Gordon</strong> (piano):</p>",
            notes.repertoire_html,
        )
        self.assertEqual(notes.repertoire_html.count('<ul class="repertoire">'), 5)
        self.assertIn(("Daniel Gordon", "piano"), notes.artists)
        self.assertEqual(len(notes.artists), 6)  # Daniel Gordon only once

    def test_quotes_stay_as_text(self):
        notes = notes_for("toby-shaer-archie-moss-and-jack-warnock")
        self.assertIn("Robert McMillen, The Irish News</p>", notes.about_html)
        self.assertIn("Jack Warnock’s voice", notes.about_html)  # curly apostrophe

    def test_outlook_safe_links_are_unwrapped(self):
        html = build.render_notes(
            build.parse_html(fixture("event-toby-shaer-archie-moss-and-jack-warnock.html")).find("div", cls="cms")
        )
        self.assertNotIn("safelinks", html)
        self.assertIn('href="https://leedscityoflanguages.leeds.ac.uk/"', html)

    def test_bold_does_not_leak_out_of_broken_markup(self):
        notes = notes_for("la-polemica")
        self.assertIn("<p>Clothworkers Centenary Concert Hall</p>", notes.about_html)
        self.assertEqual(len(notes.artists), 6)

    def test_video_thumbnail_becomes_a_link(self):
        notes = notes_for("dongyang-gozupa")
        self.assertIn(
            '<a href="https://youtu.be/KgqGy-nhEuU" target="_blank" rel="noopener">Watch the video</a>',
            notes.about_html,
        )

    def test_site_styling_is_removed(self):
        for slug in ("toby-shaer-archie-moss-and-jack-warnock", "diva-opera-la-cenerentola", "rolf-hind"):
            notes = notes_for(slug)
            html = notes.repertoire_html + notes.about_html
            with self.subTest(slug=slug):
                for unwanted in ("style=", "<h5", "<img", "&nbsp;"):
                    self.assertNotIn(unwanted, html)

    def test_unsafe_markup_is_dropped(self):
        root = build.parse_html(
            '<div class="cms"><p onclick="x()">Hello <script>alert(1)</script>'
            '<a href="javascript:alert(1)">there</a><iframe src="https://e.com"></iframe>'
            "<style>p{}</style></p></div>"
        )
        self.assertEqual(build.render_notes(root.find("div", cls="cms")), "<p>Hello there</p>")


class ImageTests(unittest.TestCase):
    def test_full_size_originals(self):
        cases = {
            "https://openup.leeds.ac.uk/wp-content/uploads/2026/06/g1qlojhgmtkpvbkx8h55-600x400.webp":
                "https://openup.leeds.ac.uk/wp-content/uploads/2026/06/g1qlojhgmtkpvbkx8h55.webp",
            "https://openup.leeds.ac.uk/wp-content/webp-express/webp-images/doc-root/wp-content/uploads/2026/09/Untitled-Design-17-600x400.png.webp":
                "https://openup.leeds.ac.uk/wp-content/uploads/2026/09/Untitled-Design-17.png",
            "https://concerts.leeds.ac.uk/wp-content/uploads/sites/101/2026/06/Header-700x350.png":
                "https://concerts.leeds.ac.uk/wp-content/uploads/sites/101/2026/06/Header.png",
            "https://concerts.leeds.ac.uk/wp-content/uploads/sites/101/2026/06/Header.png":
                "https://concerts.leeds.ac.uk/wp-content/uploads/sites/101/2026/06/Header.png",
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(build.without_size_suffix(url), expected)


class BoxOfficeTests(unittest.TestCase):
    def test_links_open_ticket_selection(self):
        cases = {
            "https://buytickets.at/leedsuniversity72/2255364": "https://buytickets.at/leedsuniversity72/2255364/select-date",
            "https://buytickets.at/leedsuniversity72/2255364/select-date": "https://buytickets.at/leedsuniversity72/2255364/select-date",
            "https://www.tickettailor.com/events/leedsuniversity72/2418052": "https://buytickets.at/leedsuniversity72/2418052/select-date",
            "https://openup.leeds.ac.uk/events/marco-fusi/": "",
            "": "",
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(build.box_office_link(url), expected)

    def test_open_up_only_without_a_box_office_link(self):
        concert = build.make_concert("1", "X", "u", dt.date(2026, 11, 20), "7:30pm")
        concert.openup_url = "https://openup.leeds.ac.uk/events/x/"
        self.assertEqual(concert.booking_url, concert.openup_url)
        concert.ticket_url = "https://buytickets.at/leedsuniversity72/1/select-date"
        self.assertEqual(concert.booking_url, concert.ticket_url)


class OpenUpAndYouTubeTests(unittest.TestCase):
    def setUp(self):
        self.concerts = build.parse_listing(fixture("listing-current.html"))
        self.events = build.parse_openup_listing(fixture("openup-whats-on.html"))
        self.videos = build.parse_youtube_feed(fixture("youtube-feed.xml"))

    def concert(self, title):
        return next(c for c in self.concerts if c.title.startswith(title))

    def test_matches_open_up_pages_even_with_different_names(self):
        flamenco = build.match_openup(self.concert("Flamenco"), self.events)
        self.assertEqual(flamenco.url, "https://openup.leeds.ac.uk/events/flamenco-monica-garcia-adrian-sola-marcos-morales/")
        dongyang = build.match_openup(self.concert("Dongyang"), self.events)
        self.assertEqual(dongyang.title, "Leeds Korean Day Festival: Dongyang Gozupa")
        self.assertIsNone(build.match_openup(self.concert("Samuel Moore"), self.events))
        matched = [c.title for c in self.concerts if build.match_openup(c, self.events)]
        self.assertEqual(len(matched), 6)

    def test_finds_the_scheduled_livestream(self):
        video = build.match_stream(self.concert("Toby Shaer"), self.videos)
        self.assertEqual(video.id, "6mF3M7acbGE")

    def test_does_not_reuse_an_old_stream_with_the_same_name(self):
        # October's "Student Showcase" stream is not December's.
        self.assertIsNone(build.match_stream(self.concert("Student Showcase"), self.videos))


class BuildTests(unittest.TestCase):
    def run_build(self, now, **kwargs):
        site = kwargs.pop("site", FakeSite())
        out = Path(self.enterContext(tempfile.TemporaryDirectory()))
        concerts = build.build(out, fetch=site, now=now, images=False, **kwargs)
        return out, (out / "index.html").read_text(encoding="utf-8"), concerts

    @staticmethod
    def visible_concerts(page):
        return re.findall(r'data-concert="(\d+)"[^>]*?data-until="[^"]+">', page)

    def test_site_pages(self):
        out, _, concerts = self.run_build(london(2026, 11, 7, 12), site_url="https://example.org/notes/")
        self.assertTrue((out / "events" / "index.html").exists())
        for concert in concerts:
            self.assertTrue((out / "programmes" / concert.slug / "index.html").exists(), concert.slug)
        for name in ("404.html", "build-hash.txt", "assets/site.css", "assets/app.js", "assets/uol-logo.svg"):
            self.assertTrue((out / name).exists(), name)
        for page in out.rglob("*.html"):
            self.assertNotIn("{{", page.read_text(encoding="utf-8"), page)

    def test_home_shows_todays_concert(self):
        _, page, _ = self.run_build(london(2026, 11, 13, 12))
        self.assertEqual(page.count('class="programme"'), build.EMBEDDED_CONCERTS)
        self.assertEqual(self.visible_concerts(page), ["5520"])
        self.assertIn('data-default="Next concert">Today</p>', page)
        self.assertIn('id="empty" aria-labelledby="empty-title" hidden>', page)
        self.assertIn("International<br>Concert Series", page)
        self.assertIn('aria-label="University of Leeds"', page)

    def test_home_moves_on_after_the_concert(self):
        _, page, _ = self.run_build(london(2026, 11, 13, 15))
        self.assertEqual(self.visible_concerts(page), ["5573"])

    def test_programme_layout_and_links(self):
        out, _, _ = self.run_build(london(2026, 11, 7, 12))
        page = (out / "programmes" / "toby-shaer-archie-moss-and-jack-warnock" / "index.html").read_text()
        for heading in ('>Artists</h2>', '>About the concert</h2>'):
            self.assertIn(heading, page)
        # Ticket buttons go straight to ticket selection at the box office.
        self.assertIn('href="https://buytickets.at/leedsuniversity72/2255364/select-date"', page)
        self.assertNotIn('href="https://openup.leeds.ac.uk/events/toby-shaer', page)
        self.assertIn('href="https://www.youtube.com/watch?v=6mF3M7acbGE"', page)
        self.assertIn(build.YOUTUBE_LIVE, page)
        # "Next event" advertises the following concert with a ticket button.
        band = page[page.index('class="next-band"'):]
        self.assertIn("Dongyang Gozupa", band)
        self.assertIn('href="https://buytickets.at/leedsuniversity72/2359314/select-date"', band)

        pixels = (out / "programmes" / "pixels-ensemble-6" / "index.html").read_text()
        for heading in ('>Artists</h2>', '>Repertoire</h2>', '>Notes &amp; biographies</h2>'):
            self.assertIn(heading, pixels)
        # Not on Open Up: still goes straight to the box office.
        self.assertIn("buytickets.at/leedsuniversity72/2510089/select-date", pixels)

    def test_whats_on_lists_every_concert(self):
        out, _, concerts = self.run_build(london(2026, 11, 7, 12))
        page = (out / "events" / "index.html").read_text()
        self.assertEqual(page.count('<li class="card'), len(concerts))
        self.assertEqual(page.count("is-next"), 1)
        for concert in concerts:
            self.assertIn(f'href="../programmes/{concert.slug}/"', page)

    def test_coming_up_skips_the_advertised_next_event(self):
        _, page, _ = self.run_build(london(2026, 11, 13, 12))
        items = re.findall(r'<li data-upcoming="(\d+)"[^>]*?( hidden)?>', page)
        self.assertEqual(items[0], ("5573", " hidden"))
        self.assertEqual(sum(1 for _, hidden in items if not hidden), build.UPCOMING_SHOWN)

    def test_open_up_or_youtube_down_still_builds(self):
        site = FakeSite(broken={"openup.leeds.ac.uk", "youtube.com"})
        out, _, concerts = self.run_build(london(2026, 11, 7, 12), site=site)
        self.assertFalse(any(c.openup_url or c.stream_url for c in concerts))
        page = (out / "programmes" / "connaught-brass" / "index.html").read_text()
        self.assertIn("buytickets.at/leedsuniversity72", page)

    def test_empty_listing_shows_placeholder(self):
        listing = (
            '<main><input type="hidden" name="post_type" value="tk_events">'
            '<p class="search-results__count">No results</p></main>'
        )
        out, page, _ = self.run_build(london(2026, 11, 7), site=FakeSite(listing=listing))
        events = (out / "events" / "index.html").read_text()
        self.assertNotIn('class="programme"', page)
        self.assertNotIn('<li class="card', events)
        for html in (page, events):
            # Shown (not hidden), with the message and a link to Open Up.
            self.assertRegex(html, r'<section class="notice[^"]*" id="(empty|no-events)" aria-labelledby="[\w-]+">')
            self.assertIn("No events currently listed", html)
            self.assertIn("Join us again soon for our next event.", html)
            self.assertIn("for other events across our campus.", html)
            self.assertIn('href="https://openup.leeds.ac.uk/"', html)
            self.assertIn('href="https://openup.leeds.ac.uk/whats-on/"', html)

    def test_no_events_message_hidden_while_concerts_are_listed(self):
        out, page, _ = self.run_build(london(2026, 11, 7, 12))
        events = (out / "events" / "index.html").read_text()
        self.assertIn('id="no-events" aria-labelledby="no-events-title" hidden>', events)
        self.assertNotIn("No events currently listed", page)  # home uses its own fallback
        self.assertIn('id="empty" aria-labelledby="empty-title" hidden>', page)

    def test_failed_download_stops_the_build(self):
        with self.assertRaises(build.BuildError):
            self.run_build(london(2026, 11, 7), site=FakeSite(broken={"events/yue-qi/"}))

    def test_refuses_to_empty_an_unrelated_folder(self):
        out = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (out / "precious.txt").write_text("keep me")
        with self.assertRaises(build.BuildError):
            build.build(out, fetch=FakeSite(), now=london(2026, 11, 7), images=False)
        self.assertTrue((out / "precious.txt").exists())

    def test_fingerprint_ignores_the_update_time(self):
        first, _, _ = self.run_build(london(2026, 11, 7, 12, 0))
        second, _, _ = self.run_build(london(2026, 11, 7, 12, 30))
        self.assertEqual((first / "build-hash.txt").read_text(), (second / "build-hash.txt").read_text())

    def test_freight_fonts_from_adobe(self):
        out, page, _ = self.run_build(london(2026, 11, 7, 12))
        self.assertIn(f'href="https://use.typekit.net/{build.ADOBE_FONTS_KIT}.css"', page)
        css = (out / "assets" / "site.css").read_text()
        for family in ("freight-display-pro", "freight-text-pro", "freight-sans-pro", "Playfair Display"):
            self.assertIn(family, css)

    def test_free_fonts_only(self):
        original = build.ADOBE_FONTS_KIT
        build.ADOBE_FONTS_KIT = ""
        try:
            _, page, _ = self.run_build(london(2026, 11, 7, 12))
        finally:
            build.ADOBE_FONTS_KIT = original
        self.assertNotIn("typekit", page)
        self.assertIn("playfair-display-latin-600-normal.woff2", page)

    def test_poster_and_qr_code(self):
        try:
            import segno  # noqa: F401
        except ImportError:
            self.skipTest("segno not installed")
        out, _, _ = self.run_build(london(2026, 11, 7), site_url="https://example.org/notes/")
        self.assertTrue((out / "qr.svg").exists())
        self.assertTrue((out / "qr.png").exists())
        poster = (out / "poster.html").read_text(encoding="utf-8")
        self.assertIn("example.org/notes", poster)
        self.assertIn("<svg", poster)
        # The poster's QR code scales to fill its box (viewBox, no fixed size).
        qr_tag = re.search(r'<svg[^>]*class="qr-code"[^>]*>', poster).group(0)
        self.assertIn("viewBox", qr_tag)
        self.assertNotIn("width=", qr_tag)
        self.assertIn("International<br>Concert Series", poster)


if __name__ == "__main__":
    unittest.main()
