# Concert Programme Notes

A single web page that always shows the programme notes for the **current concert** of the
[University of Leeds Concert Series](https://concerts.leeds.ac.uk/events/), replacing paper programmes.

Audiences use **one link (or one QR code) every time**:

> **https://cbradburne.github.io/ConcertProgramme/**
>
> Printable QR poster: https://cbradburne.github.io/ConcertProgramme/poster.html

These addresses work once the one-time setup below is done.

## How it works

- Every 30 minutes during the day, a GitHub Action (`.github/workflows/publish.yml`) runs `build.py`. The script:
  1. reads the What's On listing at `concerts.leeds.ac.uk/events/`,
  2. downloads the event pages of the next four concerts,
  3. turns their notes into a clean, mobile-friendly page,
  4. publishes it with GitHub Pages. If nothing has changed, the run publishes nothing.
- **Programme notes come straight from each event's page** on the Concert Series website. Edit an event there, and the page picks up the change within 30 minutes. To update straight away, use **Actions → Update programme notes → Run workflow**.
- **The page switches between concerts by itself**, using the clock on the visitor's phone:
  - It shows the next concert in advance ("Next concert", "Tomorrow", "Today", "On now").
  - It keeps showing a concert for **45 minutes after it ends**, then moves to the next one.
  - If two concerts are close together, it moves on **30 minutes before the next one starts**. For example, on 13 Nov it shows the 1:05pm concert until 2:40pm, then the 7:30pm concert.
- **Lists of works and performers are laid out like a printed programme** when they're written in the site's usual style (see the tips below). Everything else appears as normal paragraphs.
- **"Coming up"** at the bottom lists the next four concerts, with links to book.

### Designed for the concert hall

- It follows the phone's dark mode. There's also a light/dark button, and dark mode dims the photo, so screens don't light up the hall.
- A text-size button offers three sizes. Both settings are remembered on that phone only.
- A gentle reminder to keep phones silent and screens dimmed.

### Light on energy and data

- The page is about 30 KB, plus one header photo converted to WebP: about 40 KB instead of the original PNG of around 600 KB.
- No web fonts, cookies, trackers or third-party scripts. It's a static page served by GitHub Pages.
- Images are cached between runs, so the concert website is only asked for a few small pages each time.

## One-time setup

1. **Make this the `main` branch.** The work is on branch `claude/concert-programme-auto-update-0fluq1`. Since it's the only branch, the simplest option is to rename it to `main` (on GitHub: *Branches* → ⋯ → *Rename branch*), or create `main` from it. Scheduled runs only happen on the default branch.
2. **Check the repository's visibility.** GitHub Pages is free for **public** repositories. A private repository needs a paid GitHub plan (Pro/Team). Nothing in this repository is secret, so making it public is fine: *Settings → General → Danger Zone → Change visibility*.
3. **Turn on GitHub Pages:** *Settings → Pages → Build and deployment → Source:* **GitHub Actions**.
4. **Run it once:** *Actions → Update programme notes → Run workflow*. After a minute the page is live at the address above.
5. **Print the poster** from `/poster.html` (A4), or use `/qr.png` or `/qr.svg` for seat cards, screens or slides. The QR code never changes, so laminated copies can be reused every concert.

*Optional:* to use a university address instead (e.g. `programme.concerts.leeds.ac.uk`), ask IT for a DNS `CNAME` record pointing to `cbradburne.github.io`, then add it under *Settings → Pages → Custom domain*. Set this up **before** printing QR codes, because the QR code contains the address.

## Tips for whoever writes the notes on the website

The notes are taken from the main text of each event page, so nothing extra needs to be filled in. These patterns are recognised and laid out nicely:

| Write on the event page | Shown as |
| --- | --- |
| **Johann Sebastian Bach** (1685-1750) - Sonata in G minor, BWV1029 | A programme entry: composer in capitals, dates, work underneath |
| **Robert Schumann** - Sonata no 1 in A minor, op 105 | A programme entry (dates are optional) |
| **Olwen Foulkes** (recorders) **Ellen Bundy** (violin) … | A list of performers and instruments |
| A short line that is all bold, e.g. **Jewels that brightly burn** | A heading |

- Put **one work per line**, with the composer in **bold**, then a dash, then the work.
- Write programme notes for each work as normal paragraphs below the list.
- Pictures and logos inside the text are left out to keep the page small. The event's main header image is shown at the top. YouTube links become a "Watch the video" link.

## Changing the behaviour

Settings are at the top of `build.py`:

| Setting | Default | Meaning |
| --- | --- | --- |
| `AFTER_CONCERT` | 45 minutes | How long a concert stays on the page after it ends |
| `BEFORE_NEXT` | 30 minutes | Switch early if the next concert starts within this time |
| `DEFAULT_LENGTH` | 2 hours | Assumed length when only a start time is published |
| `EMBEDDED_CONCERTS` | 4 | Concerts built into the page, so it keeps switching correctly even if updates stop for a while |
| `UPCOMING_SHOWN` | 4 | Concerts listed under "Coming up" |

To change how often it updates, edit the `cron` line in `.github/workflows/publish.yml`. Times there are in UTC.

`keepalive.yml` makes a one-line commit if the repository has been quiet for 50 days. Without it, GitHub would pause the schedule in a public repository, for example over the summer break.

If the concert website is down or its layout changes, the run fails, the **last good page stays online**, and the failed run shows up in the Actions tab (GitHub normally emails a notification too).

## Running it on your own computer

```sh
pip install -r requirements.txt        # optional: image shrinking, QR code, time zone data
python build.py --out _site --site-url https://cbradburne.github.io/ConcertProgramme/
python -m unittest discover -s tests   # tests use saved copies of real event pages
```

Then open `_site/index.html` in a browser.

## Files

| Path | Purpose |
| --- | --- |
| `build.py` | Reads the Concert Series website and writes the page |
| `templates/page.html`, `style.css`, `app.js` | The page, its styles, and the small script that picks the current concert |
| `templates/poster.html` | The printable QR poster |
| `tests/` | Automated tests, using trimmed copies of real event pages (saved 7 Nov 2026) |
| `.github/workflows/publish.yml` | Scheduled build and publish to GitHub Pages |
| `.github/workflows/keepalive.yml` | Stops GitHub pausing the schedule in quiet periods |
