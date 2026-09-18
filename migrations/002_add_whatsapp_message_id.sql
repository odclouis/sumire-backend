-- Adds whatsapp_message_id to conversations so the webhook can dedupe retried
-- Meta deliveries (Meta retries a webhook if it doesn't get a fast 200 back).
alter table conversations
  add column if not exists whatsapp_message_id text;

create unique index if not exists conversations_whatsapp_message_id_idx
  on conversations (whatsapp_message_id)
  where whatsapp_message_id is not null;
