-- ============================================================
-- Sumire — LIVE Supabase schema
-- Generated from information_schema on 2026-09-18
--
-- SOURCE OF TRUTH. Do not guess column names; check here.
-- Regenerate with:
--   select table_name, column_name, data_type, is_nullable
--   from information_schema.columns
--   where table_schema = 'public'
--   order by table_name, ordinal_position;
-- ============================================================


-- ============================================================
-- user_profiles — one row per user. Created from WhatsApp phone
-- number on first inbound message. NOTE: no goal_6m column —
-- goals live in their own table.
-- ============================================================
CREATE TABLE user_profiles (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phone            TEXT NOT NULL,
    name             TEXT,
    job_role         TEXT,                       -- renamed: "current_role" is reserved in Postgres
    company          TEXT,
    career_stage     TEXT,
    onboarding_done  BOOLEAN,
    onboarding_data  JSONB,
    created_at       TIMESTAMPTZ,
    updated_at       TIMESTAMPTZ
);


-- ============================================================
-- goals — what the user is working toward. Multiple active goals
-- allowed. Populated by onboarding (Day 5); empty until then.
-- ============================================================
CREATE TABLE goals (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    description  TEXT,
    target_date  DATE,
    status       TEXT,                           -- active | achieved | paused | dropped
    created_at   TIMESTAMPTZ,
    updated_at   TIMESTAMPTZ
);


-- ============================================================
-- conversations — every inbound and outbound WhatsApp message.
-- whatsapp_message_id is the idempotency key against Meta retries.
-- ============================================================
CREATE TABLE conversations (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id              UUID NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    direction            TEXT NOT NULL,          -- inbound | outbound
    raw_content          TEXT,
    media_type           TEXT,                   -- text | voice | image
    classification       TEXT,                   -- work_update | question | correction |
                                                 -- context_reply | acknowledgement |
                                                 -- reflection_reply | unknown
    whatsapp_message_id  TEXT,
    created_at           TIMESTAMPTZ
);


-- ============================================================
-- skills — upserted on mention. mention_count rises rather than
-- creating duplicate rows.
-- ============================================================
CREATE TABLE skills (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    name           TEXT NOT NULL,
    category       TEXT,                         -- technical | leadership | communication |
                                                 -- strategic | operational
    mention_count  INTEGER,
    first_seen     TIMESTAMPTZ,
    last_seen      TIMESTAMPTZ,
    evidence       JSONB,                        -- [{conversation_id, snippet, date}]
    goal_ids       UUID[],
    created_at     TIMESTAMPTZ,
    updated_at     TIMESTAMPTZ
);


-- ============================================================
-- stakeholders — people who matter for the user's career.
-- email is null until Gmail sync (Week 2) populates it.
-- ============================================================
CREATE TABLE stakeholders (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    name           TEXT NOT NULL,
    role           TEXT,
    company        TEXT,
    relationship   TEXT,                         -- manager | skip_level | peer | report |
                                                 -- mentor | sponsor | external | unknown
    sentiment      TEXT,                         -- positive | neutral | negative | unknown
    context        TEXT,
    email          TEXT,
    mention_count  INTEGER,
    first_seen     TIMESTAMPTZ,
    last_seen      TIMESTAMPTZ,
    created_at     TIMESTAMPTZ,
    updated_at     TIMESTAMPTZ
);


-- ============================================================
-- power_stories — Situation / Action / Outcome / Evidence.
--
-- The *_status columns are the core of the product model: they
-- drive completeness, the gap follow-up questions, and portfolio
-- health. Each is strong | partial | missing.
--
-- OUTCOME STATUS RULE: strong requires a number, percentage,
-- named metric, or explicit before/after comparison. "Improved
-- significantly" is PARTIAL, not strong.
--
-- completeness is computed in Python (services/scoring.py), never
-- by the LLM. Weights: situation 16/10/0, action 22/14/0,
-- outcome 26/14/0, evidence 16/8/0.
-- ============================================================
CREATE TABLE power_stories (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    title             TEXT NOT NULL,

    situation         TEXT,
    situation_status  TEXT,
    action            TEXT,
    action_status     TEXT,
    outcome           TEXT,                      -- renamed from "result" 2026-09-18
    outcome_status    TEXT,
    evidence          TEXT,
    evidence_status   TEXT,

    completeness      INTEGER DEFAULT 0,
    gaps              JSONB DEFAULT '[]',        -- [{element, gap_description, follow_up_question}]
    status            TEXT DEFAULT 'building',   -- building | complete | dismissed

    skills_used       TEXT[],
    stakeholders      UUID[],
    goal_ids          UUID[],

    date              DATE,
    source            TEXT,                      -- whatsapp | gmail
    source_id         UUID,
    confidence        REAL,
    confirmed         BOOLEAN,

    created_at        TIMESTAMPTZ,
    updated_at        TIMESTAMPTZ
);


-- ============================================================
-- career_updates — one row per processed message, linking the
-- conversation to what was extracted from it.
-- ============================================================
CREATE TABLE career_updates (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    conversation_id         UUID REFERENCES conversations(id),
    summary                 TEXT NOT NULL,
    extracted_skills        TEXT[],
    extracted_story         UUID REFERENCES power_stories(id),
    extracted_stakeholders  UUID[],
    goal_relevance          JSONB,
    created_at              TIMESTAMPTZ
);


-- ============================================================
-- gmail_connections — OAuth tokens per user. Week 2.
-- Tokens are encrypted at the application layer before storage.
-- ============================================================
CREATE TABLE gmail_connections (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    access_token     TEXT NOT NULL,
    refresh_token    TEXT,
    token_expires    TIMESTAMPTZ,
    last_sync        TIMESTAMPTZ,
    last_history_id  BIGINT,                     -- Gmail History API incremental sync bookmark
    status           TEXT,
    created_at       TIMESTAMPTZ,
    updated_at       TIMESTAMPTZ
);


-- ============================================================
-- email_threads — passive collection from Gmail. Week 2.
-- Mirrors career_updates so both sources feed the same model.
-- ============================================================
CREATE TABLE email_threads (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    gmail_thread_id         TEXT NOT NULL,
    subject                 TEXT,
    participants            TEXT[],
    message_count           INTEGER,
    last_message            TIMESTAMPTZ,
    summary                 TEXT,
    extracted_skills        TEXT[],
    extracted_story         UUID REFERENCES power_stories(id),
    extracted_stakeholders  UUID[],
    goal_relevance          JSONB,
    synced_at               TIMESTAMPTZ,
    created_at              TIMESTAMPTZ
);


-- ============================================================
-- NOTES FOR CODE
--
-- 1. No goal_6m on user_profiles. Read goals WHERE status='active'.
--    Returns empty for every user until onboarding ships (Day 5) —
--    omit the goals section from the context block when empty.
--
-- 2. power_stories.outcome (not "result").
--
-- 3. ARRAY columns: skills_used and extracted_skills are TEXT[];
--    stakeholders, goal_ids, extracted_stakeholders are UUID[].
--
-- 4. RLS is enabled on all tables. The backend uses the
--    service_role key and bypasses it, so every query must scope
--    to user_id explicitly.
-- ============================================================
