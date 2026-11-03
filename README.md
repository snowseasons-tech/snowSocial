# SnowSocial

SnowSocial is a single-business social content planner for drafting posts, scheduling a publishing queue, and reviewing monthly performance.

## Step-by-step build plan

1. **Post lifecycle API** — save drafts, approve them, schedule future posts, and create one delivery record per selected channel. *(This branch.)*
2. **Publishing worker** — claim due delivery records safely, persist outcomes, and retry temporary failures with backoff. *(This branch.)*
3. **Channel connections** — add OAuth, secure token storage, and channel-specific content validation.
4. **Monthly analytics** — import available platform metrics and generate a month-over-month summary.
5. **Frontend integration** — replace browser-only storage with the API and display real post and delivery statuses.

## Run the dashboard prototype

Open `index.html` in a current browser. Posts created in the composer are saved in that browser's local storage; the page is not connected to the API yet.

## Run the API and PostgreSQL locally

1. Copy `.env.example` to `.env` and set a long alphanumeric `POSTGRES_PASSWORD`.
2. Run `docker compose up --build`.
3. Open `http://127.0.0.1:8000/docs` to inspect the API.
4. Check `http://127.0.0.1:8000/api/health` for the database health response.

The optional worker service can be started with `docker compose --profile publishing up worker`. It will leave queued deliveries untouched until at least one channel publisher is registered in `backend/app/publishers.py`.

Pull requests and pushes to `main` run GitHub Actions checks for frontend syntax, backend lint and syntax, database migrations, Compose configuration, and the API image build.

The API supports draft creation and editing, approval, future scheduling, cancellation, and per-channel delivery records. The worker includes per-channel claiming, retry backoff, and a review-required state for interrupted attempts. It does not publish to social platforms until channel adapters are registered. The dashboard metrics and existing post examples remain sample data. Keep the API private until authentication is added.
