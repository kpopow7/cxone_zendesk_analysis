# Analytics Chatbot on Railway

Natural-language Q&A over your `combined_interactions` data, with **company-only login** (Gradio basic auth).

## What you need on Railway

This setup uses **two separate services** in one Railway project:

| Service | Purpose | Public URL? |
|---------|---------|-------------|
| **PostgreSQL** | Stores synced pipeline data | No (DB only) |
| **Web service** (this repo) | Runs the Gradio chatbot | **Yes — you must generate a domain** |

If you only added Postgres, or added a generic “Empty service”, you will **not** see the chatbot yet. You need the **GitHub repo web service** built from `chatbot/Dockerfile`.

## Architecture

```
[Your PC: pipeline extracts]  --sync-->  [Railway PostgreSQL]
                                              ^
[Employees] --> login --> [Railway Web: Gradio chatbot] --SQL--> Postgres
                              |
                              +--> OpenAI (generate SQL + summarize)
```

---

## Prerequisites (before deploying the chatbot)

Complete these on your PC first:

- [ ] Local pipeline runs and loads data into Docker Postgres (`DATABASE_URL=localhost:5433`)
- [ ] `TARGET_DATABASE_URL` in `.env` uses the **public** Postgres URL (not `postgres.railway.internal`)
- [ ] `python scripts/sync_to_railway.py` completes successfully
- [ ] Analytics views exist on Railway (`analytics_interactions`, `analytics_transcript_summaries` if using Step 4b)
- [ ] Knowledge index built for RAG (`python scripts/build_knowledge_index.py` — see [docs/RAG.md](../docs/RAG.md))
- [ ] This repo is pushed to GitHub (Railway deploys from GitHub)

---

## Step 1: Create Railway Postgres

1. Go to [railway.app](https://railway.app) → **New Project**.
2. Click **+ New** → **Database** → **Add PostgreSQL**.
3. Rename the service to something clear, e.g. `Postgres` (you will reference this name later).
4. Open the Postgres service → **Connect** (or **Data** → **Connect**). Railway shows two URLs:
   - **Private** (`postgres.railway.internal`) — for services **inside Railway** (chatbot `DATABASE_URL`)
   - **Public / TCP proxy** (`....proxy.rlwy.net` or `....railway.app`) — for **sync from your PC** (`TARGET_DATABASE_URL`)

Keep both URLs handy; they serve different purposes.

---

## Step 2: Load data from your local pipeline

```powershell
cd C:\Users\kpopo\OneDrive\Documents\cxone_zendesk_analysis

# Public TCP proxy URL — NOT postgres.railway.internal
# (or set TARGET_DATABASE_URL in .env and run without the $env line)
$env:TARGET_DATABASE_URL = "postgresql://postgres:PASSWORD@PUBLIC_HOST:PORT/railway"
python scripts/sync_to_railway.py
```

### Create the analytics view (required for the chatbot)

The chatbot queries **`analytics_interactions`** (Zendesk-linked calls) and optionally **`analytics_transcript_summaries`** (per-call LLM transcript reasons from Step 4b). `sync_to_railway.py` creates/refreshes these views on the target DB; you can also run `scripts/railway_analytics_setup.sql` manually.

See **[Running `railway_analytics_setup.sql` on Railway](#running-railway_analytics_setupsql-on-railway)** below for full steps.

Quick check after setup:

```sql
SELECT COUNT(*) FROM analytics_interactions;
```

Re-run `sync_to_railway.py` after daily pipeline jobs.

---

## Running `railway_analytics_setup.sql` on Railway

This script creates (or updates) `analytics_interactions` and `analytics_transcript_summaries`. The chatbot and example SQL depend on them.

**Prerequisites**

- Railway Postgres service exists in your project
- `python scripts/sync_to_railway.py` has completed at least once (`combined_interactions` has rows)
- If you added normalized columns (`call_reason`, `disposition_label`, etc.), re-run sync **before** updating the view so those columns exist on Railway

**What the script does**

- `CREATE OR REPLACE VIEW analytics_interactions AS SELECT ... FROM combined_interactions`
- Exposes analytics-friendly columns including `call_reason`, `disposition_label`, and `transcript_preview` (truncated transcript)
- Does **not** modify or delete table data
- Safe to re-run anytime the view definition in the repo changes

**What to run**

Only the active SQL block (lines 4–33 in the file). The rest is commented optional setup for a read-only `chatbot_reader` user.

---

### Method A: Railway dashboard (recommended)

1. Open [railway.app](https://railway.app) → your project.
2. Click the **Postgres** service (database icon — not the chatbot web service).
3. Open the data/SQL UI. Railway’s label varies by version; look for one of:
   - **Data** → **Query**
   - **Database** → **Query**
   - **Connect** → **Query** tab
4. On your PC, open `scripts/railway_analytics_setup.sql` in this repo and copy **the entire file** (comments are fine; Postgres ignores `--` lines).
5. Paste into the Railway query editor.
6. Click **Run** / **Execute**.
7. Expect success with no error. `CREATE OR REPLACE VIEW` does not return rows.

**Verify**

Run these in the same query editor:

```sql
-- View exists and is readable
SELECT COUNT(*) FROM analytics_interactions;

-- Normalized columns present (after rebuild + sync)
SELECT call_reason, disposition_label
FROM analytics_interactions
WHERE call_reason IS NOT NULL OR disposition_label IS NOT NULL
LIMIT 5;
```

If the first query works but the second fails with `column "call_reason" does not exist`, the view is **outdated** — re-run the full setup script after syncing latest `combined_interactions` data.

---

### Method B: `psql` from your PC (Windows)

Use the **public TCP proxy** URL from Postgres → **Connect** (same URL as `TARGET_DATABASE_URL`, not `postgres.railway.internal`).

**Install `psql`** if needed:

- [PostgreSQL Windows installer](https://www.postgresql.org/download/windows/) (client tools only), or
- `winget install PostgreSQL.PostgreSQL` and use `psql` from the install `bin` folder

**Run the script**

```powershell
cd C:\Users\kpopo\OneDrive\Documents\cxone_zendesk_analysis

# Public URL from Railway Postgres -> Connect
$env:DATABASE_URL = "postgresql://postgres:PASSWORD@PUBLIC_HOST:PORT/railway"

psql $env:DATABASE_URL -f scripts/railway_analytics_setup.sql
```

You should see:

```text
CREATE VIEW
```

**Verify**

```powershell
psql $env:DATABASE_URL -c "SELECT COUNT(*) FROM analytics_interactions;"
```

**Common `psql` errors**

| Error | Fix |
|-------|-----|
| `could not translate host name` | Use the **public** proxy host, not `postgres.railway.internal` |
| `password authentication failed` | Copy the URL exactly from Railway Connect; URL-encode special characters in the password |
| `relation "combined_interactions" does not exist` | Run `python scripts/sync_to_railway.py` first |
| `column "call_reason" does not exist` | Re-run sync after local rebuild, then re-run this setup script |

---

### Method C: Railway CLI (optional)

If you use the [Railway CLI](https://docs.railway.app/guides/cli):

```powershell
cd C:\Users\kpopo\OneDrive\Documents\cxone_zendesk_analysis
railway link
railway run psql $DATABASE_URL -f scripts/railway_analytics_setup.sql
```

`railway run` injects the linked service’s `DATABASE_URL` (usually the private URL, which works from Railway’s environment). For local `psql`, prefer Method B with the public URL.

---

### When to re-run the setup script

| Situation | Action |
|-----------|--------|
| First-time chatbot setup | Run once after first successful sync |
| View definition changed in repo (new columns) | Re-run after sync |
| Error: `relation "analytics_interactions" does not exist` | Run the script |
| View exists but missing `call_reason` / `disposition_label` | Re-run sync, then re-run script |
| Daily pipeline / sync only | **Do not** re-run unless the SQL file changed |

---

### Optional: read-only `chatbot_reader` user

The bottom of `railway_analytics_setup.sql` has commented `CREATE USER` / `GRANT` statements. Only use if you want the chatbot service to use a non-`postgres` login:

1. Uncomment those lines in a **copy** (not committed) with a strong password.
2. Run in Railway Query or `psql`.
3. Set chatbot service `DATABASE_URL` to the `chatbot_reader` connection string.

Most setups can keep the Postgres reference variable on the chatbot service.

---

## Step 3: Deploy the chatbot web service (detailed)

### 3a. Add the GitHub repo as a new service

1. In the **same Railway project** as Postgres, click **+ New**.
2. Choose **GitHub Repo** (not “Empty service”, not another Postgres).
3. Select this repository (`cxone_zendesk_analysis`) and the branch you want (usually `main`).
4. Railway creates a new service — rename it to **`chatbot`** so it is easy to find.

You should now see **two services** in the project canvas: `Postgres` and `chatbot`.

### 3b. Verify build settings (Dockerfile)

Click the **chatbot** service → **Settings** → **Build**:

| Setting | Required value |
|---------|----------------|
| **Builder** | Dockerfile |
| **Dockerfile path** | `chatbot/Dockerfile` |
| **Root directory** | `/` (repo root — leave blank or `.`) |

This repo includes `railway.toml` at the root, which should set these automatically. If Railway used **Railpack/Nixpacks** instead, the chatbot will not start correctly — switch the builder to **Dockerfile** manually.

**Deploy** tab → latest build logs should show steps like:

```
COPY chatbot/ /app/chatbot/
CMD ["python", "chatbot/app.py"]
```

Not a generic Python/Nixpacks install of the whole orchestration pipeline.

### 3c. Set environment variables on the chatbot service

Click **chatbot** service → **Variables** tab.

**Do not** paste secrets into Postgres unless you intend to — set these on the **chatbot web service**.

#### Required variables

| Variable | How to set | Notes |
|----------|------------|-------|
| `DATABASE_URL` | **Reference** from Postgres service | See below |
| `OPENAI_API_KEY` | Your OpenAI API key | Required |
| `CHATBOT_USERNAME` | e.g. `analytics` | Company login |
| `CHATBOT_PASSWORD` | Strong password | Not `change-me-to-a-strong-password` |

#### Linking `DATABASE_URL` from Postgres (recommended)

1. On the **chatbot** service → **Variables** → **+ New Variable**.
2. Name: `DATABASE_URL`
3. Click **Add Reference** (or type a reference, depending on Railway UI version).
4. Select your **Postgres** service → variable **`DATABASE_URL`** (or `DATABASE_PRIVATE_URL` if offered).

This gives the chatbot the **private** internal URL (`postgres.railway.internal`), which is correct because the chatbot runs inside Railway.

**Manual alternative** (if references do not work):

```env
DATABASE_URL=postgresql://postgres:PASSWORD@postgres.railway.internal:5432/railway
```

Use the **private** host here, not the public TCP proxy.

#### Optional variables

```env
OPENAI_MODEL=gpt-4o-mini
CHATBOT_SHOW_SQL=true
CHATBOT_USERS=alice:pass1,bob:pass2
```

For multiple users, `CHATBOT_USERS` replaces single `CHATBOT_USERNAME` / `CHATBOT_PASSWORD`.

**Do not set `PORT` manually** unless Railway support asks you to. The app reads Railway’s injected `PORT` automatically. (Hardcoding `PORT=7860` in service variables can break routing.)

### 3d. Generate a public domain (easy to miss)

The chatbot is **not** reachable until you expose it:

1. Click **chatbot** service (not Postgres).
2. Go to **Settings** → **Networking** → **Public Networking**.
3. Click **Generate Domain**.

Railway assigns a URL like:

```
https://chatbot-production-xxxx.up.railway.app
```

Open **that URL** in a browser. Postgres has no public web UI — if you open the Postgres service, you will not see Gradio.

### 3e. Deploy and verify

1. **chatbot** service → **Deployments** → confirm status is **Success** / **Active**.
2. Open **Deploy Logs** (not Build Logs). You should see Gradio startup, e.g.:

```
Running on local URL:  http://0.0.0.0:XXXX
```

3. Open your generated domain → browser shows a **login prompt** → enter `CHATBOT_USERNAME` / `CHATBOT_PASSWORD`.
4. After login, you see **“Contact Center Analytics Assistant”** with example questions.

### 3f. Quick smoke test

After logging in, ask:

```
How many rows are in combined_interactions?
```

If data and the analytics view are set up, you get a summarized answer. If not, see Troubleshooting below.

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

For local dev, use the **public** Postgres URL in `DATABASE_URL` (your PC cannot resolve `postgres.railway.internal`).

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

---

## Troubleshooting

### “I deployed a service but don’t see the chatbot”

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Only Postgres in the project | Web service not added | **+ New → GitHub Repo** (Step 3a) |
| Service exists, no URL | Domain not generated | **chatbot → Settings → Networking → Generate Domain** |
| Build logs show Nixpacks/Railpack, not Docker | Wrong builder | **Settings → Build → Dockerfile** path `chatbot/Dockerfile` |
| Deploy crashes immediately | Missing env vars | Set `DATABASE_URL`, `OPENAI_API_KEY`, `CHATBOT_USERNAME`, `CHATBOT_PASSWORD` on **chatbot** service |
| `No chatbot login configured` in logs | Auth vars missing | Set `CHATBOT_USERNAME` + `CHATBOT_PASSWORD` (or `CHATBOT_USERS`) |
| `ValidationError` for `DATABASE_URL` | DB URL not set on web service | Add `DATABASE_URL` reference from Postgres |
| Domain shows “Application failed to respond” | App not listening on `$PORT` | Remove any manual `PORT=7860` service variable; redeploy |
| Login works, errors on every question | DB/view/data issue | Run sync + `railway_analytics_setup.sql`; check deploy logs |
| Blank or “no data” answers | Empty tables | Re-run `sync_to_railway.py` from your PC |

### Other errors

| Issue | Fix |
|-------|-----|
| Login fails | Check `CHATBOT_USERNAME` / `CHATBOT_PASSWORD` on the **chatbot** web service |
| `relation "analytics_interactions" does not exist` | Run `scripts/railway_analytics_setup.sql` on Railway Postgres |
| Empty answers | Run `sync_to_railway.py`; confirm rows in `combined_interactions` |
| `password authentication failed` | Use Railway `DATABASE_URL` exactly; URL-encode special chars in password |
| Slow first reply | Normal — two OpenAI calls (SQL + summary) per question |
| `failed to resolve host 'postgres.railway.internal'` | Local sync needs the **public** Postgres URL in `TARGET_DATABASE_URL`, not `*.railway.internal` |
| `column "call_reason" does not exist` during sync | Re-run `python scripts/sync_to_railway.py` (latest code migrates missing columns on Railway automatically) |
| `out of memory for query result` on `cxone_transcripts` | Transcript rows are large. Use `--batch-size 10`. `raw_metadata` is omitted by default. |
| `out of memory for query result` on `combined_interactions` | Sync truncates `transcript_text` to 2000 chars at source (full text stays in `cxone_transcripts`). Default batch is 10; retry with `--batch-size 5` if needed. |
| `extension "vector" is not available` during **sync** | Fixed: sync no longer requires pgvector. If you see this on `build_knowledge_index.py`, enable pgvector on that database (Railway Query: `CREATE EXTENSION vector;`) or use the pgvector Docker image locally. |
| `failed to resolve host 'postgres.railway.internal'` **on Railway** | `DATABASE_URL` reference is wrong or Postgres is in a different project — use Postgres reference in the same project |
| HTTP **429** / `OpenAI rate limit` | OpenAI RPM/TPM or quota limit — wait 60s, ask one question at a time, check [OpenAI usage](https://platform.openai.com/usage); avoid clicking multiple example questions quickly |
| Chatbot reply is just **Error** / **ERROR** | Usually an unhandled backend exception — check **chatbot → Deploy Logs** after asking; redeploy latest code; verify `OPENAI_API_KEY`, `DATABASE_URL`, and that `combined_interactions` exists |

### Where to look in Railway

- **Build Logs** — Docker image built correctly?
- **Deploy Logs** — Python/Gradio started? Auth or DB errors?
- **HTTP Logs** — Requests reaching the service after domain is generated?

---

## Hugging Face Spaces (optional)

You can also host the same `chatbot/app.py` on a **private** Hugging Face Space with Secrets for `DATABASE_URL`, `OPENAI_API_KEY`, and `CHATBOT_USERS`. Railway is recommended here because DB + app live in one project with simpler networking.
