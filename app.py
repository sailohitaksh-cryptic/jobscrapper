"""FastAPI app: reads/updates jobs.db and serves the dashboard."""

import os

from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from jobs_core import scrape_all, init_db, get_db

# absolute path so the page serves no matter where uvicorn is launched from
HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML = os.path.join(HERE, "static", "index.html")

app = FastAPI()

# make sure the table exists so a fresh clone doesn't 500 before the first scrape
init_db()


class StatusUpdate(BaseModel):
    status: str


@app.get("/")
def index():
    return FileResponse(INDEX_HTML)


@app.get("/api/jobs")
def list_jobs(status="new", max_years=100, hide_no_sponsor=False, q=""):
    clauses = []
    params = []
    # status defaults to 'new'; 'all' skips the status filter
    if status != "all":
        clauses.append("status = ?")
        params.append(status)
    # always cap by experience; an unstated requirement (NULL) always passes
    clauses.append("(min_years IS NULL OR min_years <= ?)")
    params.append(int(max_years))
    # hide explicit no-sponsor roles; NULL/unknown stay visible
    if str(hide_no_sponsor).lower() == "true":
        clauses.append("(sponsorship IS NULL OR sponsorship != 'no_sponsorship')")
    if q:
        clauses.append("(title LIKE ? OR company LIKE ?)")
        like = f"%{q}%"
        params.append(like)
        params.append(like)
    sql = "SELECT * FROM jobs WHERE " + " AND ".join(clauses) + " ORDER BY first_seen DESC"
    conn = get_db()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    jobs = []
    for r in rows:
        jobs.append(dict(r))
    return jobs


@app.get("/api/stats")
def stats():
    conn = get_db()
    rows = conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status").fetchall()
    conn.close()
    out = {}
    for r in rows:
        out[r["status"]] = r["n"]
    return out


@app.post("/api/jobs/{job_id}/status")
def set_status(job_id: str, body: StatusUpdate):
    conn = get_db()
    cur = conn.execute(
        "UPDATE jobs SET status = ? WHERE job_id = ?", (body.status, job_id)
    )
    conn.commit()
    changed = cur.rowcount
    conn.close()
    if changed == 0:
        return JSONResponse({"error": "job not found"}, status_code=404)
    return {"ok": True, "job_id": job_id, "status": body.status}


@app.post("/api/scrape")
def scrape(background: BackgroundTasks):
    # run in the background so the request returns immediately
    background.add_task(scrape_all)
    return {"started": True}
