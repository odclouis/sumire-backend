-- Single-slot pending-correction state for ambiguous corrections awaiting disambiguation.
--
-- Deliberately a JSONB column on user_profiles rather than a new table: at most one pending
-- correction makes sense per user at a time (Sumire won't stack a second disambiguation question
-- on top of an unresolved first one - a new ambiguous correction just supersedes the old one), so
-- this is ephemeral, short-lived (<=24h, enforced app-side) conversational state, not a
-- first-class entity with its own relations or lifecycle. See app/db.py's
-- get_pending_correction/set_pending_correction/clear_pending_correction and
-- app/services/pipeline.py for the read/write/expiry logic.
alter table user_profiles
  add column if not exists pending_correction jsonb;
