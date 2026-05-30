# Analytics Chatbot on Railway

Natural-language Q&A over your `combined_interactions` data, with **company-only login** (Gradio basic auth).

## Architecture

```
[Your PC: pipeline extracts]  --sync-->  [Railway PostgreSQL]
                                              ^
[Employees] --> login --> [Railway Web: Gradio chatbot] --SQL--> Postgres
                              |
                              +--> OpenAI (generate SQL + summarize)
```

- **Database:** Railway Postgres (not reachable from the public internet except via your services)
- **Chatbot:** Railway web service (Docker) running `chatbot/app.py`
- **Auth:** Username/password per employee (`CHATBOT_USERS` or `CHATBOT_USERNAME` / `CHATBOT_PASSWORD`)
- **Safety:** Read-only SELECT, table allowlist, mandatory LIMIT, query timeout

---

## Step 1: Create Railway Postgres

1. Go to [railway.app](https://railway.app) and create a project.
2. **Add service → Database → PostgreSQL**.
3. Open the Postgres service → **Connect** → copy `DATABASE_URL` (usually `postgresql://postgres:...@....railway.app:PORT/railway`).

Keep this URL for sync and for the chatbot service.

---

## Step 2: Load data from your local pipeline

Run extracts and combined dataset locally (as today), then sync to Railway:

```powershell
# One-time: create tables on Railway + copy data
$env:TARGET_DATABASE_URL = "postgresql://postgres:PASSWORD@HOST:PORT/railway"
python scripts/sync_to_railway.py

# Create analytics view for the chatbot
# Railway dashboard -> Postgres -> Query tab, paste scripts/railway_analytics_setup.sql
```

Re-run sync after daily pipeline jobs (or schedule `sync_to_railway.py` via Task Scheduler / GitHub Actions).

**Alternative:** Point your local `.env` `DATABASE_URL` at Railway and run the full pipeline directly against cloud Postgres (slower for large backfills).

---

## Step 3: Deploy the chatbot web service

1. In the same Railway project, **Add service → GitHub Repo** (this repository).
2. Railway detects `railway.toml` and builds `chatbot/Dockerfile`.
3. Set **Variables** on the **web service** (not necessarily on Postgres):

| Variable | Value |
|----------|--------|
| `DATABASE_URL` | Railway Postgres URL (prefer read-only `chatbot_reader` user after setup) |
| `OPENAI_API_KEY` | Your OpenAI key |
| `CHATBOT_USERNAME` | Company login user |
| `CHATBOT_PASSWORD` | Strong password |
| `CHATBOT_SHOW_SQL` | `true` (optional transparency) |

For multiple users:

```env
CHATBOT_USERS=alice:pass1,bob:pass2,cx-lead:pass3
```

4. Deploy. Railway assigns a public URL like `https://your-chatbot.up.railway.app`.
5. Open the URL → Gradio login prompt → ask questions.

---

## Step 4: Run locally (development)

```powershell
pip install -r requirements-chatbot.txt
copy .env.chatbot.example .env.chatbot
# Edit .env.chatbot — merge into .env or load separately

$env:PYTHONPATH = "src"
python chatbot/app.py
```

Open `http://localhost:7860` — login required.

---

## Example questions

- What were the top call reasons for inbound calls last week?
- How many calls per skill for LEV Consumer in the last 7 days?
- What are the top disposition codes this month?
- Show daily inbound call volume for the last 14 days.
- Which skills have the highest negative sentiment rate last week?

---

## Security checklist

- [ ] Strong passwords in `CHATBOT_USERS` / `CHATBOT_PASSWORD`
- [ ] Optional: create `chatbot_reader` Postgres user with SELECT-only (see `scripts/railway_analytics_setup.sql`)
- [ ] Do not commit `.env` or Railway URLs to git
- [ ] Rotate passwords when employees leave
- [ ] SQL guardrails block writes and non-allowlisted tables
- [ ] Consider Railway **private networking** if you add more internal services later

---

## Hugging Face Spaces (optional)

You can also host the same `chatbot/app.py` on a **private** Hugging Face Space with Secrets for `DATABASE_URL`, `OPENAI_API_KEY`, and `CHATBOT_USERS`. Railway is recommended here because DB + app live in one project with simpler networking.

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Login fails | Check `CHATBOT_USERNAME` / `CHATBOT_PASSWORD` on the web service |
| `relation "analytics_interactions" does not exist` | Run `scripts/railway_analytics_setup.sql` on Railway Postgres |
| Empty answers | Run `sync_to_railway.py`; confirm rows in `combined_interactions` |
| `password authentication failed` | Use Railway `DATABASE_URL` exactly; URL-encode special chars in password |
| Slow first reply | Normal — two OpenAI calls (SQL + summary) per question |
