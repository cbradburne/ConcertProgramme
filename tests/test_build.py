"""Tests for build.py, using saved copies of concerts.leeds.ac.uk pages.

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
    return build.render_notes(root.find("div", cls="cms"))


class FakeSite:
    """Serves the saved pages instead of the live website."""

    def __init__(self, listing=None, broken=()):
        self.listing = listing
        self.broken = set(broken)
        self.requested = []

    def __call__(self, url):
        self.requested.append(url)
        if "date-type=" in url:
            if self.listing is not None:
                return self.listing.encode()
            month = re.search(r"date-type=(\d{4})\.(\d{2})", url)
            name = f"listing-{month[1]}-{month[2]}.html" if month else "listing-current.html"
        elif url.startswith(build.SITE + "/events/"):
            slug = url.rstrip("/").rsplit("/", 1)[-1]
            if slug in self.broken:
                raise build.BuildError(f"Could not download {url}")
            name = f"event-{slug}.html"
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
        self.assertEqual(
            first.url, "https://concerts.leeds.ac.uk/events/toby-shaer-archie-moss-and-jack-warnock/"
        )
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
    def test_repertoire_and_performers(self):
        notes = notes_for("ensemble-augelletti")
        self.assertIn('<ul class="performers">', notes)
        self.assertEqual(notes.count('<span class="name">'), 5)
        self.assertIn('<span class="role">viola da gamba</span>', notes)
        self.assertIn("<h3>Jewels that brightly burn</h3>", notes)
        self.assertEqual(notes.count('<span class="composer">'), 7)
        self.assertIn(
            '<span class="composer">Johann Sebastian Bach</span> '
            '<span class="detail">(1685-1750) arr. Ensemble Augelletti</span>'
            '<span class="work">Sonata in G minor, BWV1029</span>',
            notes,
        )

    def test_works_in_separate_paragraphs_form_one_list(self):
        notes = notes_for("matteo-cimatti-nicola-eimer")
        self.assertEqual(notes.count('<ul class="repertoire">'), 1)
        self.assertEqual(notes.count('<span class="composer">'), 2)

    def test_mixed_paragraphs_are_split(self):
        notes = notes_for("student-showcase-62")
        self.assertIn(
            "<p>Performed by <strong>Lydia-Faith Smith</strong> (vocals) and "
            "<strong>Daniel Gordon</strong> (piano):</p>",
            notes,
        )
        self.assertIn('<span class="composer">Murray Grand</span>', notes)
        self.assertEqual(notes.count('<ul class="repertoire">'), 5)

    def test_quotes_and_biographies_stay_as_text(self):
        notes = notes_for("toby-shaer-archie-moss-and-jack-warnock")
        self.assertNotIn('class="composer"', notes)
        self.assertIn("<p><strong>Toby Shaer</strong> (fiddle, flute, whistles, cittern) is a", notes)
        self.assertIn("Robert McMillen, The Irish News</p>", notes)

    def test_outlook_safe_links_are_unwrapped(self):
        notes = notes_for("toby-shaer-archie-moss-and-jack-warnock")
        self.assertNotIn("safelinks", notes)
        self.assertIn('href="https://leedscityoflanguages.leeds.ac.uk/"', notes)

    def test_bold_does_not_leak_out_of_broken_markup(self):
        notes = notes_for("la-polemica")
        self.assertIn("<p>Clothworkers Centenary Concert Hall</p>", notes)
        self.assertEqual(notes.count('<span class="name">'), 6)

    def test_video_thumbnail_becomes_a_link(self):
        notes = notes_for("dongyang-gozupa")
        self.assertIn('<a href="https://youtu.be/KgqGy-nhEuU" target="_blank" rel="noopener">Watch the video</a>', notes)

    def test_site_styling_is_removed(self):
        for slug in ("toby-shaer-archie-moss-and-jack-warnock", "diva-opera-la-cenerentola", "rolf-hind"):
            notes = notes_for(slug)
            with self.subTest(slug=slug):
                self.assertNotIn("style=", notes)
                self.assertNotIn("<h5", notes)
                self.assertNotIn("<span style", notes)
                self.assertNotIn("<img", notes)
                self.assertNotIn("&nbsp;", notes)

    def test_unsafe_markup_is_dropped(self):
        root = build.parse_html(
            '<div class="cms"><p onclick="x()">Hello <script>alert(1)</script>'
            '<a href="javascript:alert(1)">there</a><iframe src="https://e.com"></iframe>'
            "<style>p{}</style></p></div>"
        )
        notes = build.render_notes(root.find("div", cls="cms"))
        self.assertEqual(notes, "<p>Hello there</p>")


class BuildTests(unittest.TestCase):
    def run_build(self, now, **kwargs):
        site = kwargs.pop("site", FakeSite())
        out = Path(self.enterContext(tempfile.TemporaryDirectory()))
        build.build(out, fetch=site, now=now, images=False, **kwargs)
        return out, (out / "index.html").read_text(encoding="utf-8")

    @staticmethod
    def visible_concerts(page):
        return re.findall(r'data-concert="(\d+)"[^>]*?data-until="[^"]+">', page)

    def test_page_shows_todays_concert(self):
        out, page = self.run_build(london(2026, 11, 13, 12))
        self.assertEqual(page.count('class="concert"'), build.EMBEDDED_CONCERTS)
        self.assertEqual(self.visible_concerts(page), ["5520"])
        self.assertIn('<p class="status" data-status>Today</p>', page)
        self.assertIn('id="empty" hidden', page)
        self.assertNotIn("{{", page)

    def test_page_moves_on_after_the_concert(self):
        _, page = self.run_build(london(2026, 11, 13, 15))
        self.assertEqual(self.visible_concerts(page), ["5573"])
        self.assertIn("Dongyang Gozupa", page)

    def test_coming_up_lists_later_concerts(self):
        _, page = self.run_build(london(2026, 11, 13, 12))
        items = re.findall(r'<li data-upcoming="(\d+)"[^>]*?(hidden)?>', page)
        self.assertEqual(items[0][0], "5573")
        self.assertNotIn("5520", [item for item, _ in items])
        self.assertEqual(sum(1 for _, hidden in items if not hidden), build.UPCOMING_SHOWN)

    def test_empty_listing_shows_placeholder(self):
        listing = (
            '<main><input type="hidden" name="post_type" value="tk_events">'
            '<p class="search-results__count">No results</p></main>'
        )
        _, page = self.run_build(london(2026, 11, 7), site=FakeSite(listing=listing))
        self.assertIn('id="empty">', page)
        self.assertNotIn('class="concert"', page)

    def test_failed_download_stops_the_build(self):
        site = FakeSite(broken={"dongyang-gozupa"})
        with self.assertRaises(build.BuildError):
            self.run_build(london(2026, 11, 7), site=site)

    def test_poster_and_qr_code(self):
        try:
            import segno  # noqa: F401
        except ImportError:
            self.skipTest("segno not installed")
        out, _ = self.run_build(london(2026, 11, 7), site_url="https://example.org/notes/")
        self.assertTrue((out / "qr.svg").exists())
        self.assertTrue((out / "qr.png").exists())
        poster = (out / "poster.html").read_text(encoding="utf-8")
        self.assertIn("example.org/notes", poster)
        self.assertIn("<svg", poster)


if __name__ == "__main__":
    unittest.main()
