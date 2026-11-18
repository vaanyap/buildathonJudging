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
| Organizer | Avery Shah | vp2852@nyu.edu | organizer-demo |
| Judge | Alex Rivera | vpuri22@uwo.ca | judge-demo |

The login screen can sign in as the organizer or as Maya directly. Every judge uses `judge-demo`.

The seed imports `examples/submissions_export.csv`, the MLH/Devpost submissions export. Prize categories become tracks, and a project can belong to more than one. Six demo judges are assigned across those tracks. JARVIS in Best Open-Source AI Project is a scoring discrepancy. CityLine is flagged for the SpaceX Super Intelligence Track. JARVIS is in Q&A for Alex, and Droplet's presentation timer is running for Jordan.

`examples/submissions_export.csv` is the organizer import file. Re-importing it updates projects by Page URL.

The seed also loads `examples/sign_in.csv`, a day-of sign-in list. LIFTR and Hodlings are missing entirely, Stephen MO on JARVIS is only a possible match, and Romir Basu and Emily Yang are not on the list.

## Tests

```bash
cd backend
source .venv/bin/activate
pytest
```

Pytest uses the `judging_test` database and will not touch the demo data. The suite covers role checks, private-note isolation, autosave status changes, reopen audit history, timer extensions, and track rankings that ignore drafts.

## How the pieces fit

- Judges are assigned to prize-category tracks and score every project in those tracks. A project in several tracks is a separate scorecard in each one (`uq_evaluation_judge_project_track`). Another judge's project returns 404.
- Private notes stay off organizer results, winners, and exports. On an active discrepancy, the judges whose scores caused the flag have their notes shown, labeled as visible because of the discrepancy, and each view is written to that evaluation's audit trail.
- Opening a scorecard marks it viewed. Partial input becomes a draft. A complete scorecard is ready to submit. Submit locks it.
- Organizers can reopen a submitted scorecard from results or the discrepancy review. Later score changes are written to an audit log.
- One presentation timer is shared by a judge across every track scorecard for that project. Presentation defaults to 3 minutes and Q&A to 2. The judge starts Q&A manually. Each phase has start, pause, reset, a warning, time-up, and its own actual start and end. Organizer extensions add minutes to one phase. The timer does not submit or block scores.
- Results average submitted quality scores only and rank inside each track. A discrepancy is an overall spread of 1.5 or more, or a criterion where one submitted score is 4–5 and another is 1–2. Organizers mark it open, discussing, or resolved. Results and winners keep the badge until it is resolved.
- Winner summaries are built from submitted formal feedback. An optional `WINNER_SUMMARY_API_URL` can rewrite the narrative; if it is unset or the call fails, the rule-based summary is used.
- Cross-track flags are a review list. They do not move a project or change its rank.
- The Sign-in check page compares each submitted teammate with the day-of sign-in list (first name, last name, email). A missing or possible match flags the project for review and does not change scores, rank, or eligibility. Manual confirmations survive a new upload. Results CSV includes the sign-in status.
- Organizer progress polls every few seconds.

## Known limits

- Live updates use polling, not websockets.
- The UI creates judge accounts. Additional organizer accounts are not created from the dashboard.
- There is no emailed password reset. Organizers set judge passwords when they create accounts.
- The one-minute, 30-second, and time-up notices are on-screen banners.

## Migration notes

Startup runs `upgrade_schema` after `create_all`.

- An old single `projects.track_id` becomes a row in `project_tracks`. Assignments and evaluations gain `track_id` from that membership. The unique keys are now `(judge_id, project_id, track_id)`.
- An old `schedule_slots` row is copied onto the project: a 5 minute slot becomes 3 minutes of presentation and 2 of Q&A, and the old extension moves to the presentation phase. The slot table is then dropped. There is no scheduled start time.
- An old timer row keyed by `assignment_id` is copied onto `(judge_id, project_id)` as the presentation phase. Q&A starts idle. One clock is shared by every track scorecard that judge has for the project.
- Existing databases keep per-project assignments (`source = project`), so judges do not lose access. New access comes from assigning a judge to a track.
- Re-seed with `python -m app.seed --reset` to load the submissions export. That replaces the old demo projects.
- A submitted evaluation cannot be reassigned or deleted, so finished scores stay attached to the judge who wrote them.
