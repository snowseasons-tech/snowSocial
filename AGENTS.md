# Agent Instructions

## Completed milestones

These capabilities are already merged. Preserve their behavior and build on them; do not reimplement them:

1. **Post lifecycle API** — save drafts, approve them, schedule future posts, and create one delivery record per selected channel. **Status: merged.**
2. **Publishing worker** — safely claim due delivery records, persist outcomes, and retry temporary failures with backoff. **Status: merged.**

When changing either area, inspect the existing routes, models, migrations, and worker first. Keep delivery creation idempotent and preserve safe claim/retry behavior.
