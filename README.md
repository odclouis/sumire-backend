# Sumire Backend

Backend for Sumire, a career intelligence companion. Users send WhatsApp voice
notes and text about their work; an LLM extracts skills, power stories, and
stakeholders from those conversations and stores them in Supabase.

This is the foundation layer: a FastAPI service, configuration, and Supabase
data-access helpers. LLM extraction and WhatsApp webhook handling are not
implemented yet.

## Local setup

```bash
uv sync
cp .env.example .env
# fill in SUPABASE_URL, SUPABASE_SERVICE_KEY, SUPABASE_ANON_KEY in .env
uv run uvicorn app.main:app --reload
```

The service starts on `http://127.0.0.1:8000`. Check `GET /health` to confirm
it's running and can reach Supabase.

## Project structure

```
app/
  main.py            FastAPI app instance, router registration, startup log
  config.py           Settings loaded from environment variables (.env)
  db.py                Supabase client and data-access helper functions
  models.py            Pydantic models mirroring the database schema
  routers/
    health.py          GET /health — service and database status
  services/            Business logic (empty for now)
tests/                 Test suite (pytest)
pyproject.toml         Project metadata and dependencies (uv-compatible)
.env.example           Template for required environment variables
```
