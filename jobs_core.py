"""Job-search engine: scrape LinkedIn (via JobSpy), parse, and store in SQLite.

Runnable standalone: `python jobs_core.py` runs one scrape pass and prints how
many new jobs were added.
"""

import re
import time
import sqlite3
from datetime import datetime, timezone

# ---- config (edit me) ----
# base role keywords to rotate through. edit this list freely.
ROLE_KEYWORDS = [
    "data scientist",
    "data science",
    "machine learning engineer",
    "ai engineer",
    "applied ai engineer",
    "llm engineer",
    "nlp engineer",
    "computer vision engineer",
    "data scientist, machine learning",
    "healthcare ai engineer",
    "generative ai engineer",
    "ai research engineer",
    "mlops engineer",
    "ml platform engineer",
    "research engineer, ai/ml",
]
# each keyword is also searched with this prefix, to surface junior roles
# (mirrors the wording of LinkedIn's "entry level ..." semantic search)
ENTRY_LEVEL_PREFIX = "entry level"
# the searches actually run: each keyword plain, then its entry-level variant
SEARCHES = []
for _kw in ROLE_KEYWORDS:
    SEARCHES.append(_kw)
    SEARCHES.append(ENTRY_LEVEL_PREFIX + " " + _kw)
LOCATION = "United States"
# matches LinkedIn's "past hour" filter
HOURS_OLD = 1
RESULTS_PER_SEARCH = 40
SLEEP_BETWEEN = 5
MY_YEARS = 2
MAX_YEARS_OK = 4
DB_PATH = "jobs.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    title TEXT,
    company TEXT,
    location TEXT,
    url TEXT,
    description TEXT,
    date_posted TEXT,
    job_level TEXT,
    search_term TEXT,
    min_years INTEGER,
    sponsorship TEXT,
    status TEXT DEFAULT 'new',
    first_seen TEXT
)
"""


def get_db(path=None):
    # default to the module-level DB_PATH so tests can monkeypatch it
    if path is None:
        path = DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path=None):
    conn = get_db(path)
    conn.execute(SCHEMA)
    conn.commit()
    conn.close()


# ---- parsing ----

# matches "5 years", "5+ yrs", "3-5 years", "3 to 5 years"; phrases like
# "at least"/"minimum of" are picked up via the bare "N years" inside them
YEAR_RE = re.compile(
    r"(\d+)(?:\s*(?:-|–|—|to)\s*(\d+))?\s*\+?\s*(?:years?|yrs?)",
    re.IGNORECASE,
)


def extract_min_years(text):
    """Return the minimum years of experience a posting asks for, or None."""
    if not text:
        return None
    # mentions sitting near the word "experience"
    qualifying = []
    # every plausible year-mention, used as a fallback
    fallback = []
    for m in YEAR_RE.finditer(text):
        low = int(m.group(1))
        # for a range like "3-5", take the lower bound
        if m.group(2):
            high = int(m.group(2))
            if high < low:
                low = high
        # ignore noise like "50 years of history"
        if low > 20:
            continue
        fallback.append(low)
        # is "experience" within ~45 chars on either side of this mention?
        start = max(0, m.start() - 45)
        window = text[start:m.end() + 45].lower()
        if "experience" in window:
            qualifying.append(low)
    if qualifying:
        return min(qualifying)
    if fallback:
        return min(fallback)
    return None


# word-boundary so "campus citizenship" doesn't trip the citizen check
CITIZEN_RE = re.compile(r"\bu\.?s\.?\s*citizen", re.IGNORECASE)


def sponsorship_signal(text):
    """Classify a posting as 'sponsors', 'no_sponsorship', or 'unknown'."""
    if not text:
        return "unknown"
    t = text.lower()
    # no-sponsorship signals come first: they matter most to someone on OPT
    no_phrases = (
        "will not sponsor",
        "not sponsor",
        "no sponsorship",
        "without sponsorship",
        "security clearance",
        "citizenship is required",
        "citizenship required",
    )
    for p in no_phrases:
        if p in t:
            return "no_sponsorship"
    if CITIZEN_RE.search(t):
        return "no_sponsorship"
    # positive signals. note: plain "must be authorized to work" is fine on OPT
    # and is deliberately NOT treated as a no-sponsorship flag
    yes_phrases = (
        "visa sponsorship",
        "will sponsor",
        "sponsorship available",
        "sponsorship is available",
    )
    for p in yes_phrases:
        if p in t:
            return "sponsors"
    return "unknown"


# ---- scraping ----

def _val(row, key):
    """Pull a field from a JobSpy row, normalising NaN/NaT/blank to None."""
    v = row.get(key)
    # NaN and NaT are the values that are not equal to themselves
    if v is None or v != v:
        return None
    s = str(v).strip()
    if s == "" or s.lower() == "nan":
        return None
    return v


def insert_job(conn, row, term):
    """Insert one unseen, in-range job. Return True if a row was added."""
    # job_id from JobSpy's id, fall back to the job url
    job_id = _val(row, "id")
    if job_id is None:
        job_id = _val(row, "job_url")
    if job_id is None:
        return False
    job_id = str(job_id)
    # dedupe: skip anything we've already stored
    cur = conn.execute("SELECT 1 FROM jobs WHERE job_id = ?", (job_id,))
    if cur.fetchone():
        return False
    description = _val(row, "description") or ""
    min_years = extract_min_years(description)
    # keep a job if no requirement is stated, or it is within range
    if min_years is not None and min_years > MAX_YEARS_OK:
        return False
    sponsorship = sponsorship_signal(description)
    date_posted = _val(row, "date_posted")
    if date_posted is not None:
        date_posted = str(date_posted)
    conn.execute(
        "INSERT INTO jobs (job_id, title, company, location, url, description, "
        "date_posted, job_level, search_term, min_years, sponsorship, status, "
        "first_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            job_id,
            _val(row, "title"),
            _val(row, "company"),
            _val(row, "location"),
            _val(row, "job_url"),
            description,
            date_posted,
            _val(row, "job_level"),
            term,
            min_years,
            sponsorship,
            "new",
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    return True


def scrape_all(path=None):
    """Run every search, store unseen in-range jobs, return the count added."""
    # imported here so the rest of the module (and the API) load without jobspy
    from jobspy import scrape_jobs

    init_db(path)
    conn = get_db(path)
    added = 0
    for i, term in enumerate(SEARCHES):
        # sleep between searches, not before the first or after the last
        if i > 0:
            time.sleep(SLEEP_BETWEEN)
        # one bad search shouldn't kill the whole run
        try:
            df = scrape_jobs(
                site_name=["linkedin"],
                search_term=term,
                location=LOCATION,
                results_wanted=RESULTS_PER_SEARCH,
                hours_old=HOURS_OLD,
                linkedin_fetch_description=True,
            )
        except Exception as e:
            print(f"search failed: {term!r} -> {e}")
            continue
        if df is None or len(df) == 0:
            continue
        for _, row in df.iterrows():
            if insert_job(conn, row, term):
                added += 1
        conn.commit()
    conn.close()
    return added


if __name__ == "__main__":
    n = scrape_all()
    print(f"added {n} new jobs")
