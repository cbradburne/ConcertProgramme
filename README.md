# International Concert Series: Programmes

A paper-free programme website for the
[University of Leeds International Concert Series](https://concerts.leeds.ac.uk/events/).
Audiences use **one link (or one QR code) every time**:

> **https://cbradburne.github.io/ConcertProgramme/**: today's programme
>
> https://cbradburne.github.io/ConcertProgramme/events/: What's on, with every upcoming concert
>
> https://cbradburne.github.io/ConcertProgramme/poster.html: printable QR poster

These addresses work once the one-time setup below is done.

## What's on the site

- **Today's programme** (the home page) always shows the current concert and switches by itself (see below).
- **What's on** lists every scheduled concert. Each has a photo, date and time, artists and price, with buttons to **book tickets** and read its **programme**. The next concert is featured at the top.
- **A programme page for every concert** (`/programmes/<name>/`). They're all set out the same way:
  1. the photo, title, date, start and end time, venue, door time and ticket price,
  2. **Book tickets** (straight to the box office) and, when one is scheduled, **Watch the livestream**,
  3. **Artists**: names and instruments,
  4. **Repertoire**: composer, dates and work, as in a printed programme,
  5. **Notes & biographies** (or **About the concert** when there's no list of works),
  6. a **Next event** panel advertising the following concert, with its own **Book tickets** button.
- **Watch live** is in the menu on every page. It goes to `youtube.com/@uolconcerts/live`, which YouTube points to the current stream (or the next scheduled one).
- University of Leeds and International Concert Series branding throughout: the official University logo, the University's **Freight** typefaces, the series teal (#008384) and the series tower icon as the browser/home-screen icon.

### Where the information comes from

| What | Source |
| --- | --- |
| Concerts, dates, times, venue, price, notes | Each event page on concerts.leeds.ac.uk |
| Ticket links | The "Book Tickets" link on the concert's page on concerts.leeds.ac.uk. It goes to the series' Ticket Tailor box office, with `/select-date` added (as Open Up's "Book now" does) so it opens ticket selection directly. The concert's Open Up page is used only if there's no box office link. |
| Photos | Open Up's **full-size** photo, found by removing the size from the end of the file name (e.g. `photo-600x400.webp` → `photo.webp`, 1507×1000). If there's none, the concert site's event header is used. That one is already full size, because its original upload is only 800×400. |
| Livestream links | The UOLConcerts YouTube channel's feed, matched by title for streams scheduled for that concert |

Open Up and YouTube are extras: Open Up supplies the larger photos, and YouTube the livestream links. If either can't be reached, the site still updates without them.

### How "today's programme" switches

The home page contains the next four concerts, and a tiny script picks the right one from the visitor's clock:

- It shows the next concert in advance, labelled "Next concert", "Tomorrow", "Today" or "On now".
- It keeps showing a concert for **45 minutes after it ends**, then moves to the next one.
- If two concerts are close together, it moves on **30 minutes before the next one starts**. For example, on 13 Nov it shows the 1:05pm concert until 2:40pm, then the 7:30pm concert.

### How it updates

- Every 30 minutes during the day, a GitHub Action (`.github/workflows/publish.yml`) runs `build.py`. The script reads the concert site, Open Up and YouTube, rebuilds the site and publishes it with GitHub Pages. If nothing has changed, nothing is republished.
- Edits to an event on the concert site appear within 30 minutes. To update straight away, use **Actions → Update programme notes → Run workflow**.
- If the concert website is down or its layout changes, the run fails, the **last good version stays online**, and the failed run shows up in the Actions tab.

### Designed for the concert hall, and light on energy

- It follows the phone's dark mode, and there's a light/dark button; dark mode also dims photos. A text-size button offers three sizes. Both settings are remembered on that phone only.
- Pages are small static files: 15–40 KB each, plus 28 KB of shared styles and script, and photos converted to WebP (around 40–70 KB each, compared with 300–600 KB originals).
- No cookies and no third-party scripts.

### Fonts

The site uses the University's typefaces: **Freight Display** for titles, **Freight Text** for programme notes and **Freight Sans** for labels and buttons. They come from the University's Adobe Fonts kit (`vlw5ilb`), the same one concerts.leeds.ac.uk uses. Freight is licensed through Adobe, so it can only be used through a kit like this, never copied into the site.

- Adobe counts page views for licensing, so visitors' browsers also load a tiny file from `p.typekit.net`.
- If the kit can't be used (blocked, or switched off), the site automatically falls back to **Playfair Display** (free, stored in the site) and system fonts.
- Please check with the University's web team that it's fine to use their kit on this site. If they'd rather you had your own, create an Adobe Fonts web project with Freight Display, Text and Sans (regular and semibold, with italics), then put its kit ID in `ADOBE_FONTS_KIT` at the top of `build.py`. Set it to `""` to use the free fonts only.

## One-time setup

Do these in this order:

1. **Make the repository public** (done). GitHub Pages is free for public repositories.
2. **Make `main` the default branch:** *Settings → General → Default branch* → switch to **main**. Scheduled updates only run on the default branch, and Pages only accepts updates from it.
3. **Turn on GitHub Pages:** *Settings → Pages → Build and deployment → Source:* **GitHub Actions**. There's no need to pick a branch or folder.
4. **Run it once:** *Actions → Update programme notes → Run workflow* (branch: **main**). After about a minute the site is live.
5. **Print the poster** from `/poster.html`, or use `/qr.png` / `/qr.svg` on seat cards, screens or slides. The QR code never changes, so laminated copies can be reused.

*Optional:* to use a university address instead (e.g. `programme.concerts.leeds.ac.uk`), ask IT for a DNS `CNAME` record pointing to `cbradburne.github.io`, then add it under *Settings → Pages → Custom domain*. Do this **before** printing QR codes.

## Tips for whoever writes the event pages

Nothing extra needs filling in, but these patterns are recognised and laid out like a printed programme:

| Write on the event page | Shown as |
| --- | --- |
| **Johann Sebastian Bach** (1685-1750) - Sonata in G minor, BWV1029 | **Repertoire**: composer in capitals, dates, work underneath |
| **Robert Schumann** - Sonata no 1 in A minor, op 105 | **Repertoire** (dates are optional) |
| **Olwen Foulkes** (recorders) **Ellen Bundy** (violin) … | **Artists** list |
| …violinist **Matteo Cimatti** … or **Toby Shaer** (fiddle) is … in a biography | Also added to **Artists** |
| A short line that is all bold, e.g. **Jewels that brightly burn** | A heading |

- Put **one work per line**, with the composer in **bold**, then a dash, then the work.
- Everything after the last work appears under **Notes & biographies**.
- Pictures and logos inside the text are left out to keep pages small. YouTube links become "Watch the video".
- **Book tickets** buttons use the box office link in the event's details on the concert site, so make sure each event has one.
- For a concert to use **Open Up's larger photo**, give it the **same date and a similar title** on Open Up.
- For a programme to link to **its own livestream**, give the YouTube stream the **same title** as the concert. Otherwise the menu's *Watch live* still works.

## Changing the behaviour

Settings are at the top of `build.py`:

| Setting | Default | Meaning |
| --- | --- | --- |
| `AFTER_CONCERT` | 45 minutes | How long a concert stays on the home page after it ends |
| `BEFORE_NEXT` | 30 minutes | Switch early if the next concert starts within this time |
| `DEFAULT_LENGTH` | 2 hours | Assumed length when only a start time is published |
| `EMBEDDED_CONCERTS` | 4 | Concerts built into the home page, so it keeps switching correctly even if updates stop for a while |
| `UPCOMING_SHOWN` | 4 | Concerts under "Later in the season" on the home page |
| `IMAGE_WIDTHS` | 800, 1200 | Sizes of the converted photos |
| `ADOBE_FONTS_KIT` | `vlw5ilb` | Adobe Fonts kit for Freight (`""` = free fonts only) |

Colours and fonts are at the top of `templates/site.css`. To change how often it updates, edit the `cron` line in `.github/workflows/publish.yml` (times are in UTC).

`keepalive.yml` makes a one-line commit if the repository has been quiet for 50 days. Without it, GitHub would pause the schedule in a public repository, for example over the summer.

## Running it on your own computer

```sh
pip install -r requirements.txt        # optional: photo shrinking, QR code, time zone data
python build.py --out _site --site-url https://cbradburne.github.io/ConcertProgramme/
python -m http.server --directory _site 8000   # then open http://localhost:8000
python -m unittest discover -s tests   # tests use saved copies of the real pages
```

## Files

| Path | Purpose |
| --- | --- |
| `build.py` | Reads the concert site, Open Up and YouTube, and writes the site |
| `templates/` | Page layout (`base.html`, `header.html`, `footer.html`), styles (`site.css`), the small script (`app.js`) and the poster |
| `assets/` | University logo, series icon, and the fallback Playfair Display font (SIL Open Font Licence, `assets/fonts/OFL.txt`) |
| `tests/` | Automated tests, using trimmed copies of the real pages (saved 7 Nov 2026) |
| `.github/workflows/publish.yml` | Scheduled build and publish to GitHub Pages |
| `.github/workflows/keepalive.yml` | Stops GitHub pausing the schedule in quiet periods |
