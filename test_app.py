"""Offline verification: parsing unit tests + API tests against a temp DB.

Run with `python test_app.py`. Uses a throwaway SQLite file in a temp dir, so
no jobs.db is written into the repo and nothing hits LinkedIn.
"""

import os
import tempfile
import shutil

import jobs_core


def test_parsing():
    e = jobs_core.extract_min_years
    s = jobs_core.sponsorship_signal

    # ranges, modifiers, plurals, and the >20 noise case
    assert e("5+ years of experience") == 5
    assert e("we want 3-5 years experience") == 3
    assert e("at least 4 years of experience") == 4
    assert e("minimum of 3 years experience") == 3
    assert e("1 year of experience") == 1
    assert e("2 yrs experience preferred") == 2
    # "50 years of history" must not be read as a requirement
    assert e("a company with 50 years of history") is None
    assert e("no experience requirement listed") is None

    # sponsorship signals, incl. the OPT-friendly "authorized to work" case
    assert s("we will not sponsor visas") == "no_sponsorship"
    assert s("no sponsorship available") == "no_sponsorship"
    assert s("must be a U.S. citizen") == "no_sponsorship"
    assert s("visa sponsorship is available") == "sponsors"
    assert s("must be authorized to work in the US") == "unknown"
    assert s("nothing relevant here") == "unknown"

    # applicant counts off a job page, including the "over"/"first" wordings
    a = jobs_core.extract_applicants
    assert a("Over 200 applicants") == 200
    assert a("47 applicants") == 47
    assert a("Be among the first 25 applicants") == 25
    assert a("1 applicant") == 1
    assert a("no count shown") is None
    print("PASS  parsing")


def _seed():
    # (job_id, title, company, location, url, description, date_posted,
    #  job_level, search_term, min_years, sponsorship, applicants, status,
    #  first_seen)
    rows = [
        ("j1", "Junior Data Scientist", "Acme", "NYC", "http://x/1", "", None,
         "entry level", "data scientist", 1, "sponsors", 5, "new",
         "2026-06-22T10:00:00"),
        ("j2", "ML Engineer", "Beta", "Remote", "http://x/2", "", None,
         "mid-senior level", "machine learning engineer", None, "unknown", None,
         "new", "2026-06-22T11:00:00"),
        ("j3", "Senior Data Scientist", "Gamma", "SF", "http://x/3", "", None,
         "mid-senior level", "data science", 6, "unknown", 300, "new",
         "2026-06-22T12:00:00"),
        ("j4", "AI Engineer", "Delta", "Austin", "http://x/4", "", None,
         "associate", "ai engineer", 2, "no_sponsorship", 150, "new",
         "2026-06-22T13:00:00"),
    ]
    conn = jobs_core.get_db()
    for r in rows:
        conn.execute(
            "INSERT INTO jobs (job_id, title, company, location, url, "
            "description, date_posted, job_level, search_term, min_years, "
            "sponsorship, applicants, status, first_seen) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            r,
        )
    conn.commit()
    conn.close()


def _ids(jobs):
    out = set()
    for j in jobs:
        out.add(j["job_id"])
    return out


def test_api():
    tmp = tempfile.mkdtemp()
    # point the engine at a throwaway db before importing the app
    jobs_core.DB_PATH = os.path.join(tmp, "test.db")
    try:
        # imported here so init_db() at import uses the temp path
        import app as app_module
        from fastapi.testclient import TestClient

        # never hit LinkedIn from the scrape endpoint during tests
        app_module.scrape_all = lambda *a, **k: 0

        _seed()
        client = TestClient(app_module.app)

        # year cap drops the 6-year role (j3); NULL years (j2) always passes
        r = client.get("/api/jobs", params={"status": "new", "max_years": 4})
        ids = _ids(r.json())
        assert ids == {"j1", "j2", "j4"}, ids

        # applicant cap: <=100 keeps j1 (5) and the unknown j2 (NULL), drops
        # j3 (300) and j4 (150)
        r = client.get("/api/jobs", params={"status": "all", "max_years": 100,
                                            "max_applicants": 100})
        ids = _ids(r.json())
        assert ids == {"j1", "j2"}, ids

        # hide no-sponsor removes j4
        r = client.get("/api/jobs", params={"status": "new", "max_years": 4,
                                            "hide_no_sponsor": "true"})
        ids = _ids(r.json())
        assert ids == {"j1", "j2"}, ids

        # keyword search on title/company
        r = client.get("/api/jobs", params={"q": "engineer"})
        assert _ids(r.json()) == {"j2", "j4"}, _ids(r.json())
        r = client.get("/api/jobs", params={"q": "acme"})
        assert _ids(r.json()) == {"j1"}, _ids(r.json())

        # stats: everything starts in 'new'
        assert client.get("/api/stats").json().get("new") == 4

        # status update moves a job between tabs
        r = client.post("/api/jobs/j1/status", json={"status": "applied"})
        assert r.status_code == 200, r.text
        assert "j1" not in _ids(client.get("/api/jobs",
                                params={"status": "new"}).json())
        assert "j1" in _ids(client.get("/api/jobs",
                            params={"status": "applied", "max_years": 100}).json())
        stats = client.get("/api/stats").json()
        assert stats.get("new") == 3 and stats.get("applied") == 1, stats

        # unknown job -> 404
        assert client.post("/api/jobs/nope/status",
                           json={"status": "new"}).status_code == 404

        # scrape endpoint returns immediately (stubbed, no network)
        r = client.post("/api/scrape")
        assert r.status_code == 200 and r.json().get("started") is True

        print("PASS  api")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_parsing()
    test_api()
    print("all good")
