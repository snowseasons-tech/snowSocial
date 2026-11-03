# SnowSocial

SnowSocial is a single-business social content planner for drafting posts, scheduling a publishing queue, and reviewing monthly performance.

## Step-by-step build plan

1. **Post lifecycle API** — save drafts, approve them, schedule future posts, and create one delivery record per selected channel. *(Merged.)*
2. **Publishing worker** — claim due delivery records safely, persist outcomes, and retry temporary failures with backoff. *(Merged.)*
3. **Channel connections** — connect Facebook Pages and linked Instagram business accounts with Meta OAuth, encrypted token storage, and channel-specific content validation. *(Merged.)*
4. **Monthly analytics** — import normalized post metrics and return month-over-month summaries. *(Merged.)*
5. **Frontend integration** — replace browser-only storage with the API and display real post and delivery statuses. *(Merged.)*
6. **Facebook publishing** — publish scheduled text posts to the connected Facebook Page and route uncertain results to review instead of blindly retrying. *(Previous PR.)*
7. **Instagram publishing** — publish single-image posts using a public HTTPS image URL and Meta's media-container flow. *(This branch.)*

## Run the dashboard prototype

Serve the dashboard at `http://localhost:8080` (for example, `python3 -m http.server 8080`) and run the API using the steps below. The dashboard reads and writes posts through the API; it no longer stores posts in browser local storage.

## Run the API and PostgreSQL locally

1. Copy `.env.example` to `.env` and set a long alphanumeric `POSTGRES_PASSWORD`.
2. Run `docker compose up --build`.
3. Open `http://127.0.0.1:8000/docs` to inspect the API.
4. Check `http://127.0.0.1:8000/api/health` for the database health response.

The optional worker service can be started with `docker compose --profile publishing up --build worker`. Facebook publishes text to the Page feed. Instagram supports single-image feed posts through Meta's media-container and publish endpoints; the composer accepts a direct public HTTPS image URL, which Meta must be able to fetch. If multiple Pages or Instagram accounts are connected, set `META_FACEBOOK_PAGE_ID` or `META_INSTAGRAM_ACCOUNT_ID` to select the intended account (IDs are visible as `provider_account_id` in `/api/connections`). The worker needs the same `TOKEN_ENCRYPTION_KEY` as the API. Google Business Profile and LinkedIn deliveries remain queued until their adapters are added. If Meta may have accepted a publish request but did not confirm it, SnowSocial marks the delivery `needs_review` to avoid an automatic duplicate.

To enable Meta account linking, set `META_APP_ID`, `META_APP_SECRET`, `META_REDIRECT_URI`, `META_FRONTEND_URL`, and `TOKEN_ENCRYPTION_KEY` in the API environment. Instagram analytics also requests `instagram_manage_insights`; existing connections may need to reconnect to grant the added permission. Configure the exact redirect URI in the Meta developer app. Generate a Fernet key with `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`. Start account linking at `/api/connections/meta/authorize`, list connected accounts at `/api/connections`, and disconnect one with `DELETE /api/connections/{connection_id}`. The API stores tokens encrypted at rest; keep the API private until authentication is added.

Monthly platform metrics can be imported with `POST /api/analytics/metrics` using the `X-Analytics-Ingest-Token` header and a payload containing published delivery IDs, the first day of each reporting month, and available counters (`impressions`, `reach`, `likes`, `comments`, `shares`, `saves`, and `link_clicks`). Set `ANALYTICS_INGEST_TOKEN` to a random secret of at least 32 characters. Re-importing the same delivery and month replaces that snapshot. Read the aggregate and previous-month comparison with `GET /api/analytics/monthly?month=YYYY-MM-01`. A missing metric remains `null`; percentage change is `null` when either month has no comparable value or the previous value is zero. Instagram post metrics are refreshed automatically by the publishing worker every 24 hours for published posts from the last 45 days. Each lifetime snapshot is grouped by the post's publication month; the report therefore summarizes content published in that month. Set `ANALYTICS_SYNC_INTERVAL_SECONDS` or `ANALYTICS_SYNC_BATCH_SIZE` to adjust collection. The automatic collector covers Instagram and Facebook Page posts. Facebook snapshots include reactions, comments, and shares; reach and impressions are not collected by this adapter. Google Business Profile and LinkedIn metrics can still be imported through the authenticated metrics endpoint.

The dashboard now loads posts, account connections, and monthly totals from the API. When the dashboard and API use different origins, set the `snowsocial-api-base` meta tag in `index.html` to the API base URL and set `CORS_ORIGINS` in the API environment to the exact dashboard origin. For example, a dashboard hosted at `https://social.example.com` should use `https://api.example.com` in the meta tag and `https://social.example.com` in `CORS_ORIGINS`. For local use, set that meta tag to `http://127.0.0.1:8000`, serve the dashboard on port 8080, and allow `http://localhost:8080` or `http://127.0.0.1:8080` in `CORS_ORIGINS`.

Pull requests and pushes to `main` run GitHub Actions checks for frontend syntax, backend lint and syntax, database migrations, Compose configuration, and the API image build.

The API supports draft creation and editing, approval, future scheduling, cancellation, per-channel delivery records, connected Meta accounts, and monthly metric summaries. The dashboard reads posts, connection state, and summary totals from the API. The worker publishes text posts to Facebook Pages and single-image feed posts to Instagram; Google Business Profile and LinkedIn still need adapters. The API has no general user authentication, so keep it private.
