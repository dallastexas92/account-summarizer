#!/usr/bin/env python3
"""
Test Google Docs date element matching logic.

Usage: python scripts/test_date_matching.py

Tests the logic for finding meeting notes blocks by date:
- Searches for HEADING_2 paragraphs with dateElement
- Extracts timestamp from dateElement.dateElementProperties.timestamp
- Matches against test call date (configurable in script)
- Shows where notes would be inserted (after "Attendees:" paragraph)

Useful for debugging date matching failures and index positioning.
Requires TEST_DOC_URL to be set in .env
"""

import os
from datetime import datetime
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

load_dotenv()

# Use a test doc URL for testing
doc_url = os.getenv("TEST_DOC_URL")
if not doc_url:
    print("ERROR: TEST_DOC_URL not set in .env")
    exit(1)

doc_id = doc_url.split("/d/")[1].split("/")[0]
print(f"Testing with doc ID: {doc_id}\n")

credentials = service_account.Credentials.from_service_account_file(
    os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
    scopes=["https://www.googleapis.com/auth/documents"]
)
service = build("docs", "v1", credentials=credentials)

# Fetch the document
doc = service.documents().get(documentId=doc_id).execute()

# Test date to search for (you can change this)
test_call_date = "2024-12-18T15:30:00Z"  # ISO format like Gong returns

# Parse the test date
if test_call_date.isdigit():
    call_datetime = datetime.fromtimestamp(int(test_call_date))
else:
    call_datetime = datetime.fromisoformat(test_call_date.replace("Z", "+00:00"))
call_date_str = call_datetime.strftime("%Y-%m-%d")

print(f"Looking for meeting block with date: {call_date_str}")
print("=" * 60 + "\n")

content_elements = doc.get("body", {}).get("content", [])
found_matching_heading = False
insert_index = None

for element in content_elements:
    if "paragraph" not in element:
        continue

    para = element["paragraph"]

    # Look for HEADING_2 with date
    if not found_matching_heading and para.get("paragraphStyle", {}).get("namedStyleType") == "HEADING_2":
        print(f"\n🔍 Found HEADING_2 at index {element.get('startIndex')}")

        for el in para.get("elements", []):
            if "dateElement" in el:
                print(f"   Found dateElement: {el['dateElement'].keys()}")

                # Try OLD way (wrong)
                timestamp_old = el["dateElement"].get("timestamp", "")
                print(f"   OLD way (direct): timestamp = '{timestamp_old}'")

                # Try NEW way (correct)
                timestamp_new = el["dateElement"].get("dateElementProperties", {}).get("timestamp", "")
                print(f"   NEW way (nested):  timestamp = '{timestamp_new}'")

                if timestamp_new:
                    block_date = datetime.fromisoformat(timestamp_new.replace("Z", "+00:00"))
                    block_date_str = block_date.strftime("%Y-%m-%d")
                    print(f"   📅 Block date: {block_date_str}")
                    print(f"   📅 Call date:  {call_date_str}")

                    if call_date_str == block_date_str:
                        found_matching_heading = True
                        print(f"   ✅ MATCH! Found matching HEADING_2")
                        break
                    else:
                        print(f"   ❌ No match (dates differ)")

    # After finding the heading, look for "Attendees:" paragraph
    elif found_matching_heading:
        for el in para.get("elements", []):
            if "textRun" in el:
                content = el["textRun"].get("content", "")
                if "Attendees:" in content or "attendees:" in content.lower():
                    insert_index = element.get("endIndex")
                    print(f"\n✅ Found 'Attendees' paragraph at index {insert_index}")
                    print(f"   Would insert notes here!")
                    break
        if insert_index:
            break

print("\n" + "=" * 60)
if insert_index:
    print(f"✅ SUCCESS: Would insert at index {insert_index}")
else:
    print(f"❌ FAILED: No matching meeting block found for date {call_date_str}")
    print(f"\nMake sure you have a meeting notes block with date {call_date_str} in the Google Doc!")
