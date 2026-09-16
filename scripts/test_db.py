"""Manual smoke test for the Supabase data-access helpers in app/db.py.

Run with: uv run python scripts/test_db.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import (
    create_conversation,
    create_user,
    get_client,
    get_recent_conversations,
    get_user_by_phone,
    normalize_phone,
)

TEST_PHONE = "+33612345678"
TEST_NAME = "Test User"


def delete_user(user_id: str) -> None:
    """Delete a user profile by id, relying on the schema's ON DELETE CASCADE for conversations."""
    get_client().table("user_profiles").delete().eq("id", user_id).execute()


def main() -> None:
    """Run each step of the smoke test in order, printing progress and results."""
    print("=== Setup: clearing any leftover test user from a previous run ===")
    existing = get_user_by_phone(TEST_PHONE)
    if existing:
        delete_user(existing["id"])
        print(f"Removed leftover test user {existing['id']}")
    else:
        print("No leftover test user found")

    print("\n=== Step 1: create test user ===")
    user = create_user(phone=TEST_PHONE, name=TEST_NAME)
    print(user)
    user_id = user["id"]

    print("\n=== Step 2: create two conversations (inbound, outbound) ===")
    inbound = create_conversation(
        user_id=user_id,
        direction="inbound",
        content="Just wrapped up the Q3 roadmap review with the VP.",
        media_type="text",
    )
    print("Inbound:", inbound)
    outbound = create_conversation(
        user_id=user_id,
        direction="outbound",
        content="Got it — sounds like a solid win. Anything you'd flag as a stakeholder story?",
        media_type="text",
    )
    print("Outbound:", outbound)

    print("\n=== Step 3: fetch user by phone ===")
    fetched_user = get_user_by_phone(normalize_phone(TEST_PHONE))
    print(fetched_user)

    print("\n=== Step 4: fetch recent conversations ===")
    conversations = get_recent_conversations(user_id)
    print(f"Found {len(conversations)} conversation(s):")
    for conversation in conversations:
        print(conversation)

    print("\n=== Step 5: delete test user ===")
    delete_user(user_id)
    print(f"Deleted user {user_id}")

    remaining = get_recent_conversations(user_id)
    if remaining:
        print(f"FAIL: cascade delete did not remove conversations, {len(remaining)} remain")
    else:
        print("OK: cascade delete removed all conversations for the user")


if __name__ == "__main__":
    main()
