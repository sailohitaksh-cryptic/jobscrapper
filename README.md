# Job-search dashboard

A personal job-search board for a data scientist on OPT. It runs a rotating set
of LinkedIn searches through [JobSpy](https://github.com/Bunsly/JobSpy), keeps
only roles whose stated experience requirement is in range, flags sponsorship
signals, and lets you apply / dismiss from one page.

## What it does

- Scrapes LinkedIn guest results for each search term in `SEARCHES`.
- Parses each posting's description for the **minimum years of experience** and a
  **sponsorship signal** (sponsors / no-sponsorship / unknown).
- Stores everything in a single SQLite file (`jobs.db`), keyed on the JobSpy job
  id so re-scrapes dedupe automatically.
- Serves a one-page dashboard to filter by experience and sponsorship, search by
  keyword, and mark jobs **applied** / **dismissed**.

## Files

| File | Purpose |
|------|---------|
| `jobs_core.py` | the engine: scrape + parse + store. Runnable standalone. |
| `app.py` | FastAPI: reads/updates `jobs.db`, serves the page. |
| `static/index.html` | the dashboard (vanilla JS, no build step). |
| `tunnel.sh` | run the app + open a public Cloudflare tunnel link. |
| `test_app.py` | offline verification (parsing + API, no network). |

## Setup

Python 3.12, CPU-only. Using the `capstone` conda env:

```bash
conda activate capstone
pip install -r requirements.txt
```

## Run

Scrape once (populates `jobs.db`):

```bash
python jobs_core.py        # prints e.g. "added 17 new jobs"
```

Serve the dashboard:

```bash
uvicorn app:app --reload
# open http://127.0.0.1:8000
```

The dashboard also has a **Scrape now** button (on-demand scrape) so you don't
have to wait for cron.

The job list refreshes on a fixed **30-minute** cadence (and on **Scrape now**).
In between, filtering and apply/dismiss happen against a cached snapshot, so
working through the list never resurfaces newly-scraped jobs mid-session. The
header shows an `updated HH:MM` stamp for the current snapshot.

## Get a public link (Cloudflare Tunnel)

To reach the dashboard from your phone or anywhere via a simple `https://` link,
run it on your Mac and expose it with a free Cloudflare quick tunnel — no account
needed. This is the recommended setup: the scrape runs from your home IP (LinkedIn
blocks datacenter/cloud IPs, so a normal cloud host would scrape unreliably), and
the data stays on your machine.

```bash
brew install cloudflared        # one-time
conda activate capstone
./tunnel.sh
```

`tunnel.sh` starts the app and opens the tunnel; it prints a
`https://<random>.trycloudflare.com` URL — open that on any device. Press Ctrl-C
to stop both. The link is live only while your Mac is awake and the script is
running.

Notes:

- The quick-tunnel URL changes every run. For a permanent, bookmarkable link, use
  a [named Cloudflare tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
  (needs a domain on Cloudflare) or an ngrok static domain.
- There's no auth, so treat the URL as a secret while it's live.

## Configure

Edit the config block at the top of `jobs_core.py`:

- `SEARCHES` — the keyword searches to rotate through.
- `LOCATION`, `HOURS_OLD` (1 = LinkedIn's "past hour"), `RESULTS_PER_SEARCH`.
- `SLEEP_BETWEEN` — seconds between searches (be polite, avoid rate limits).
- `MY_YEARS`, `MAX_YEARS_OK` — a job is kept when its stated minimum is missing
  **or** `<= MAX_YEARS_OK`. An unstated requirement is kept (usually entry-level).

## Hourly scrape (cron)

cron doesn't load your shell profile, so point it at the env's Python directly.
Run `crontab -e` and add:

```cron
0 * * * * cd /path/to/jobscrapper && /opt/anaconda3/envs/capstone/bin/python jobs_core.py >> scrape.log 2>&1
```

Find your env's Python with `conda run -n capstone which python`. The job runs at
the top of every hour and appends output to `scrape.log`.

## Verify (no network)

```bash
python test_app.py
```

Unit-tests the parsers (including the "50 years of history" and "3-5 years"
cases) and exercises every API endpoint against a throwaway database — no live
LinkedIn scrape required.

## Roadmap (Phase 2)

Not built yet: Claude Haiku fallback for postings where the regex finds no year
requirement, more job sites (Indeed/Glassdoor/Google), a resume-overlap fit
score, and a company block-list.
