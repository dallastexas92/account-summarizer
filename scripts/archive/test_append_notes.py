"""
Test script: LLM-powered note insertion
Fetches real Gong transcript and uses Claude to determine insertion points
"""

import os
import sys
import json
import requests
from dotenv import load_dotenv
from anthropic import Anthropic
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime

load_dotenv()


def fetch_gong_transcript(call_id: str) -> dict:
    """Fetch transcript from Gong API - mirrors activities.py logic."""
    api_key = os.getenv("GONG_API_KEY")
    api_secret = os.getenv("GONG_API_SECRET")

    if not api_key or not api_secret:
        raise ValueError("GONG_API_KEY and GONG_API_SECRET must be set")

    print(f"[1/6] Fetching Gong transcript for call {call_id}...")

    # Fetch call metadata
    meta_response = requests.post(
        "https://api.gong.io/v2/calls/extensive",
        auth=(api_key, api_secret),
        headers={"Content-Type": "application/json"},
        json={
            "filter": {"callIds": [call_id]},
            "contentSelector": {"exposedFields": {"parties": True}}
        }
    )
    meta_response.raise_for_status()
    call_data = meta_response.json()["calls"][0]
    metadata = call_data.get("metaData", {})

    # Fetch transcript
    transcript_response = requests.post(
        "https://api.gong.io/v2/calls/transcript",
        auth=(api_key, api_secret),
        headers={"Content-Type": "application/json"},
        json={"filter": {"callIds": [call_id]}}
    )
    transcript_response.raise_for_status()
    call_transcript = transcript_response.json()["callTranscripts"][0]

    # Parse transcript
    transcript_lines = []
    for entry in call_transcript["transcript"]:
        speaker_id = entry["speakerId"]
        sentences = entry.get("sentences", [])
        text = " ".join([s["text"] for s in sentences])
        transcript_lines.append(f"Speaker {speaker_id}: {text}")

    transcript_text = "\n".join(transcript_lines)

    # Get call date (raw format from Gong - will be parsed later)
    call_date = metadata.get("scheduled", "")

    # Parse for display only
    try:
        if call_date.isdigit():
            call_datetime = datetime.fromtimestamp(int(call_date))
        else:
            call_datetime = datetime.fromisoformat(call_date.replace("Z", "+00:00"))
        call_date_display = call_datetime.strftime("%Y-%m-%d")
    except:
        call_date_display = call_date

    print(f"  ✓ Call date: {call_date_display}")
    print(f"  ✓ Transcript: {len(transcript_text)} chars")

    return {
        "call_id": call_id,
        "title": metadata.get("title", ""),
        "call_date": call_date,  # Raw format
        "call_date_display": call_date_display,
        "transcript_text": transcript_text,
        "parties": call_data.get("parties", [])
    }


def find_google_doc(call_id: str, parties: list[dict]) -> str:
    """Find meeting notes doc using LLM - mirrors llm_find_google_doc activity."""
    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

    print(f"\n[2/6] Finding Google Doc...")

    # Extract customer participants from provided parties data
    customer_participants = []
    for party in parties:
        email = party.get("emailAddress", "")
        name = party.get("name", "")
        if email and not email.endswith("@temporal.io"):
            customer_participants.append({"email": email, "name": name})

    if not customer_participants:
        raise ValueError("No customer participants found")

    # Search Google Drive by email
    credentials = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    service = build("drive", "v3", credentials=credentials)

    docs_by_email = []
    matched_email = ""
    matched_name = ""

    for participant in customer_participants:
        email = participant["email"]
        name = participant["name"]
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
            docs_by_email = [{"id": d["id"], "name": d["name"]} for d in docs]
            matched_email = email
            matched_name = name
            break

    if not docs_by_email:
        raise ValueError("No docs found containing customer email")

    # Ask Claude to identify meeting notes doc
    client = Anthropic(api_key=anthropic_api_key)
    prompt = f"""You are helping find the correct Google Doc for meeting notes.

Customer: {matched_name} <{matched_email}>

Documents found:
{json.dumps(docs_by_email, indent=2)}

Task: Identify which document is used to write MEETING NOTES after each call.

Important context:
- "Use Case" docs often ALSO contain meeting notes sections
- If there's BOTH a dedicated "Notes" doc AND a "Use Case" doc, prefer the Notes doc
- If there's ONLY a "Use Case" doc, it likely contains the meeting notes - select it
- Exclude docs that are clearly not for notes (e.g., "Sales Deck", "Proposal", "Contract")

Return ONLY valid JSON:
{{"doc_id": "abc123", "doc_name": "Meeting Notes", "confidence": "high", "reasoning": "..."}}
"""

    message = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}]
    )

    output = message.content[0].text

    if "```json" in output:
        json_str = output.split("```json")[1].split("```")[0].strip()
    else:
        json_str = output.strip()

    result = json.loads(json_str)
    doc_id = result.get("doc_id")
    doc_name = result.get("doc_name", "Unknown")

    if not doc_id:
        print(f"  ✗ ERROR: Claude did not return a doc_id")
        print(f"  → Full response: {result}")
        raise ValueError(f"Claude did not return a doc_id. Response: {result}")

    print(f"  ✓ Found: {doc_name}")

    return f"https://docs.google.com/document/d/{doc_id}/edit"


def read_existing_snapshot(doc_url: str) -> str:
    """Read existing snapshot from doc - mirrors read_google_doc activity."""
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

    print(f"\n[3/6] Reading existing snapshot from doc...")

    doc_id = doc_url.split("/d/")[1].split("/")[0]
    credentials = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=["https://www.googleapis.com/auth/documents"]
    )
    service = build("docs", "v1", credentials=credentials)

    doc = service.documents().get(documentId=doc_id).execute()

    # Extract snapshot section
    full_text = ""
    for element in doc.get("body", {}).get("content", []):
        if "paragraph" in element:
            for text_run in element["paragraph"].get("elements", []):
                if "textRun" in text_run:
                    full_text += text_run["textRun"].get("content", "")

    SNAPSHOT_START = "=== ACCOUNT SNAPSHOT ==="
    SNAPSHOT_END = "=== END SNAPSHOT ==="

    if SNAPSHOT_START in full_text and SNAPSHOT_END in full_text:
        start_idx = full_text.index(SNAPSHOT_START)
        end_idx = full_text.index(SNAPSHOT_END) + len(SNAPSHOT_END)
        snapshot = full_text[start_idx:end_idx]
        print(f"  ✓ Found existing snapshot ({len(snapshot)} chars)")
        return snapshot
    else:
        print(f"  ℹ No existing snapshot found")
        return ""


def structure_with_claude(transcript_data: dict, existing_snapshot: str) -> dict:
    """Structure transcript with Claude - mirrors structure_with_claude activity."""
    api_key = os.getenv("ANTHROPIC_API_KEY")

    print(f"\n[4/6] Structuring with Claude...")

    client = Anthropic(api_key=api_key)

    # Use the exact prompt from activities.py (simplified for testing)
    prompt = f"""You are creating structured call notes for Temporal AEs and SAs.

EXISTING ACCOUNT SNAPSHOT:
{existing_snapshot if existing_snapshot else "No existing snapshot"}

NEW CALL:
Title: {transcript_data['title']}
Date: {transcript_data['call_date_display']}

Transcript:
{transcript_data['transcript_text'][:8000]}

Output TWO sections separated by "---SPLIT---":

**SECTION 1: Updated Account Snapshot**
=== ACCOUNT SNAPSHOT ===
Primary Use Case: [summary]
Current Solution: [what they use]
Why Temporal: [reasons]
Why Now: [urgency]
Key Stakeholders: [decision makers]
Business Impact: [impact]
Timing/Priority: [timeline]
Workload Sizing: [scale estimate]
Risks: [blockers/concerns]
=== END SNAPSHOT ===

---SPLIT---

**SECTION 2: Call Notes**
**Participants**
[names and roles]

**Use Case/Context**
[what was discussed]

**Technical Details**
[technical requirements]

**Next Steps**
[action items]
"""

    message = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    )

    output = message.content[0].text

    # Parse output
    if "---SPLIT---" in output:
        parts = output.split("---SPLIT---")
        snapshot = parts[0].strip()
        call_notes = parts[1].strip()
    else:
        # Fallback if no split marker
        snapshot = output[:1000]
        call_notes = output[1000:]

    print(f"  ✓ Generated snapshot: {len(snapshot)} chars")
    print(f"  ✓ Generated call notes: {len(call_notes)} chars")

    return {
        "snapshot": snapshot,
        "call_notes": call_notes
    }


def read_doc_structure(doc_url: str) -> dict:
    """Read the full document structure including content and elements."""
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not credentials_path:
        raise ValueError("GOOGLE_APPLICATION_CREDENTIALS must be set")

    doc_id = doc_url.split("/d/")[1].split("/")[0]
    credentials = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=["https://www.googleapis.com/auth/documents"]
    )
    service = build("docs", "v1", credentials=credentials)

    print(f"  Reading document structure...")
    doc = service.documents().get(documentId=doc_id).execute()

    # Extract text content with structure info
    content_elements = doc.get("body", {}).get("content", [])
    structured_content = []

    for element in content_elements:
        if "paragraph" in element:
            para = element["paragraph"]
            text = ""
            for text_element in para.get("elements", []):
                if "textRun" in text_element:
                    text += text_element["textRun"].get("content", "")

            style = para.get("paragraphStyle", {}).get("namedStyleType", "NORMAL_TEXT")
            start_index = element.get("startIndex")
            end_index = element.get("endIndex")

            if text.strip():  # Only include non-empty paragraphs
                structured_content.append({
                    "text": text.strip(),
                    "style": style,
                    "start_index": start_index,
                    "end_index": end_index
                })
        elif "table" in element:
            structured_content.append({
                "text": "[TABLE]",
                "style": "TABLE",
                "start_index": element.get("startIndex"),
                "end_index": element.get("endIndex")
            })

    print(f"  ✓ Found {len(structured_content)} content elements")
    return {
        "doc_id": doc_id,
        "full_doc": doc,
        "structured_content": structured_content
    }


def ask_claude_where_to_insert(doc_structure: dict, call_date: str, snapshot_text: str, call_notes_text: str) -> dict:
    """Use Claude to determine where to insert snapshot and call notes."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY must be set")

    client = Anthropic(api_key=api_key)

    print(f"  Asking Claude where to insert content...")

    # Prepare doc structure for Claude
    content_summary = []
    for i, elem in enumerate(doc_structure["structured_content"][:50]):  # First 50 elements
        content_summary.append({
            "index": i,
            "text": elem["text"][:200],  # First 200 chars
            "style": elem["style"],
            "start_index": elem["start_index"],
            "end_index": elem["end_index"]
        })

    prompt = f"""You are helping insert structured meeting notes into a Google Doc.

DOCUMENT STRUCTURE (first 50 elements):
{json.dumps(content_summary, indent=2)}

TASK: Determine where to insert two pieces of content:

1. **Account Snapshot** (replace existing):
{snapshot_text[:500]}...

2. **Call Notes** (insert under date heading for {call_date}):
{call_notes_text[:500]}...

INSTRUCTIONS:

1. **Snapshot placement:**
   - IF there's an existing "Use Case:" table/template at the top:
     * Find the end of that table (its end_index)
     * Look for existing snapshot markers (=== ACCOUNT SNAPSHOT ===)
     * If snapshot exists, REPLACE it (provide start/end indices)
     * If snapshot doesn't exist, INSERT it right after the template table
   - IF there's NO template:
     * Look for existing snapshot markers
     * If snapshot exists, REPLACE it
     * If snapshot doesn't exist, INSERT at index 1 (top of doc)

2. **Call notes placement:**
   - Find the meeting notes date heading that matches {call_date}
   - Look for "Attendees:" line after that heading
   - Insert notes right after the "Attendees:" line
   - If no "Attendees:" line, insert after the date heading
   - If no date heading exists for {call_date}, return "needs_date_block_creation": true

Return ONLY valid JSON:
{{
  "snapshot_location": {{
    "action": "replace" | "insert",
    "insert_index": <number>,       // where to insert (if action=insert OR if replacing, where old snapshot starts)
    "delete_start": <number>,       // only if action=replace
    "delete_end": <number>,         // only if action=replace
    "reasoning": "..."
  }},
  "notes_location": {{
    "insert_index": <number>,
    "reasoning": "..."
  }} OR {{"needs_date_block_creation": true, "reasoning": "..."}},
  "confidence": "high|medium|low"
}}
"""

    message = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}]
    )

    output = message.content[0].text
    print(f"  ✓ Claude response received")

    # Parse JSON response
    try:
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


def show_dry_run(doc_structure: dict, insertion_plan: dict, snapshot: str, call_notes: str):
    """Show what will be written where."""
    print(f"\n[3/5] DRY RUN - What will be written:")
    print("=" * 80)

    # Snapshot placement
    if "snapshot_location" in insertion_plan:
        snap_loc = insertion_plan["snapshot_location"]
        action = snap_loc.get("action", "insert")
        if action == "replace":
            print(f"\n1. SNAPSHOT (replace at indices {snap_loc.get('delete_start')}-{snap_loc.get('delete_end')}):")
        else:
            print(f"\n1. SNAPSHOT (insert at index {snap_loc.get('insert_index')}):")
        print(f"   Action: {action}")
        print(f"   Reasoning: {snap_loc['reasoning']}")
        print(f"   Content preview:\n{snapshot[:300]}...\n")

    # Call notes insertion
    if "notes_location" in insertion_plan:
        notes_loc = insertion_plan["notes_location"]
        if notes_loc.get("needs_date_block_creation"):
            print(f"\n2. CALL NOTES: ⚠️  Date block missing!")
            print(f"   Reasoning: {notes_loc['reasoning']}")
        elif notes_loc.get("skipped"):
            print(f"\n2. CALL NOTES: ⚠️  Skipped")
            print(f"   Reasoning: {notes_loc['reasoning']}")
        else:
            print(f"\n2. CALL NOTES (insert at index {notes_loc['insert_index']}):")
            print(f"   Reasoning: {notes_loc['reasoning']}")
            print(f"   Content preview:\n{call_notes[:300]}...\n")

    print("=" * 80)


def execute_writes(doc_id: str, insertion_plan: dict, snapshot: str, call_notes: str):
    """Actually write to the document."""
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    credentials = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=["https://www.googleapis.com/auth/documents"]
    )
    service = build("docs", "v1", credentials=credentials)

    print(f"\n[4/5] Executing writes...")

    requests_body = []

    # Step 1: Insert call notes FIRST (before indices shift)
    if "notes_location" in insertion_plan:
        notes_loc = insertion_plan["notes_location"]
        if not notes_loc.get("needs_date_block_creation") and not notes_loc.get("skipped"):
            requests_body.append({
                "insertText": {
                    "location": {"index": notes_loc["insert_index"]},
                    "text": f"\n{call_notes}\n\n"
                }
            })
            print(f"  ✓ Queued call notes insertion at index {notes_loc['insert_index']}")
        elif notes_loc.get("skipped"):
            print(f"  ⊘ Skipping call notes insertion (date block not found)")

    # Step 2: Handle snapshot (replace or insert)
    if "snapshot_location" in insertion_plan:
        snap_loc = insertion_plan["snapshot_location"]
        action = snap_loc.get("action", "insert")

        if action == "replace":
            # Delete old snapshot
            requests_body.append({
                "deleteContentRange": {
                    "range": {
                        "startIndex": snap_loc["delete_start"],
                        "endIndex": snap_loc["delete_end"]
                    }
                }
            })
            # Insert new snapshot at same location
            requests_body.append({
                "insertText": {
                    "location": {"index": snap_loc["insert_index"]},
                    "text": f"{snapshot}\n\n"
                }
            })
            print(f"  ✓ Queued snapshot replacement at indices {snap_loc['delete_start']}-{snap_loc['delete_end']}")
        else:
            # Just insert snapshot
            requests_body.append({
                "insertText": {
                    "location": {"index": snap_loc["insert_index"]},
                    "text": f"{snapshot}\n\n"
                }
            })
            print(f"  ✓ Queued snapshot insertion at index {snap_loc['insert_index']}")

    # Execute batch update
    if requests_body:
        service.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": requests_body}
        ).execute()
        print(f"  ✓ Successfully updated document!")
    else:
        print(f"  ⚠️  No writes to execute")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 test_append_notes.py <call_id> <--dry-run|--execute>")
        print("Example: python3 test_append_notes.py 7782342274025937895 --dry-run")
        sys.exit(1)

    call_id = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else "--dry-run"

    print("=" * 80)
    print("LLM-Powered Note Insertion Test")
    print("=" * 80)

    # Step 1: Fetch Gong transcript
    transcript_data = fetch_gong_transcript(call_id)

    # Step 2: Find Google Doc (reuses parties from transcript)
    doc_url = find_google_doc(call_id, transcript_data["parties"])

    # Step 3: Read existing snapshot
    existing_snapshot = read_existing_snapshot(doc_url)

    # Step 4: Structure with Claude
    structured = structure_with_claude(transcript_data, existing_snapshot)

    # Step 5: Read doc structure and determine insertion points (with retry for missing date block)
    max_retries = 3
    retry_count = 0
    insertion_plan = None

    while retry_count < max_retries:
        print(f"\n[5/6] Reading document structure...")
        doc_structure = read_doc_structure(doc_url)

        # Step 6: Ask Claude where to insert
        print(f"\n[6/6] Asking Claude where to insert content...")
        insertion_plan = ask_claude_where_to_insert(
            doc_structure,
            transcript_data["call_date_display"],
            structured["snapshot"],
            structured["call_notes"]
        )

        # Check if date block is missing
        notes_location = insertion_plan.get("notes_location", {})
        if notes_location.get("needs_date_block_creation"):
            print(f"\n⚠️  MISSING DATE BLOCK")
            print(f"=" * 80)
            print(f"The document does not have a meeting notes block for the call on {transcript_data['call_date_display']}")
            print(f"\nPlease:")
            print(f"  1. Open the document: {doc_url}")
            print(f"  2. Add a meeting notes block for this call (use '@meeting notes' in Google Docs)")
            print(f"=" * 80)

            retry = input(f"\nPress Enter when ready to retry, or type 'skip' to continue without inserting call notes: ").strip().lower()
            if retry == 'skip':
                print(f"\n⚠️  Skipping call notes insertion - will only insert snapshot")
                # Remove notes_location to skip inserting call notes
                insertion_plan["notes_location"] = {"skipped": True, "reasoning": "User chose to skip - date block not found"}
                break

            retry_count += 1
            if retry_count < max_retries:
                print(f"\nRetrying ({retry_count}/{max_retries})...")
            else:
                print(f"\n✗ Max retries reached. Call notes will not be inserted.")
                insertion_plan["notes_location"] = {"skipped": True, "reasoning": "Max retries reached - date block not found"}
                break
        else:
            # Date block found, proceed
            break

    # Show dry run
    print(f"\nDRY RUN - What will be written:")
    print("=" * 80)
    show_dry_run(doc_structure, insertion_plan, structured["snapshot"], structured["call_notes"])

    # Execute or skip
    if mode == "--execute":
        confirm = input("\nExecute writes? (yes/no): ")
        if confirm.lower() == "yes":
            execute_writes(doc_structure["doc_id"], insertion_plan, structured["snapshot"], structured["call_notes"])
            print(f"\n✓ SUCCESS - Document updated!")
            print(f"View: {doc_url}")
        else:
            print("\n✗ Aborted")
    else:
        print(f"\nDRY RUN COMPLETE - Use --execute to actually write")
        print(f"Run: python3 test_append_notes.py {call_id} --execute")


if __name__ == "__main__":
    main()
