import os
import base64
import json
import re
import requests
from anthropic import Anthropic
from temporalio import activity
from dataclasses import dataclass
from datetime import datetime, timedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build


@dataclass
class GongTranscript:
    call_id: str
    title: str
    call_date: str
    account_name: str
    participants: list[dict]
    transcript_text: str


# === GONG API ===

def normalize_company_name(name: str) -> str:
    """Normalize company name for comparison (lowercase, no spaces/hyphens)"""
    return name.lower().replace(" ", "").replace("-", "")


def extract_company_from_email(email: str) -> str:
    """
    Extract company name from email domain.

    Examples:
        user@acme.com → acme
        john@example.ai → example
        person@company-corp.io → companycorp
    """
    if "@" not in email:
        return ""

    domain = email.split("@")[1]

    # Remove common TLDs
    for tld in [".com", ".io", ".ai", ".net", ".org", ".co", ".edu"]:
        if domain.endswith(tld):
            domain = domain[:-len(tld)]
            break

    # Normalize (remove hyphens, dots)
    return normalize_company_name(domain)


def filter_calls_with_llm(calls: list, company_name: str, anthropic_api_key: str) -> list:
    """
    Use Claude to filter calls by customer company name.

    Args:
        calls: List of call objects from Gong API
        company_name: Company name to match (e.g., "Acme Corp", "Example Inc")
        anthropic_api_key: Anthropic API key

    Returns:
        Filtered list of calls where company_name is the customer
    """
    if not calls:
        return []

    from anthropic import Anthropic
    client = Anthropic(api_key=anthropic_api_key)

    # Build call list with titles AND participant domains for LLM
    call_list = []
    for i, call in enumerate(calls):
        title = call.get("metaData", {}).get("title", "Unknown")

        # Extract non-Temporal participant email domains
        parties = call.get("parties", [])
        customer_domains = []
        for party in parties:
            email = party.get("emailAddress", "")
            if email and not email.endswith("@temporal.io"):
                domain = email.split("@")[1] if "@" in email else ""
                if domain:
                    customer_domains.append(domain)

        domains_str = f" | Participants from: {', '.join(set(customer_domains))}" if customer_domains else ""
        call_list.append(f"{i}. {title}{domains_str}")

    call_list_str = "\n".join(call_list)

    prompt = f"""Filter calls where the CUSTOMER company is: {company_name}

Rules:
1. Include ONLY calls where {company_name} is the CUSTOMER/PROSPECT
2. Match against call title AND participant domains
3. Call formats vary: "Customer <> Temporal", "Temporal / Customer", etc.
4. Company names may vary: "Company.ai", "Company AI", "companyai" are all the same

CALLS:
{call_list_str}

CRITICAL: Return ONLY comma-separated numbers (e.g., "0,5,12") or "NONE". NO other text.

Examples:
Input: "126. Temporal / Acme.io - Sync | Participants from: acme.io"
If searching for "Acme": Output "126"

Input: "45. Example <> Temporal: Biweekly | Participants from: example.com"
If searching for "Example": Output "45"

Your response (numbers only):"""

    # Get model from environment or use default
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")

    response = client.messages.create(
        model=model,
        max_tokens=100,
        messages=[{"role": "user", "content": prompt}]
    )

    response_text = response.content[0].text.strip()

    if response_text == "NONE" or not response_text:
        return []

    # Parse response
    try:
        indices = [int(x.strip()) for x in response_text.split(",")]
        filtered_calls = [calls[i] for i in indices if 0 <= i < len(calls)]
        return filtered_calls
    except (ValueError, IndexError) as e:
        # Fallback to original calls if parsing fails
        activity.logger.warning(f"LLM response parsing failed: {e}, using all calls")
        return calls


@activity.defn
async def fetch_all_call_ids(company_name: str, max_calls: int = 50) -> tuple[list[str], int, str]:
    """
    Fetch all call IDs for an account from Gong API using time-windowing strategy.

    Args:
        company_name: Company name to filter calls (e.g., 'acme', 'example')
        max_calls: Maximum calls to return (default: 50)

    Returns:
        tuple: (call_ids, total_count, detected_account_name)
    """
    api_key = os.getenv("GONG_API_KEY")
    api_secret = os.getenv("GONG_API_SECRET")
    primary_user_ids = os.getenv("GONG_PRIMARY_USER_IDS", "")

    if not api_key or not api_secret:
        raise ValueError("GONG_API_KEY and GONG_API_SECRET must be set in .env")

    # Parse primary user IDs if configured
    user_ids = [uid.strip() for uid in primary_user_ids.split(",") if uid.strip()] if primary_user_ids else []

    auth = (api_key, api_secret)
    headers = {"Content-Type": "application/json"}

    activity.logger.info(f"Searching for calls from company: {company_name}")
    activity.logger.info("Using LLM-based filtering for accurate company matching")
    if user_ids:
        activity.logger.info(f"Using primaryUserIds filter: {len(user_ids)} users (efficiency boost!)")
    else:
        activity.logger.warning("No primaryUserIds filter - will be slow (add GONG_PRIMARY_USER_IDS to .env)")

    matching_calls = []
    total_api_calls = 0
    consecutive_empty_windows = 0
    max_consecutive_empty = 6  # Stop after 6 months of no activity
    window_days = 30

    activity.logger.info("Using time-windowing strategy: 30-day windows, newest first")

    # Work backwards from today in time windows (max 2 years = 24 months)
    for months_back in range(24):
        # Calculate window boundaries
        end_date = datetime.now() - timedelta(days=window_days * months_back)
        start_date = end_date - timedelta(days=window_days)

        # Format dates for Gong API
        from_date = start_date.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        to_date = end_date.strftime("%Y-%m-%dT%H:%M:%S+00:00")

        # Fetch all calls in this window (may require pagination within window)
        cursor = None
        window_calls = []  # Collect ALL calls from this window first
        window_api_calls = 0

        while True:
            body = {
                "filter": {
                    "fromDateTime": from_date,
                    "toDateTime": to_date
                },
                "contentSelector": {
                    "context": "Extended",
                    "exposedFields": {
                        "parties": True
                    }
                }
            }

            # Add primaryUserIds filter if configured
            if user_ids:
                body["filter"]["primaryUserIds"] = user_ids

            if cursor:
                body["cursor"] = cursor

            response = requests.post(
                "https://api.gong.io/v2/calls/extensive",
                auth=auth,
                headers=headers,
                json=body
            )

            total_api_calls += 1
            window_api_calls += 1

            if response.status_code != 200:
                activity.logger.error(f"Gong API Error: {response.status_code}")
                activity.logger.error(f"Response: {response.text}")
                response.raise_for_status()

            data = response.json()
            page_calls = data.get("calls", [])
            window_calls.extend(page_calls)  # Collect all calls

            # Check for pagination within this window
            records_info = data.get("records", {})
            cursor = records_info.get("cursor")

            if not cursor:
                break  # No more pages in this window

        # Use LLM to filter calls for this window
        anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
        if not anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY must be set")

        window_filtered = filter_calls_with_llm(window_calls, company_name, anthropic_api_key)
        window_matches = len(window_filtered)
        matching_calls.extend(window_filtered)

        activity.logger.info(f"Window {months_back + 1} ({start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}): {window_matches} matches from {len(window_calls)} calls ({window_api_calls} API calls)")

        # Track consecutive empty windows
        if window_matches == 0:
            consecutive_empty_windows += 1
            # Stop after 4 consecutive empty windows (whether account is dormant or doesn't exist)
            if consecutive_empty_windows >= max_consecutive_empty:
                if len(matching_calls) > 0:
                    activity.logger.info(f"No activity for {max_consecutive_empty} consecutive windows (dormant account), stopping")
                else:
                    activity.logger.info(f"No matches found after {max_consecutive_empty} consecutive windows (account may not exist or name mismatch)")
                activity.logger.info(f"Total API calls: {total_api_calls}")
                break
        else:
            consecutive_empty_windows = 0  # Reset counter when we find matches

        # Stop early if we have enough matches
        if len(matching_calls) >= max_calls:
            activity.logger.info(f"Found {max_calls}+ matching calls, stopping early")
            activity.logger.info(f"Total API calls: {total_api_calls}")
            break

    activity.logger.info(f"Total matching calls: {len(matching_calls)}")

    # Sort by most recent first
    matching_calls.sort(
        key=lambda c: c.get("metaData", {}).get("started", 0),
        reverse=True
    )

    # Extract call IDs and limit to max_calls
    call_ids = [
        call.get("metaData", {}).get("id")
        for call in matching_calls[:max_calls]
    ]

    total_count = len(matching_calls)

    activity.logger.info(f"Returning {len(call_ids)} call IDs (limited to {max_calls})")

    if total_count > max_calls:
        activity.logger.warning(f"Account has {total_count} calls, but only processing {max_calls} most recent")

    return call_ids, total_count, company_name


# === ACCOUNT INTELLIGENCE ACTIVITIES ===
# These activities implement the Account Intelligence workflow
# which analyzes ALL Gong calls for an account

# Configuration
MAX_CALLS_TO_ANALYZE = 30
TARGET_SUMMARY_LENGTH = 300  # words


@activity.defn
async def find_or_create_summaries_doc(account_name: str) -> str:
    """
    Activity 2: Find or create [Account Name] - LLM - Summary doc.

    Args:
        account_name: Account name (e.g., "Acme Corp")

    Returns:
        Doc URL
    """
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")

    if not credentials_path or not anthropic_api_key:
        raise ValueError("GOOGLE_APPLICATION_CREDENTIALS and ANTHROPIC_API_KEY must be set")

    activity.logger.info(f"Finding or creating summaries doc for: {account_name}")

    credentials = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=[
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/documents"
        ]
    )
    drive_service = build("drive", "v3", credentials=credentials)
    claude_client = Anthropic(api_key=anthropic_api_key)

    doc_title = f"{account_name} - LLM - Summary"

    # Step 1: Search for existing doc by exact title
    doc_query = f"name = '{doc_title}' and mimeType='application/vnd.google-apps.document'"
    activity.logger.info(f"Searching for doc: {doc_title}")

    doc_results = drive_service.files().list(
        q=doc_query,
        fields="files(id, name)",
        corpora='allDrives',
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()

    existing_docs = doc_results.get("files", [])

    if existing_docs:
        doc_id = existing_docs[0]["id"]
        doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"
        activity.logger.info(f"Found existing doc: {doc_url}")
        return doc_url

    activity.logger.info("Doc not found, will create new doc\n")

    # Step 2: Navigate folder hierarchy using environment variable
    accounts_folder_id = os.getenv("GOOGLE_DRIVE_ACCOUNTS_ROOT_FOLDER_ID")
    if not accounts_folder_id:
        raise Exception(
            "GOOGLE_DRIVE_ACCOUNTS_ROOT_FOLDER_ID not set in .env\n"
            "Find your 'Accounts & Prospects' folder ID from the Drive URL and add to .env"
        )
    activity.logger.info(f"Using accounts folder ID from env: {accounts_folder_id}\n")

    # Step 3: Determine letter folder (first character, uppercase or '0-9' for digits)
    first_char = account_name[0].upper()
    if first_char.isdigit():
        letter = "0-9"
    else:
        letter = first_char
    activity.logger.info(f"Account '{account_name}' belongs in letter folder: {letter}\n")

    # Step 4: Find letter folder
    letter_query = f"name = '{letter}' and '{accounts_folder_id}' in parents and mimeType='application/vnd.google-apps.folder'"
    letter_results = drive_service.files().list(
        q=letter_query,
        fields="files(id, name)",
        corpora='allDrives',
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()

    letter_folders = letter_results.get("files", [])
    if not letter_folders:
        raise Exception(f"Letter folder '{letter}' not found under accounts folder")
    letter_folder_id = letter_folders[0]["id"]
    activity.logger.info(f"Found letter folder '{letter}': {letter_folder_id}\n")

    # Step 5: List ALL folders in letter folder (no name filtering)
    all_folders_query = f"'{letter_folder_id}' in parents and mimeType='application/vnd.google-apps.folder'"
    all_folders_results = drive_service.files().list(
        q=all_folders_query,
        fields="files(id, name)",
        corpora='allDrives',
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()

    folders = all_folders_results.get("files", [])

    # If no folders exist in letter folder, we'll create one (skip LLM check)
    if not folders:
        activity.logger.info(f"No existing folders in letter '{letter}', creating new folder\n")

        # Capitalize first letter of company name for folder
        folder_name = account_name[0].upper() + account_name[1:] if len(account_name) > 1 else account_name.upper()

        folder_metadata = {
            "name": folder_name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [letter_folder_id]
        }

        created_folder = drive_service.files().create(
            body=folder_metadata,
            fields="id",
            supportsAllDrives=True
        ).execute()

        account_folder_id = created_folder["id"]
        activity.logger.info(f"Created new company folder '{folder_name}': {account_folder_id}\n")
    else:
        activity.logger.info(f"Found {len(folders)} folders in letter '{letter}', using LLM to pick correct one\n")

        # Step 6: Use LLM to pick the right folder (handles name variations like "company ai" → "company.ai")
        folder_list = "\n".join([
            f"{i+1}. {f['name']}"
            for i, f in enumerate(folders)
        ])

        prompt = f"""Pick the correct folder for company: {account_name}

Folders in letter '{letter}':
{folder_list}

IMPORTANT: Match is case-INSENSITIVE. "example" matches "Example", "acme" matches "ACME", etc.

Also match these variations:
- "acme corp" matches "Acme.io", "acmecorp", "Acme Corp"
- Handle dots, spaces, punctuation differences

Return ONLY the number (1, 2, 3, etc.) or "NONE" if no match exists.

Your response (number only):"""

        # Get model from environment or use default
        model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")

        response = claude_client.messages.create(
            model=model,
            max_tokens=10,
            messages=[{"role": "user", "content": prompt}]
        )

        choice_text = response.content[0].text.strip().upper()

        if choice_text == "NONE":
            # No matching folder found - create it with capitalized first letter
            activity.logger.info(f"No match found for '{account_name}', creating new folder in letter '{letter}'\n")

            # Capitalize first letter of company name for folder
            folder_name = account_name[0].upper() + account_name[1:] if len(account_name) > 1 else account_name.upper()

            folder_metadata = {
                "name": folder_name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [letter_folder_id]
            }

            created_folder = drive_service.files().create(
                body=folder_metadata,
                fields="id",
                supportsAllDrives=True
            ).execute()

            account_folder_id = created_folder["id"]
            activity.logger.info(f"Created new company folder '{folder_name}': {account_folder_id}\n")
        else:
            # LLM found a match
            try:
                choice_idx = int(choice_text) - 1
                if 0 <= choice_idx < len(folders):
                    selected_folder = folders[choice_idx]
                else:
                    raise ValueError(f"Invalid folder number: {choice_text}")
            except ValueError as e:
                raise Exception(f"LLM returned invalid response: {choice_text}. Error: {e}")

            account_folder_id = selected_folder["id"]
            activity.logger.info(f"LLM selected folder: {selected_folder['name']}\n")

    # Step 7: Create doc
    doc_metadata = {
        "name": doc_title,
        "mimeType": "application/vnd.google-apps.document",
        "parents": [account_folder_id]
    }

    created_doc = drive_service.files().create(
        body=doc_metadata,
        fields="id",
        supportsAllDrives=True
    ).execute()

    doc_id = created_doc["id"]
    doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"

    activity.logger.info(f"Created doc: {doc_url}\n")
    return doc_url


@activity.defn
async def fetch_and_summarize_call(call_id: str) -> tuple[str, str]:
    """
    Activity 3a: Fetch call from Gong and summarize with Claude.

    Atomic activity combining Gong metadata + transcript + Claude summarization.
    If Gong fails, we don't waste a Claude API call.

    Args:
        call_id: Gong call ID

    Returns:
        tuple: (formatted_summary, call_id) for idempotency check
    """
    api_key = os.getenv("GONG_API_KEY")
    api_secret = os.getenv("GONG_API_SECRET")
    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")

    if not api_key or not api_secret or not anthropic_api_key:
        raise ValueError("GONG_API_KEY, GONG_API_SECRET, and ANTHROPIC_API_KEY must be set")

    auth = (api_key, api_secret)
    headers = {"Content-Type": "application/json"}

    activity.logger.info(f"Fetching and summarizing call: {call_id}\n")

    # Fetch call metadata
    meta_response = requests.post(
        "https://api.gong.io/v2/calls/extensive",
        auth=auth,
        headers=headers,
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
        auth=auth,
        headers=headers,
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

    # Parse call date
    call_date_raw = metadata.get("scheduled", "")
    if isinstance(call_date_raw, str) and call_date_raw.isdigit():
        call_datetime = datetime.fromtimestamp(int(call_date_raw))
    elif isinstance(call_date_raw, str):
        call_datetime = datetime.fromisoformat(call_date_raw.replace("Z", "+00:00"))
    else:
        call_datetime = datetime.fromtimestamp(call_date_raw)

    call_date_str = call_datetime.strftime("%Y-%m-%d")

    # Extract participant names
    parties = call_data.get("parties", [])
    participant_names = [
        p.get("name", "Unknown")
        for p in parties
        if p.get("emailAddress") and not p.get("emailAddress", "").endswith("@temporal.io")
    ]

    activity.logger.info(f"Fetched call: {metadata.get('title', 'Untitled')}\n")

    # Summarize with Claude (REPORTER role)
    client = Anthropic(api_key=anthropic_api_key)

    # Calculate duration if available
    duration = metadata.get("duration", "")
    if duration:
        duration_mins = int(duration) // 60000  # Convert ms to minutes
        duration_str = f"{duration_mins} minutes"
    else:
        duration_str = "Unknown"

    prompt = f"""You are extracting key facts from a Temporal sales call. Be CONCISE and SPECIFIC.

CONTEXT:
- Temporal Technologies sells Temporal Cloud (managed SaaS)
- Many prospects use OSS (self-hosted) and evaluate Cloud migration
- Distinguish OSS usage vs Cloud evaluation

CALL METADATA:
- Date: {call_date_str}
- Title: {metadata.get('title', 'Untitled')}
- Duration: {duration_str}

OUTPUT FORMAT (omit sections if not relevant to this call):

**Call Type:** [Discovery | Technical | Commercial | Check-in | Enablement]

**Participants:**
- Temporal: [names with (AE) or (SA)]
- Customer: [names with roles]

**What Happened:** (2-3 sentences)
[Key discussion topics, decisions made, or insights gained from this call]

**Use Case & Business Impact:** (if discussed)
- What they're building: [specific workflows/processes]
- Business pain: [what's broken today, cost of status quo]
- Expected impact: [efficiency gains, cost savings, capabilities unlocked]

**Discovery Qualification:** (discovery calls only)
- Why do anything? [pain/status quo cost driving change]
- Why Temporal? [alternatives considered, why Temporal fits]
- Why now? [urgency, timeline drivers, forcing function]

**Technical Details:** (if discussed)
- Current setup: [OSS/Cloud, architecture, scale]
- Technical requirements: [integrations, features needed]
- Blockers: [technical issues or concerns]

**Commercial Details:** (if discussed)
- Deal stage signal: [early exploration | active POC | commercial negotiation | commitment pending]
- Pricing: [tier discussed, budget concerns]
- Timeline: [urgency, deadlines, milestones]
- Competition: [alternatives mentioned]

**Risks/Concerns:** (if any)
[Anything that could prevent progress - technical, commercial, or organizational]

**Action Items:**
- [Person]: [specific task] by [date/timeframe]

**Next Call Goal:** (if discussed)
[Purpose of next interaction, what needs to happen before then]

KEEP IT UNDER {TARGET_SUMMARY_LENGTH} WORDS. Be specific with names, numbers, dates. If information wasn't discussed, omit that section entirely.

CALL TRANSCRIPT:
{transcript_text}
"""

    # Get model from environment or use default
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")

    response = client.messages.create(
        model=model,
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )

    summary = response.content[0].text.strip()
    word_count = len(summary.split())
    activity.logger.info(f"Summary generated: {word_count} words\n")

    # Format summary with metadata
    participants_str = ", ".join(participant_names) if participant_names else "No external participants"

    formatted_summary = f"""=== CALL SUMMARY: {call_date_str} - {metadata.get('title', 'Untitled')} ===
Call ID: {call_id}
Participants: {participants_str}
Duration: {metadata.get('duration', 0) // 60} minutes

{summary}

===
"""

    return formatted_summary, call_id


@activity.defn
async def append_summary_to_doc(doc_url: str, summary: str, call_id: str) -> bool:
    """
    Activity 3b: Append summary to doc with rich text formatting (idempotent).

    Checks if call_id already exists in doc before appending.
    Prepends to beginning (newest first).
    Applies bold formatting to header line and metadata labels.

    Args:
        doc_url: Google Doc URL
        summary: Formatted summary text
        call_id: Call ID for idempotency check

    Returns:
        True if summary was appended, False if already existed (skipped)
    """
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

    if not credentials_path:
        raise ValueError("GOOGLE_APPLICATION_CREDENTIALS must be set")

    activity.logger.info(f"Appending formatted summary for call {call_id}\n")

    # Extract doc ID
    doc_id = doc_url.split("/d/")[1].split("/")[0]

    # Authenticate
    credentials = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=["https://www.googleapis.com/auth/documents"]
    )
    service = build("docs", "v1", credentials=credentials)

    # Read doc to check if call_id already exists (idempotency)
    doc = service.documents().get(documentId=doc_id).execute()
    doc_content = doc.get("body", {}).get("content", [])

    # Extract all text from doc and check for call_id
    # Must concatenate all textRuns since "Call ID:" and the actual ID might be in separate runs
    full_doc_text_parts = []
    for element in doc_content:
        if "paragraph" in element:
            for elem in element["paragraph"].get("elements", []):
                if "textRun" in elem:
                    full_doc_text_parts.append(elem["textRun"]["content"])

    full_doc_text = "".join(full_doc_text_parts)

    if f"Call ID: {call_id}" in full_doc_text:
        activity.logger.info(f"Call {call_id} already in doc, skipping (idempotent)\n")
        return False  # Already exists, did not append

    # Insert plain text only (no formatting)
    # Formatting will be applied later by write_intelligence_to_doc()
    insert_index = 1
    text_with_newline = summary + "\n"

    service.documents().batchUpdate(
        documentId=doc_id,
        body={"requests": [{
            "insertText": {
                "location": {"index": insert_index},
                "text": text_with_newline
            }
        }]}
    ).execute()

    activity.logger.info(f"Appended summary ({len(summary)} characters)\n")
    return True  # Successfully appended


@activity.defn
async def read_summaries_doc(doc_url: str) -> str:
    """
    Activity 4a: Read full content from summaries doc and sort chronologically.

    Summaries may be in random order due to parallel appending.
    This ensures Claude receives them chronologically (oldest → newest).

    Args:
        doc_url: Google Doc URL

    Returns:
        Full document text content with summaries sorted chronologically
    """
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

    if not credentials_path:
        raise ValueError("GOOGLE_APPLICATION_CREDENTIALS must be set")

    activity.logger.info("Reading summaries doc\n")

    # Extract doc ID
    doc_id = doc_url.split("/d/")[1].split("/")[0]

    # Authenticate
    credentials = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=["https://www.googleapis.com/auth/documents"]
    )
    service = build("docs", "v1", credentials=credentials)

    # Fetch document
    doc = service.documents().get(documentId=doc_id).execute()

    # Extract text from all paragraphs
    content_parts = []
    for element in doc.get("body", {}).get("content", []):
        if "paragraph" in element:
            paragraph = element["paragraph"]
            for elem in paragraph.get("elements", []):
                if "textRun" in elem:
                    content_parts.append(elem["textRun"]["content"])

    full_text = "".join(content_parts)

    # Parse summaries and sort chronologically
    import re
    summary_pattern = r"(=== CALL SUMMARY: (\d{4}-\d{2}-\d{2}) - .+?===.*?===)"
    matches = list(re.finditer(summary_pattern, full_text, re.DOTALL))

    if matches:
        # Extract summaries with their dates
        summaries = []
        for match in matches:
            summary_text = match.group(1)
            date_str = match.group(2)  # YYYY-MM-DD format
            summaries.append((date_str, summary_text))

        # Sort by date (oldest → newest)
        summaries.sort(key=lambda x: x[0])

        # Reconstruct text in chronological order
        sorted_text = "\n".join([summary for _, summary in summaries])

        activity.logger.info(f"Sorted {len(summaries)} summaries chronologically (oldest → newest)\n")

        return sorted_text
    else:
        # No summaries found, return as-is
        activity.logger.info(f"Read {len(full_text)} characters, no summaries parsed\n")
        return full_text


@activity.defn
async def synthesize_intelligence(summaries_text: str, account_name: str) -> dict:
    """
    Activity 4b: Synthesize intelligence from all call summaries.

    Uses Claude ANALYST role to identify patterns across ALL calls.

    Args:
        summaries_text: Full text from summaries doc
        account_name: Account name

    Returns:
        Intelligence dict with keys: commercial_summary, technical_summary,
        pain_points, next_steps, risk_assessment, call_history
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")

    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY must be set")

    client = Anthropic(api_key=api_key)

    num_calls = summaries_text.count("=== CALL SUMMARY:")

    activity.logger.info(f"Synthesizing intelligence from {num_calls} calls\n")

    # ANALYST role prompt - scannable account brief
    current_date = datetime.now().strftime("%Y-%m-%d")

    prompt = f"""Analyze ALL calls for {account_name} and create a scannable account brief for SA call prep.

CALL SUMMARIES (chronological, oldest → newest):
{summaries_text}

Generate account intelligence in JSON format with these fields:

{{
  "account": "{account_name}",
  "last_updated": "{current_date}",
  "total_calls": {num_calls},

  "quick_context": [
    "Use case: What they're building/solving with Temporal",
    "Deal stage: Where they are in buying journey and momentum",
    "Setup: OSS → Cloud migration or net-new Cloud adoption",
    "Key stakeholders: Decision makers and their sentiment",
    "Timeline: Target dates or urgency level if mentioned"
  ],

  "blocking_progress": [
    "Technical blocker with context",
    "Commercial/budget concern with context",
    "Process/organizational issue with context"
  ],

  "next_actions": [
    "[Who] needs to [what] by [when]",
    "Outstanding technical question to resolve",
    "Commercial next step (pricing, approval, contracting)"
  ],

  "risks": [
    "Deal risk: competition, budget constraints, timeline pressure",
    "Technical risk: complexity, integration challenges, resource constraints",
    "Relationship risk: champion changes, engagement declining, stakeholder turnover"
  ],

  "call_history": [
    {{"date": "YYYY-MM-DD", "type": "Discovery/Technical/Commercial/Check-in", "one_sentence": "What happened"}},
    {{"date": "YYYY-MM-DD", "type": "Discovery/Technical/Commercial/Check-in", "one_sentence": "What happened"}}
  ]
}}

RULES:
- Return ONLY valid JSON (no markdown code blocks, no extra text)
- Be ruthlessly concise - every word must earn its place
- Use specific names, numbers, dates from summaries
- For quick_context: 4-5 bullets max, 1 sentence each
- For blocking_progress: 2-3 bullets, or ["None identified"] if none
- For next_actions: 3-4 bullets with owners and timing
- For risks: 2-3 bullets, or ["None identified"] if none
- For call_history: Sort NEWEST first (reverse chronological), extract from "=== CALL SUMMARY:" headers
- If something is unknown/unclear, use "Not yet discussed" rather than speculate
- Keep total output under 800 words
"""

    # Get model from environment or use default
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")

    response = client.messages.create(
        model=model,
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}]
    )

    response_text = response.content[0].text.strip()

    # Remove markdown code blocks if Claude added them
    if response_text.startswith("```"):
        start_idx = response_text.find("{")
        end_idx = response_text.rfind("}") + 1
        response_text = response_text[start_idx:end_idx]

    try:
        intelligence = json.loads(response_text)
        activity.logger.info(f"Intelligence synthesized: {len(intelligence.get('quick_context', []))} context items, {len(intelligence.get('call_history', []))} calls in history\n")
        return intelligence
    except json.JSONDecodeError as e:
        activity.logger.error(f"JSON parsing failed: {e}")
        activity.logger.error(f"Response preview: {response_text[:500]}...")
        raise Exception(f"Failed to parse Claude's JSON response: {e}")


@activity.defn
async def write_intelligence_to_doc(doc_url: str, intelligence: dict, account_name: str) -> None:
    """
    Activity 5: Write intelligence to TOP of summaries doc (idempotent) with rich text formatting.

    Re-reads doc to find intelligence markers for safe replacement.
    Replaces existing intelligence section if found, otherwise prepends.
    Uses Google Docs API formatting for bold headers, proper bullets, etc.

    Args:
        doc_url: Google Doc URL
        intelligence: Intelligence dict from Claude
        account_name: Account name
    """
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

    if not credentials_path:
        raise ValueError("GOOGLE_APPLICATION_CREDENTIALS must be set")

    activity.logger.info("Writing formatted intelligence to doc\n")

    # Extract doc ID
    doc_id = doc_url.split("/d/")[1].split("/")[0]

    # Authenticate
    credentials = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=["https://www.googleapis.com/auth/documents"]
    )
    service = build("docs", "v1", credentials=credentials)

    # Read existing doc to check for intelligence section
    doc = service.documents().get(documentId=doc_id).execute()

    # Look for existing intelligence section markers
    doc_content = doc.get("body", {}).get("content", [])
    start_marker = "ACCOUNT INTELLIGENCE"
    end_marker = "END ACCOUNT INTELLIGENCE"

    intelligence_start_index = None
    intelligence_end_index = None

    for element in doc_content:
        if "paragraph" in element:
            paragraph = element["paragraph"]
            for elem in paragraph.get("elements", []):
                if "textRun" in elem:
                    text = elem["textRun"]["content"]
                    if start_marker in text and intelligence_start_index is None:
                        intelligence_start_index = element.get("startIndex")
                    elif end_marker in text and intelligence_start_index is not None:
                        intelligence_end_index = element.get("endIndex")
                        break

    # Always insert at beginning (index 1) for simplicity
    # If old intelligence exists, we'll delete it AFTER inserting new content
    insert_index = 1
    old_intelligence_exists = (intelligence_start_index is not None and intelligence_end_index is not None)

    if old_intelligence_exists:
        activity.logger.info(f"Found existing intelligence at indices {intelligence_start_index}-{intelligence_end_index}, will replace\n")
    else:
        activity.logger.info("No existing intelligence found, prepending to beginning\n")

    # Build formatted content structure
    # We'll track text positions to apply formatting after insertion
    requests = []

    # Step 2: Build plain text content (we'll add formatting after)
    content_parts = []
    formatting_ranges = []  # Track what needs formatting: (start_offset, end_offset, style_type)

    current_offset = 0

    # Title separator line
    separator_line = "=" * 60 + "\n"
    content_parts.append(separator_line)
    current_offset += len(separator_line)

    # Title: ACCOUNT INTELLIGENCE (will be bold + larger)
    title = "ACCOUNT INTELLIGENCE\n"
    title_start = current_offset
    content_parts.append(title)
    current_offset += len(title)
    formatting_ranges.append((title_start, current_offset - 1, "title"))  # -1 to exclude newline

    # Bottom separator
    content_parts.append(separator_line)
    current_offset += len(separator_line)

    # Metadata fields (bold labels)
    account_line = f"Account: {intelligence.get('account', account_name)}\n"
    account_label_end = current_offset + len("Account:")
    content_parts.append(account_line)
    formatting_ranges.append((current_offset, account_label_end, "bold"))
    current_offset += len(account_line)

    updated_line = f"Last Updated: {intelligence.get('last_updated', datetime.now().strftime('%Y-%m-%d'))}\n"
    updated_label_end = current_offset + len("Last Updated:")
    content_parts.append(updated_line)
    formatting_ranges.append((current_offset, updated_label_end, "bold"))
    current_offset += len(updated_line)

    calls_line = f"Total Calls: {intelligence.get('total_calls', len(intelligence.get('call_history', [])))}\n"
    calls_label_end = current_offset + len("Total Calls:")
    content_parts.append(calls_line)
    formatting_ranges.append((current_offset, calls_label_end, "bold"))
    current_offset += len(calls_line)

    content_parts.append("\n")
    current_offset += 1

    # Divider
    divider = "-" * 60 + "\n\n"
    content_parts.append(divider)
    current_offset += len(divider)

    # Section: QUICK CONTEXT
    section_header = "QUICK CONTEXT\n\n"
    section_start = current_offset
    content_parts.append(section_header)
    current_offset += len(section_header)
    formatting_ranges.append((section_start, section_start + len("QUICK CONTEXT"), "section_header"))

    bullet_ranges = []  # Track bullet list ranges for formatting
    for item in intelligence.get("quick_context", []):
        bullet_line = f"{item}\n"
        bullet_start = current_offset
        content_parts.append(bullet_line)
        current_offset += len(bullet_line)
        bullet_ranges.append((bullet_start, current_offset - 1))  # Exclude newline from bullet range
    content_parts.append("\n")
    current_offset += 1

    # Section: BLOCKING PROGRESS
    section_header = "BLOCKING PROGRESS\n\n"
    section_start = current_offset
    content_parts.append(section_header)
    current_offset += len(section_header)
    formatting_ranges.append((section_start, section_start + len("BLOCKING PROGRESS"), "section_header"))

    blocking = intelligence.get("blocking_progress", [])
    if not blocking or (len(blocking) == 1 and "None identified" in blocking[0]):
        bullet_line = "None identified\n"
        bullet_start = current_offset
        content_parts.append(bullet_line)
        current_offset += len(bullet_line)
        bullet_ranges.append((bullet_start, current_offset - 1))
    else:
        for item in blocking:
            bullet_line = f"{item}\n"
            bullet_start = current_offset
            content_parts.append(bullet_line)
            current_offset += len(bullet_line)
            bullet_ranges.append((bullet_start, current_offset - 1))
    content_parts.append("\n")
    current_offset += 1

    # Section: NEXT ACTIONS
    section_header = "NEXT ACTIONS\n\n"
    section_start = current_offset
    content_parts.append(section_header)
    current_offset += len(section_header)
    formatting_ranges.append((section_start, section_start + len("NEXT ACTIONS"), "section_header"))

    for item in intelligence.get("next_actions", []):
        bullet_line = f"{item}\n"
        bullet_start = current_offset
        content_parts.append(bullet_line)
        current_offset += len(bullet_line)
        bullet_ranges.append((bullet_start, current_offset - 1))
    content_parts.append("\n")
    current_offset += 1

    # Section: RISKS
    section_header = "RISKS\n\n"
    section_start = current_offset
    content_parts.append(section_header)
    current_offset += len(section_header)
    formatting_ranges.append((section_start, section_start + len("RISKS"), "section_header"))

    risks = intelligence.get("risks", [])
    if not risks or (len(risks) == 1 and "None identified" in risks[0]):
        bullet_line = "None identified\n"
        bullet_start = current_offset
        content_parts.append(bullet_line)
        current_offset += len(bullet_line)
        bullet_ranges.append((bullet_start, current_offset - 1))
    else:
        for item in risks:
            bullet_line = f"{item}\n"
            bullet_start = current_offset
            content_parts.append(bullet_line)
            current_offset += len(bullet_line)
            bullet_ranges.append((bullet_start, current_offset - 1))
    content_parts.append("\n")
    current_offset += 1

    # Divider
    content_parts.append(divider)
    current_offset += len(divider)

    # Section: CALL HISTORY
    section_header = "CALL HISTORY (newest first)\n\n"
    section_start = current_offset
    content_parts.append(section_header)
    current_offset += len(section_header)
    formatting_ranges.append((section_start, section_start + len("CALL HISTORY (newest first)"), "section_header"))

    for call in intelligence.get("call_history", []):
        call_type = call.get("type", "Unknown")
        date = call.get("date", "N/A")
        sentence = call.get("one_sentence", "N/A")
        bullet_line = f"{date} - {call_type}: {sentence}\n"
        bullet_start = current_offset
        content_parts.append(bullet_line)
        current_offset += len(bullet_line)
        bullet_ranges.append((bullet_start, current_offset - 1))
    content_parts.append("\n")
    current_offset += 1

    # End markers
    content_parts.append(separator_line)
    current_offset += len(separator_line)

    end_title = "END ACCOUNT INTELLIGENCE\n"
    content_parts.append(end_title)
    current_offset += len(end_title)

    content_parts.append(separator_line)
    current_offset += len(separator_line)
    content_parts.append("\n\n")
    current_offset += 2

    # Combine all content
    full_text = "".join(content_parts)

    # Step 3: Insert plain text
    requests.append({
        "insertText": {
            "location": {"index": insert_index},
            "text": full_text
        }
    })

    # Step 4: Execute text insertion ONLY in first batchUpdate
    service.documents().batchUpdate(
        documentId=doc_id,
        body={"requests": requests}
    ).execute()

    activity.logger.info(f"Inserted intelligence text ({len(full_text)} characters)\n")

    # Step 5: Apply text styling in second batchUpdate (after text exists)
    styling_requests = []
    for start_offset, end_offset, style_type in formatting_ranges:
        start_idx = insert_index + start_offset
        end_idx = insert_index + end_offset

        if style_type == "title":
            # Large bold title
            styling_requests.append({
                "updateTextStyle": {
                    "range": {
                        "startIndex": start_idx,
                        "endIndex": end_idx
                    },
                    "textStyle": {
                        "bold": True,
                        "fontSize": {
                            "magnitude": 16,
                            "unit": "PT"
                        }
                    },
                    "fields": "bold,fontSize"
                }
            })
        elif style_type == "section_header":
            # Bold section headers
            styling_requests.append({
                "updateTextStyle": {
                    "range": {
                        "startIndex": start_idx,
                        "endIndex": end_idx
                    },
                    "textStyle": {
                        "bold": True,
                        "fontSize": {
                            "magnitude": 12,
                            "unit": "PT"
                        }
                    },
                    "fields": "bold,fontSize"
                }
            })
        elif style_type == "bold":
            # Bold labels
            styling_requests.append({
                "updateTextStyle": {
                    "range": {
                        "startIndex": start_idx,
                        "endIndex": end_idx
                    },
                    "textStyle": {
                        "bold": True
                    },
                    "fields": "bold"
                }
            })

    if styling_requests:
        service.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": styling_requests}
        ).execute()
        activity.logger.info(f"Applied text styling ({len(styling_requests)} operations)\n")

    # Step 6: Apply bullet formatting in third batchUpdate
    bullet_requests = []
    for bullet_start, bullet_end in bullet_ranges:
        start_idx = insert_index + bullet_start
        end_idx = insert_index + bullet_end
        bullet_requests.append({
            "createParagraphBullets": {
                "range": {
                    "startIndex": start_idx,
                    "endIndex": end_idx
                },
                "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"
            }
        })

    if bullet_requests:
        service.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": bullet_requests}
        ).execute()
        activity.logger.info(f"Applied bullet formatting ({len(bullet_requests)} operations)\n")

    # Step 7: Delete old intelligence section if it existed
    # We do this AFTER inserting new content to avoid index calculation issues
    if old_intelligence_exists:
        # After inserting new content at index 1, the old section has shifted by len(full_text)
        new_start = intelligence_start_index + len(full_text)
        new_end = intelligence_end_index + len(full_text)

        activity.logger.info(f"Deleting old intelligence section at indices {new_start}-{new_end}\n")

        service.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": [{
                "deleteContentRange": {
                    "range": {
                        "startIndex": new_start,
                        "endIndex": new_end
                    }
                }
            }]}
        ).execute()

    # Step 8: Parse markdown formatting in entire document
    # Find all **text** patterns and markdown bullets, convert to Google Docs formatting
    activity.logger.info("Parsing markdown formatting for call summaries\n")

    # Re-read document to get current state
    doc = service.documents().get(documentId=doc_id).execute()
    doc_content = doc.get("body", {}).get("content", [])

    # Collect all markdown patterns and format/delete operations
    # We'll process deletions in reverse order to avoid index shifting issues
    bold_requests = []
    delete_requests = []
    bullet_requests = []

    current_index = 1
    for element in doc_content:
        if "paragraph" in element:
            paragraph_start = element.get("startIndex", current_index)
            paragraph_end = element.get("endIndex", current_index)

            for elem in element["paragraph"].get("elements", []):
                if "textRun" in elem:
                    text = elem["textRun"]["content"]
                    start_idx = current_index

                    # Search for **text** patterns (markdown bold)
                    for match in re.finditer(r'\*\*(.+?)\*\*', text):
                        # Calculate positions
                        match_start = start_idx + match.start()
                        match_end = start_idx + match.end()
                        content_start = match_start + 2  # Skip opening **
                        content_end = match_end - 2      # Skip closing **

                        # Bold the content (excluding the ** markers)
                        bold_requests.append({
                            "updateTextStyle": {
                                "range": {"startIndex": content_start, "endIndex": content_end},
                                "textStyle": {"bold": True},
                                "fields": "bold"
                            }
                        })

                        # Queue deletions (will be processed in reverse order)
                        delete_requests.append(("closing", match_end - 2, match_end))  # Delete closing **
                        delete_requests.append(("opening", match_start, match_start + 2))  # Delete opening **

                    # Also bold === CALL SUMMARY: headers
                    if text.startswith("=== CALL SUMMARY:"):
                        bold_requests.append({
                            "updateTextStyle": {
                                "range": {"startIndex": start_idx, "endIndex": start_idx + len(text.rstrip())},
                                "textStyle": {"bold": True},
                                "fields": "bold"
                            }
                        })

                    # Check for markdown bullets (lines starting with "- ")
                    if text.startswith("- "):
                        # Convert this line to a Google Docs bullet
                        bullet_requests.append({
                            "createParagraphBullets": {
                                "range": {
                                    "startIndex": paragraph_start,
                                    "endIndex": paragraph_end
                                },
                                "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"
                            }
                        })

                        # Delete the "- " marker
                        delete_requests.append(("bullet_marker", start_idx, start_idx + 2))

                    current_index += len(text)

    # Apply bold formatting first
    if bold_requests:
        service.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": bold_requests}
        ).execute()
        activity.logger.info(f"Applied bold formatting to {len(bold_requests)} markdown patterns\n")

    # Apply bullet formatting second
    if bullet_requests:
        service.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": bullet_requests}
        ).execute()
        activity.logger.info(f"Applied bullet formatting to {len(bullet_requests)} lines\n")

    # Delete markdown markers in reverse order to avoid index shifting
    if delete_requests:
        # Sort by start index in reverse order (highest first)
        delete_requests.sort(key=lambda x: x[1], reverse=True)

        delete_ops = []
        for _, start, end in delete_requests:
            delete_ops.append({
                "deleteContentRange": {
                    "range": {"startIndex": start, "endIndex": end}
                }
            })

        service.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": delete_ops}
        ).execute()
        activity.logger.info(f"Removed {len(delete_ops)} markdown markers (**, -)\n")
