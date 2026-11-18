# Tech@NYU Buildathon Judging

A working judging platform for a Tech@NYU buildathon. Judges score assigned projects from a phone. Organizers run projects, judges, the schedule, and results from a desktop dashboard.

Quality scores (technical execution, innovation, impact, presentation) are separate from track alignment. A weak track fit does not lower the other scores. Drafts autosave to Postgres and never count as complete.

## Run with Docker

```bash
export JWT_SECRET="$(openssl rand -hex 32)"
docker compose up --build
```

`JWT_SECRET` is required. The API will not start with a missing key or with the old published default. Compose reads the variable from the environment and does not bake a signing key into the file.

Open [http://localhost:8080](http://localhost:8080).

- The web app is on port 8080 and proxies `/api` to the backend.
- The API is also on [http://localhost:8000](http://localhost:8000).
- Postgres is on `localhost:5432` (`judging` / `judging`, database `judging`).

The API creates tables on startup and loads the demo seed if the database is empty.

## Run locally

You need Python 3.12, Node 22, and PostgreSQL 16.

```bash
sudo -u postgres psql -c "CREATE USER judging WITH PASSWORD 'judging' CREATEDB;"
sudo -u postgres psql -c "CREATE DATABASE judging OWNER judging;"
sudo -u postgres psql -c "CREATE DATABASE judging_test OWNER judging;"

cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export JWT_SECRET="$(openssl rand -hex 32)"
python -m app.seed
uvicorn app.main:app --reload --port 8000
```

In another shell:

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173). Vite proxies `/api` to port 8000.

Reseed from scratch with:

```bash
cd backend && python -m app.seed --reset
```

## Demo logins

| Role | Name | Email | Password |
| --- | --- | --- | --- |
| Organizer | Avery Shah | organizer@tech.nyu.edu | organizer-demo |
| Judge | Alex Rivera | alex.rivera@nyu.edu | judge-demo |
| Judge | Jordan Lee | jordan.lee@nyu.edu | judge-demo |
| Judge | Maya Chen | maya.chen@nyu.edu | judge-demo |
| Judge | Priya Patel | priya.patel@nyu.edu | judge-demo |
| Judge | Riley Nguyen | riley.nguyen@nyu.edu | judge-demo |
| Judge | Sam Okonkwo | sam.okonkwo@nyu.edu | judge-demo |

The login screen can sign in as the organizer or as Maya directly. Every judge uses `judge-demo`.

The seed is a judging day anchored to the current clock: 6 tracks, 6 judges, 18 projects, schedules across three rooms, and evaluations in every status. GreenRoute has a scoring discrepancy. QuizForge is flagged for Artificial Intelligence by two judges. ShelterLink is a missed start, and OfficeHours+ is running long. Times are America/New_York.

`examples/projects-import.csv` is a sample file for the organizer import.

## Tests

```bash
cd backend
source .venv/bin/activate
pytest
```

Pytest uses the `judging_test` database and will not touch the demo data. The suite covers role checks, private-note isolation, autosave status changes, reopen audit history, timer extensions, and track rankings that ignore drafts.

## How the pieces fit

- Judges only receive projects they are assigned. Another judge's project returns 404.
- Private notes stay on the judge's evaluation. Organizer pages and both CSV exports omit them.
- Opening a project marks it viewed. Partial input becomes a draft. A complete scorecard is ready to submit. Submit locks it.
- Organizers can reopen a submitted scorecard. Later score changes are written to an audit log.
- One evaluation row exists per judge and project (`uq_evaluation_judge_project`).
- Presentation timers live on the server: start, pause, reset, a one-minute warning, time-up, and actual start and end. Organizer extensions add minutes to the scheduled duration. The timer does not submit or block scores.
- Results average submitted quality scores only, rank inside each track, and flag a discrepancy when overall scores differ by 1.5 or more, or any criterion differs by 2 or more.
- Cross-track flags are a review list. They do not move a project or change its rank.
- Organizer progress polls every few seconds.

## Known limits

- Live updates use polling, not websockets.
- The UI creates judge accounts. Additional organizer accounts are not created from the dashboard.
- There is no emailed password reset. Organizers set judge passwords when they create accounts.
- The one-minute and time-up notices are on-screen banners.
- A submitted evaluation cannot be reassigned or deleted, so finished scores stay attached to the judge who wrote them.
