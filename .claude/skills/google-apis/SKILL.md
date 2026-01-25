---
name: google-drive-docs-apis
description: Google Drive and Docs API integration for Gong account intelligence system. Use when searching Shared Drives, finding documents by folder, reading document structure, inserting text with batchUpdate, managing document indices, or working with account intelligence documents. Includes critical patterns for index ordering and dateElement access.
allowed-tools: Read, Bash
---

# Google Drive & Docs APIs Reference

## Authentication (Service Account)
```python
from google.oauth2 import service_account
from googleapiclient.discovery import build

credentials = service_account.Credentials.from_service_account_file(
    "/path/to/service-account.json",
    scopes=[
        "https://www.googleapis.com/auth/drive",      # For Drive API
        "https://www.googleapis.com/auth/documents"   # For Docs API
    ]
)

drive_service = build("drive", "v3", credentials=credentials)
docs_service = build("docs", "v1", credentials=credentials)
```

## Google Drive API (v3)

### Searching for Folders
**CRITICAL**: Must include `corpora='allDrives'` parameters to search Shared Drives

```python
query = "name contains 'Acme' and mimeType='application/vnd.google-apps.folder'"
results = drive_service.files().list(
    q=query,
    fields="files(id, name)",
    pageSize=50,
    corpora='allDrives',              # REQUIRED for Shared Drives
    supportsAllDrives=True,           # REQUIRED
    includeItemsFromAllDrives=True    # REQUIRED
).execute()

folders = results.get("files", [])
# Returns: [{"id": "abc123", "name": "Acme Corp"}, ...]
```

### Searching for Documents in a Folder
```python
folder_id = "abc123"
doc_query = f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.document'"
doc_results = drive_service.files().list(
    q=doc_query,
    fields="files(id, name)",
    pageSize=10,
    corpora='allDrives',
    supportsAllDrives=True,
    includeItemsFromAllDrives=True
).execute()

docs = doc_results.get("files", [])
```

### Query Syntax
- **Contains**: `name contains 'text'` (case-insensitive substring match)
- **Exact**: `name = 'Exact Name'`
- **MIME types**:
  - Folder: `mimeType='application/vnd.google-apps.folder'`
  - Document: `mimeType='application/vnd.google-apps.document'`
- **Parent folder**: `'folder_id' in parents`
- **Combine**: Use `and` operator

## Google Docs API (v1)

### Reading a Document
```python
doc_id = "1a2b3c..."  # Extract from URL
doc = docs_service.documents().get(documentId=doc_id).execute()

# Extract full text
full_text = ""
for element in doc.get("body", {}).get("content", []):
    if "paragraph" in element:
        for text_run in element["paragraph"].get("elements", []):
            if "textRun" in text_run:
                full_text += text_run["textRun"]["content"]
```

### Document Structure
```python
{
    "body": {
        "content": [
            {
                "startIndex": 1,
                "endIndex": 100,
                "paragraph": {
                    "elements": [
                        {
                            "startIndex": 1,
                            "endIndex": 50,
                            "textRun": {
                                "content": "Hello world\n",
                                "textStyle": {...}
                            }
                        },
                        {
                            "startIndex": 50,
                            "endIndex": 100,
                            "dateElement": {
                                "dateElementProperties": {
                                    "timestamp": "2024-12-18T15:30:00Z"
                                }
                            }
                        }
                    ],
                    "paragraphStyle": {
                        "namedStyleType": "HEADING_2"  # or "NORMAL_TEXT"
                    }
                }
            }
        ]
    }
}
```

### Meeting Notes Building Block Structure
Google Docs `@meeting notes` creates HEADING_2 with:
- `dateElement`: Contains meeting date
- `richLink`: Contains meeting title

**CRITICAL**: Timestamp is nested at `dateElement.dateElementProperties.timestamp`, NOT `dateElement.timestamp`

```python
for element in content_elements:
    para = element.get("paragraph", {})

    # Check if HEADING_2
    if para.get("paragraphStyle", {}).get("namedStyleType") == "HEADING_2":
        for el in para.get("elements", []):
            if "dateElement" in el:
                # CORRECT way to access timestamp
                timestamp = el["dateElement"].get("dateElementProperties", {}).get("timestamp", "")

                # WRONG way (doesn't work)
                # timestamp = el["dateElement"].get("timestamp", "")
```

### Finding Insertion Point by Date
```python
from datetime import datetime

# Parse call date
call_datetime = datetime.fromisoformat(call_date.replace("Z", "+00:00"))
call_date_str = call_datetime.strftime("%Y-%m-%d")  # "2024-12-18"

# Search for HEADING_2 with matching date
found_matching_heading = False
insert_index = None

for element in doc.get("body", {}).get("content", []):
    para = element.get("paragraph", {})

    # Look for HEADING_2 with matching date
    if not found_matching_heading and para.get("paragraphStyle", {}).get("namedStyleType") == "HEADING_2":
        for el in para.get("elements", []):
            if "dateElement" in el:
                timestamp = el["dateElement"].get("dateElementProperties", {}).get("timestamp", "")
                if timestamp:
                    block_date = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    block_date_str = block_date.strftime("%Y-%m-%d")

                    if call_date_str == block_date_str:
                        found_matching_heading = True
                        break

    # After finding heading, look for "Attendees:" paragraph
    elif found_matching_heading:
        for el in para.get("elements", []):
            if "textRun" in el:
                content = el["textRun"].get("content", "")
                if "Attendees:" in content:
                    insert_index = element.get("endIndex")
                    break
        if insert_index:
            break
```

### Batch Updates (batchUpdate API)
**CRITICAL INDEX ORDERING**: Operations execute sequentially, and each operation shifts subsequent indices.

**WRONG ORDER** (causes index shifting bug):
```python
requests_body = [
    # Step 1: Insert snapshot at beginning (shifts ALL indices)
    {"insertText": {"location": {"index": 1}, "text": snapshot}},

    # Step 2: Insert notes at index 2532 (but index is now shifted!)
    {"insertText": {"location": {"index": 2532}, "text": notes}}
]
```

**CORRECT ORDER** (insert notes first, then snapshot):
```python
requests_body = []

# STEP 1: Insert notes at original unshifted index
requests_body.append({
    "insertText": {
        "location": {"index": insert_index},  # Use original index
        "text": f"\n{call_notes}\n\n"
    }
})

# STEP 2: Then handle snapshot (won't affect notes placement)
if snapshot_exists:
    # Delete old snapshot
    requests_body.append({
        "deleteContentRange": {
            "range": {"startIndex": start_idx + 1, "endIndex": end_idx + 1}
        }
    })
    # Insert new snapshot at same location
    requests_body.append({
        "insertText": {
            "location": {"index": start_idx + 1},
            "text": snapshot
        }
    })
else:
    # Insert snapshot at beginning
    requests_body.append({
        "insertText": {
            "location": {"index": 1},
            "text": f"{snapshot}\n\n"
        }
    })

# Execute all requests in batch
docs_service.documents().batchUpdate(
    documentId=doc_id,
    body={"requests": requests_body}
).execute()
```

### Index System
- **Index 1**: Start of document (after title)
- **Index N**: Each character occupies one index
- **endIndex**: Position AFTER the last character of an element
- **Inserting at index X**: Text appears at position X, shifting everything after
- **Deleting range [start, end)**: Removes characters from start (inclusive) to end (exclusive)

### Common Operations

**Insert text at beginning**:
```python
{"insertText": {"location": {"index": 1}, "text": "New text\n\n"}}
```

**Insert text after element**:
```python
{"insertText": {"location": {"index": element["endIndex"]}, "text": "New text\n"}}
```

**Delete text range**:
```python
{"deleteContentRange": {"range": {"startIndex": 100, "endIndex": 200}}}
```

**Replace text** (delete then insert):
```python
[
    {"deleteContentRange": {"range": {"startIndex": start, "endIndex": end}}},
    {"insertText": {"location": {"index": start}, "text": "Replacement"}}
]
```

## Common Patterns in This Project

### Account Snapshot Management
```python
SNAPSHOT_MARKER_START = "=== ACCOUNT SNAPSHOT ==="
SNAPSHOT_MARKER_END = "=== END SNAPSHOT ==="

# Check if snapshot exists
if SNAPSHOT_MARKER_START in full_text and SNAPSHOT_MARKER_END in full_text:
    # Find indices
    start_idx = full_text.find(SNAPSHOT_MARKER_START)
    end_idx = full_text.find(SNAPSHOT_MARKER_END) + len(SNAPSHOT_MARKER_END)

    # Replace: delete old, insert new
    requests_body.append({
        "deleteContentRange": {
            "range": {"startIndex": start_idx + 1, "endIndex": end_idx + 1}
        }
    })
    requests_body.append({
        "insertText": {
            "location": {"index": start_idx + 1},
            "text": new_snapshot
        }
    })
```

### Document URL Format
- **Full URL**: `https://docs.google.com/document/d/{doc_id}/edit`
- **Extract doc_id**: `doc_url.split("/d/")[1].split("/")[0]`

## Permissions & Service Accounts
- Service account must be **added as member** of Shared Drive (not just individual file sharing)
- Grant "Commenter" or "Editor" access to Shared Drive
- Service account email: `{name}@{project-id}.iam.gserviceaccount.com`
- Without Shared Drive membership: Can write to specific docs (if shared), but **cannot list/search** folders

## Error Handling
```python
try:
    doc = docs_service.documents().get(documentId=doc_id).execute()
except HttpError as e:
    if e.resp.status == 404:
        # Document not found or no access
        pass
    elif e.resp.status == 403:
        # Permission denied
        pass
```

## Testing Tips
- Test Drive search with: `uv run python scripts/test_drive_search.py`
- Test Docs read/write with: `uv run python scripts/test_gdocs.py`
- Use `activity.logger.info()` to log indices and structure during development
- Print full document JSON to understand structure: `print(json.dumps(doc, indent=2))`
