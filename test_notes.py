"""Run: python test_notes.py
Tests whether the notes table exists and accepts inserts.
"""
import os, json
from dotenv import load_dotenv
load_dotenv()

from auth import get_admin_client

admin = get_admin_client()
TEST_USER = os.getenv("TEST_USER_ID", "00000000-0000-0000-0000-000000000001")

print("=== 1. Check table exists (SELECT) ===")
try:
    r = admin.table("notes").select("id").limit(1).execute()
    print(f"  OK — rows returned: {len(r.data)}  data={r.data}")
except Exception as e:
    print(f"  FAIL: {e}")

print("\n=== 2. Try INSERT ===")
try:
    r = admin.table("notes").insert({
        "user_id": TEST_USER,
        "subject": "biology",
        "chapter": "test chapter",
        "title": "test note",
        "points": {"definition": "test", "concepts": ["a", "b"], "examples": [], "exam_tips": []},
    }).execute()
    print(f"  INSERT result: data={r.data}")
    if r.data:
        inserted_id = r.data[0].get("id")
        print(f"  SUCCESS — inserted id={inserted_id}")
        # Clean up
        admin.table("notes").delete().eq("id", inserted_id).execute()
        print(f"  Cleaned up test row.")
    else:
        print("  NO DATA RETURNED — possible RLS block or schema mismatch")
except Exception as e:
    import traceback
    print(f"  EXCEPTION: {e}")
    traceback.print_exc()

print("\n=== 3. Check columns (try inserting with wrong field to see error) ===")
try:
    r = admin.table("notes").select("id, user_id, subject, chapter, title, points, created_at").limit(0).execute()
    print(f"  Column SELECT OK")
except Exception as e:
    print(f"  Column check FAIL: {e}")
