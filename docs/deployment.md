# Deployment guide

Target topology: **Supabase** (PostgreSQL + pgvector) ← **Render** (FastAPI, Docker) ← **Vercel** (Next.js).
Railway or Fly.io work the same way for the backend, since it is a standard Docker image listening on `$PORT`.

> None of the hosted services below were provisioned while building this project. The steps follow the providers' standard flows; the Docker images themselves have been built and run end to end locally.

## 1. Database: Supabase

1. Create a project at https://supabase.com and pick a region close to your users (e.g. Mumbai `ap-south-1`).
2. **Database → Extensions:** enable `vector`. (The migration also runs `CREATE EXTENSION IF NOT EXISTS vector`.)
3. **Project Settings → Database → Connection string → URI.** Use the **session pooler** (port 5432) or the direct connection. The transaction pooler (6543) also works, because the app disables prepared-statement caching, but the session pooler is recommended for migrations.
   ```
   DATABASE_URL=postgresql://postgres.<ref>:<password>@aws-0-ap-south-1.pooler.supabase.com:5432/postgres
   ```
4. Run migrations once from your machine (the Render container also runs them on start):
   ```bash
   cd backend && DATABASE_URL="<supabase-uri>" alembic upgrade head
   ```
5. Create the first admin and, optionally, the placeholder properties:
   ```bash
   DATABASE_URL="<supabase-uri>" python -m scripts.create_admin --email owner@yourdomain.com
   DATABASE_URL="<supabase-uri>" ENVIRONMENT=production python -m scripts.seed   # optional placeholders to edit
   ```

Any PostgreSQL 15+ with pgvector ≥ 0.5 works instead (Neon, RDS, Railway Postgres, self-hosted).

## 2. Backend: Render

1. Push the repository to GitHub.
2. Render dashboard → **New → Blueprint**, select the repo; `render.yaml` defines the service (Docker, `rootDir: backend`, health check `/health`).
   Or **New → Web Service → Docker**, with root directory `backend`.
3. Set environment variables:

| Variable | Value |
|---|---|
| `ENVIRONMENT` | `production` |
| `DATABASE_URL` | Supabase URI |
| `SECRET_KEY` | 32+ random characters (`openssl rand -hex 32`); the blueprint generates one |
| `FRONTEND_URL` | `https://<your-app>.vercel.app` (CORS) |
| `CORS_EXTRA_ORIGINS` | optional extra origins, comma-separated |
| `ANTHROPIC_API_KEY` | Claude key |
| `OPENAI_API_KEY` | optional, for semantic embeddings |
| `GOOGLE_MAPS_API_KEY` | Places API (New) key |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | optional bootstrap admin for `scripts.seed` |

4. Deploy. The container runs `alembic upgrade head`, then `uvicorn` on `$PORT`. Check `https://<api>.onrender.com/health` and `/api/status`.
5. If you added `OPENAI_API_KEY` after documents were indexed, call `POST /api/admin/documents/reindex-all`.

**Railway:** create a service from the repo with root directory `backend` (Dockerfile detected) and set the same variables.

**Scaling notes**
- The rate limiter and Places cache are in-process. For more than one instance, put a Redis-backed limiter in front (or replace `InMemoryRateLimiter` with a Redis implementation with the same `check()` interface), or rely on your platform's rate limiting.
- Document processing runs as an in-process background task. For heavy upload volume, move `process_document` onto a worker queue.
- SSE needs proxy buffering disabled; the backend already sends `X-Accel-Buffering: no` and `Cache-Control: no-cache`.

## 3. Frontend: Vercel

1. Vercel → **Add New Project** → import the repo → **Root Directory: `frontend`** (Next.js is detected).
2. Environment variable: `NEXT_PUBLIC_API_URL=https://<api>.onrender.com` (no trailing slash).
3. Deploy, then put the Vercel URL in the backend's `FRONTEND_URL` and redeploy the backend.
4. Optional custom domain: add it in Vercel and add it to `CORS_EXTRA_ORIGINS`.

(`output: "standalone"` in `next.config.ts` is used by the Docker image; Vercel ignores it.)

## 4. Google Places configuration

1. Google Cloud Console → create a project → enable billing.
2. **APIs & Services → Library → Places API (New) → Enable.**
3. **Credentials → Create credentials → API key.** Under *API restrictions* allow only *Places API (New)*. Under *Application restrictions* use *IP addresses* (Render's outbound IPs) if your plan provides static egress; otherwise rely on the API restriction plus quotas.
4. Set daily quotas and budget alerts. The app limits each search to `PLACES_MAX_RESULTS` and caches results for `PLACES_CACHE_TTL_SECONDS`.
5. In the admin console, set each property's **exact coordinates** and discovery radius.

## 5. Production checklist

- [ ] `ENVIRONMENT=production` and a strong `SECRET_KEY` (the app refuses to start otherwise)
- [ ] `PLACES_PROVIDER` is `auto` or `google` (mock is rejected in production)
- [ ] Default development admin not present; real admin created with `scripts.create_admin`
- [ ] Placeholder properties replaced or deactivated; *Contains placeholder data* unticked
- [ ] Exact coordinates set for every property
- [ ] Guest PINs set where Wi-Fi passwords or access codes are stored
- [ ] `FRONTEND_URL` / `CORS_EXTRA_ORIGINS` match the deployed frontend
- [ ] Live checks from [setup.md](setup.md#verifying-live-integrations) pass with real keys
- [ ] Supabase backups enabled; API key quotas and budget alerts configured

## 6. End-to-end validation after deploy

```bash
API=https://<api>.onrender.com
curl -s $API/health
curl -s $API/api/status          # expect llm=claude, places=google_places
```

Then on a phone, open the Vercel URL, select Saket, and ask in sequence: Wi-Fi password → dinner nearby → something more budget-friendly → drinks and dancing → checkout time. Each local answer should show real places with distances measured from the property and working *Directions* links.
