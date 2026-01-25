"""
Test script: LLM-powered Google Doc finder
Compares customer email from Gong with Drive search results using Claude
"""

import os
import sys
import json
import requests
from dotenv import load_dotenv
from anthropic import Anthropic
from google.oauth2 import service_account
from googleapiclient.discovery import build

load_dotenv()

def get_customer_email_from_gong(call_id: str) -> dict:
    """Fetch Gong call and extract customer participant email."""
    api_key = os.getenv("GONG_API_KEY")
    api_secret = os.getenv("GONG_API_SECRET")
    if not api_key or not api_secret:
        raise ValueError("GONG_API_KEY and GONG_API_SECRET must be set")

    print(f"[1/4] Fetching Gong call {call_id}...")
    response = requests.post(
        "https://api.gong.io/v2/calls/extensive",
        auth=(api_key, api_secret),
        headers={"Content-Type": "application/json"},
        json={
            "filter": {"callIds": [call_id]},
            "contentSelector": {"exposedFields": {"parties": True}}
        }
    )
    response.raise_for_status()
    call_data = response.json()["calls"][0]

    # Extract customer participants (not @temporal.io)
    parties = call_data.get("parties", [])
    customer_participants = []

    for party in parties:
        email = party.get("emailAddress", "")
        name = party.get("name", "")
        if email and not email.endswith("@temporal.io"):
            customer_participants.append({"email": email, "name": name})

    if not customer_participants:
        raise ValueError("No customer participants found")

    print(f"  ✓ Found {len(customer_participants)} customer participant(s):")
    for p in customer_participants:
        print(f"    - {p['name']} <{p['email']}>")

    return {
        "participants": customer_participants,
        "primary_email": customer_participants[0]["email"]  # Use first as primary
    }


def search_drive_for_docs(customer_participants: list) -> dict:
    """Search Google Drive for docs containing customer email addresses."""
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not credentials_path:
        raise ValueError("GOOGLE_APPLICATION_CREDENTIALS must be set")

    credentials = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    service = build("drive", "v3", credentials=credentials)

    print(f"\n[2/4] Searching Drive for customer email addresses...")

    # First, let's search by company name from email to find the folder
    domain = customer_participants[0]["email"].split("@")[1]
    company_prefix = domain.split(".")[0]  # e.g., "acme" from "acme.io"

    print(f"\n  Step 2a: Searching for folders matching '{company_prefix}'...")
    folder_query = f"name contains '{company_prefix}' and mimeType='application/vnd.google-apps.folder'"
    folder_results = service.files().list(
        q=folder_query,
        fields="files(id, name)",
        pageSize=20,
        corpora='allDrives',
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()

    folders = folder_results.get("files", [])
    folder_docs_found = []

    if folders:
        print(f"    ✓ Found {len(folders)} folder(s):")
        for f in folders:
            print(f"      • {f['name']} (ID: {f['id']})")

            # Get docs in this folder
            docs_in_folder_query = f"'{f['id']}' in parents and mimeType='application/vnd.google-apps.document'"
            docs_in_folder_results = service.files().list(
                q=docs_in_folder_query,
                fields="files(id, name)",
                pageSize=10,
                corpora='allDrives',
                supportsAllDrives=True,
                includeItemsFromAllDrives=True
            ).execute()
            docs_in_folder = docs_in_folder_results.get("files", [])
            if docs_in_folder:
                for d in docs_in_folder:
                    print(f"        - {d['name']}")
                    folder_docs_found.append({"id": d["id"], "name": d["name"], "folder": f["name"]})
    else:
        print(f"    ✗ No folders found matching '{company_prefix}'")

    # Now try each customer participant email until we find docs
    print(f"\n  Step 2b: Searching for docs containing customer emails...")
    for participant in customer_participants:
        email = participant["email"]
        name = participant["name"]

        print(f"\n  Trying: {name} <{email}>")

        # Search for docs containing this email address
        query = f"fullText contains '{email}' and mimeType='application/vnd.google-apps.document'"
        results = service.files().list(
            q=query,
            fields="files(id, name)",
            pageSize=20,
            corpora='allDrives',
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()

        docs = results.get("files", [])

        if docs:
            print(f"    ✓ Found {len(docs)} doc(s) containing this email:")
            for doc in docs:
                print(f"      • {doc['name']}")

            return {
                "matched_email": email,
                "matched_name": name,
                "docs": [{"id": d["id"], "name": d["name"]} for d in docs],
                "match_type": "email"
            }
        else:
            print(f"    ✗ No docs found for this email")

    # Fallback: If email search failed but we found folder docs, use those
    if folder_docs_found:
        print(f"\n  ℹ No docs found by email, but found {len(folder_docs_found)} doc(s) in company folder")
        print(f"  Falling back to folder-based matching...")
        return {
            "matched_email": customer_participants[0]["email"],
            "matched_name": customer_participants[0]["name"],
            "docs": folder_docs_found,
            "match_type": "folder"
        }

    # No docs found at all
    print(f"\n  ✗ No docs found by email or folder search")
    return {
        "error": "No docs found",
        "tried_emails": [p["email"] for p in customer_participants],
        "tried_folder": company_prefix
    }


def ask_claude_to_find_doc(matched_email: str, matched_name: str, docs: list) -> dict:
    """Use Claude to pick the meeting notes doc from search results."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY must be set")

    client = Anthropic(api_key=api_key)

    print(f"\n[3/4] Asking Claude to identify meeting notes doc...")

    prompt = f"""You are helping find the correct Google Doc for meeting notes.

We searched Google Drive for docs containing this customer's email and found these results:

Customer: {matched_name} <{matched_email}>

Documents found:
{json.dumps(docs, indent=2)}

Task: Identify which document is used to write MEETING NOTES after each call.

Important context:
- "Use Case" docs often ALSO contain meeting notes sections
- If there's BOTH a dedicated "Notes" doc AND a "Use Case" doc, prefer the Notes doc
- If there's ONLY a "Use Case" doc, it likely contains the meeting notes - select it
- Exclude docs that are clearly not for notes (e.g., "Sales Deck", "Proposal", "Contract")

Return ONLY valid JSON in one of these formats:

Single match (high confidence):
{{"doc_id": "abc123", "doc_name": "Meeting Notes", "confidence": "high", "reasoning": "..."}}

Multiple matches (need user choice):
{{"options": [{{"doc_id": "...", "doc_name": "..."}}, ...], "needs_user_choice": true, "reasoning": "..."}}

No valid doc found:
{{"error": "No meeting notes doc found", "reasoning": "..."}}
"""

    message = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}]
    )

    output = message.content[0].text
    print(f"  ✓ Claude response received")
    print(f"\n  Raw Claude output:")
    print(f"  {'-' * 56}")
    print(f"  {output}")
    print(f"  {'-' * 56}\n")

    # Parse JSON response
    try:
        # Extract JSON from markdown code blocks if present
        if "```json" in output:
            json_str = output.split("```json")[1].split("```")[0].strip()
        elif "```" in output:
            json_str = output.split("```")[1].split("```")[0].strip()
        else:
            json_str = output.strip()

        result = json.loads(json_str)
        return result
    except json.JSONDecodeError as e:
        print(f"  ✗ Failed to parse Claude response as JSON: {e}")
        print(f"  Raw output: {output}")
        raise


def prompt_user_for_choice(options: list) -> str:
    """Display options and get user selection."""
    print(f"\n[4/4] Multiple meeting notes docs found:")
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt['doc_name']}")

    while True:
        try:
            choice = input("\nEnter number to select: ")
            idx = int(choice) - 1
            if 0 <= idx < len(options):
                return options[idx]["doc_id"]
            else:
                print(f"Invalid choice. Enter 1-{len(options)}")
        except (ValueError, KeyboardInterrupt):
            print("\nAborted")
            sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 test_llm_doc_finder.py <gong-call-id>")
        sys.exit(1)

    call_id = sys.argv[1]

    print("=" * 60)
    print("LLM-Powered Doc Finder Test")
    print("=" * 60)

    # Step 1: Get customer email from Gong
    customer_info = get_customer_email_from_gong(call_id)
    customer_participants = customer_info["participants"]

    # Step 2: Search Drive by email
    drive_results = search_drive_for_docs(customer_participants)

    # Check if search failed
    if "error" in drive_results:
        print(f"\n✗ Error: {drive_results['error']}")
        print(f"  Tried: {', '.join(drive_results['tried_emails'])}")
        sys.exit(1)

    # Step 3: Ask Claude to identify meeting notes doc
    result = ask_claude_to_find_doc(
        drive_results["matched_email"],
        drive_results["matched_name"],
        drive_results["docs"]
    )

    # Step 4: Handle result
    if "error" in result:
        print(f"\n✗ Error: {result['error']}")
        print(f"  Reasoning: {result.get('reasoning', 'N/A')}")
        sys.exit(1)

    elif result.get("needs_user_choice"):
        print(f"\n  Reasoning: {result.get('reasoning', 'Multiple matches found')}")
        doc_id = prompt_user_for_choice(result["options"])

    else:
        doc_id = result["doc_id"]
        doc_name = result.get("doc_name", "Unknown")
        confidence = result.get("confidence", "unknown")
        reasoning = result.get("reasoning", "N/A")

        print(f"\n[4/4] Found meeting notes doc!")
        print(f"  Doc: {doc_name}")
        print(f"  Confidence: {confidence}")
        print(f"  Reasoning: {reasoning}")

    doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"
    print(f"\n{'=' * 60}")
    print(f"✓ SUCCESS")
    print(f"{'=' * 60}")
    print(f"Doc URL: {doc_url}")
    print()


if __name__ == "__main__":
    main()
