---
name: gong-api
description: Gong API patterns for fetching call transcripts, searching calls, and filtering by participants. Use when working with Gong API calls/extensive endpoint, calls/transcript endpoint, pagination with cursor, time-windowing for efficient searches, or primaryUserIds filtering. Includes patterns for handling Gong API rate limits and pagination.
allowed-tools: Read, Bash
---

# Gong API Reference

## Authentication
- **Method**: HTTP Basic Auth
- **Credentials**: `(GONG_API_KEY, GONG_API_SECRET)`
- **Usage**: `requests.post(url, auth=(api_key, api_secret), headers={"Content-Type": "application/json"})`

## Key Endpoints

### 1. `/v2/calls/extensive` - Get Call Metadata
**Purpose**: Fetch call metadata including participants, title, scheduled time, content, interaction stats

**Request Body Schema**:
```python
{
    "cursor": "string",  # Optional: For pagination
    "filter": {
        "fromDateTime": "2018-02-17T02:30:00-08:00",  # Optional
        "toDateTime": "2018-02-19T02:30:00-08:00",    # Optional
        "workspaceId": "string",                       # Optional
        "callIds": ["7782342274025937895"],           # Filter by specific call IDs
        "primaryUserIds": ["string"]                   # Optional
    },
    "contentSelector": {
        "context": "None",  # Options: "None", "Extended"
        "contextTiming": ["Now"],  # Options: "Now", "Past", "Future"
        "exposedFields": {
            "parties": true,  # Include participant information
            "content": {
                "structure": false,      # Meeting structure breakdown
                "topics": false,         # Auto-detected topics
                "trackers": false,       # Tracker matches
                "trackerOccurrences": false,
                "pointsOfInterest": false,
                "brief": true,           # AI-generated call summary
                "outline": true,         # Call outline
                "highlights": true,      # Key highlights
                "callOutcome": true,     # Call outcome/disposition
                "keyPoints": true        # Key takeaways
            },
            "interaction": {
                "speakers": true,               # Speaker talk time stats
                "video": true,                  # Video participation
                "personInteractionStats": true, # Interactivity metrics
                "questions": true               # Question counts
            },
            "collaboration": {
                "publicComments": true   # Comments on the call
            },
            "media": true  # Audio/video URLs
        }
    }
}
```

**Minimal Request (Our Usage)**:
```python
response = requests.post(
    "https://api.gong.io/v2/calls/extensive",
    auth=(api_key, api_secret),
    headers={"Content-Type": "application/json"},
    json={
        "filter": {"callIds": [call_id]},
        "contentSelector": {"exposedFields": {"parties": True}}
    }
)
call_data = response.json()["calls"][0]
```

**Complete Response Structure**:
```python
{
    "requestId": "4al018gzaztcr8nbukw",
    "records": {
        "totalRecords": 263,
        "currentPageSize": 100,
        "currentPageNumber": 0,
        "cursor": "eyJhbGciOiJIUzI1NiJ9..."  # For pagination
    },
    "calls": [{
        "metaData": {
            "id": "7782342274025937895",
            "url": "https://app.gong.io/call?id=7782342274025937895",
            "title": "Example call",
            "scheduled": 1518863400,  # Unix timestamp OR ISO string
            "started": 1518863400,
            "duration": 460,  # seconds
            "primaryUserId": "234599484848423",
            "direction": "Inbound",  # or "Outbound"
            "system": "Outreach",  # Integration source
            "scope": "Internal",  # or "External", "Conference"
            "media": "Video",  # or "Audio", "AudioAndVideo"
            "language": "eng",
            "workspaceId": "623457276584334",
            "sdrDisposition": "Got the gatekeeper",
            "clientUniqueId": "7JEHFRGXDDZFEW2FC4U",
            "customData": "Conference Call",
            "purpose": "Demo Call",
            "meetingUrl": "https://zoom.us/j/123",
            "isPrivate": false,
            "calendarEventId": "abcde@google.com"
        },
        "context": [  # CRM context if available
            {
                "system": "Salesforce",
                "objects": [{
                    "objectType": "Opportunity",
                    "objectId": "0013601230sV7grAAC",
                    "fields": [{"name": "name", "value": "Gong Inc."}],
                    "timing": "Now"
                }]
            }
        ],
        "parties": [
            {
                "id": "56825452554556",
                "emailAddress": "test@test.com",
                "name": "Test User",
                "title": "Enterprise Account Executive",
                "userId": "234599484848423",
                "speakerId": "6432345678555530067",
                "context": [{...}],  # CRM context for this person
                "affiliation": "Internal",  # or "External"
                "phoneNumber": "+1 123-567-8989",
                "methods": ["Invitee"]  # How they joined
            }
        ],
        "content": {  # Only if requested in contentSelector
            "structure": [{
                "name": "Meeting Setup",
                "duration": 67
            }],
            "trackers": [{...}],
            "topics": [{...}],
            "brief": "AI-generated summary of the call",
            "outline": [{...}],
            "highlights": [{...}],
            "callOutcome": {
                "id": "MEETING_BOOKED",
                "category": "Answered",
                "name": "Meeting booked"
            },
            "keyPoints": [{"text": "string"}]
        },
        "interaction": {  # Only if requested
            "speakers": [{
                "id": "56825452554556",
                "userId": "234599484848423",
                "talkTime": 145  # seconds
            }],
            "interactionStats": [{"name": "Interactivity", "value": 56}],
            "video": [{"name": "Browser", "duration": 218}],
            "questions": {
                "companyCount": 0,
                "nonCompanyCount": 0
            }
        },
        "collaboration": {  # Only if requested
            "publicComments": [{...}]
        },
        "media": {  # Only if requested
            "audioUrl": "http://example.com",
            "videoUrl": "http://example.com"
        }
    }]
}
```

**Key Fields for This Project**:
- `metaData.title`: Call title
- `metaData.scheduled`: Call date/time (Unix timestamp as integer OR ISO 8601 string)
- `metaData.duration`: Call duration in seconds
- `parties`: Array of participants
  - `emailAddress`: Extract customer company from domain
  - `name`: Participant name
  - `title`: Job title
  - `affiliation`: "Internal" (your team) or "External" (customer)
  - `speakerId`: Links to transcript speaker IDs

### 2. `/v2/calls/transcript` - Get Call Transcript
**Purpose**: Fetch full transcript with speaker segments

**Request**:
```python
response = requests.post(
    "https://api.gong.io/v2/calls/transcript",
    auth=(api_key, api_secret),
    headers={"Content-Type": "application/json"},
    json={"filter": {"callIds": [call_id]}}
)
call_transcript = response.json()["callTranscripts"][0]
```

**Response Structure**:
```python
{
    "callTranscripts": [{
        "transcript": [
            {
                "speakerId": "0",
                "topic": "Introduction",  # Optional, can be empty
                "sentences": [
                    {"start": 0, "end": 1500, "text": "Hi, thanks for joining."},
                    {"start": 1500, "end": 3000, "text": "Let's talk about your use case."}
                ]
            }
        ]
    }]
}
```

**Parsing Logic**:
```python
transcript_lines = []
for entry in call_transcript["transcript"]:
    speaker_id = entry["speakerId"]
    topic = entry.get("topic", "")

    # Combine all sentences for this speaker segment
    sentences = entry.get("sentences", [])
    text = " ".join(s["text"] for s in sentences)

    # Format: Speaker ID (Topic): Text
    if topic:
        transcript_lines.append(f"Speaker {speaker_id} ({topic}): {text}")
    else:
        transcript_lines.append(f"Speaker {speaker_id}: {text}")

transcript_text = "\n".join(transcript_lines)
```

## Common Patterns

### Extracting Account Name from Email Domain
```python
# Find first external participant (non-Temporal email)
for party in parties:
    email = party.get("emailAddress", "")
    if email and not email.endswith("@temporal.io"):
        domain = email.split("@")[1]  # "example.com"

        # Strip common TLDs
        for tld in [".io", ".com", ".net", ".org", ".co"]:
            if domain.endswith(tld):
                domain = domain[:-len(tld)]
                break

        # Replace hyphens with spaces and capitalize
        account_name = domain.replace("-", " ").title()  # "Example"
        break
```

### Date Handling
Gong returns dates in two formats:
1. **ISO 8601**: `"2024-12-18T15:30:00Z"`
2. **Unix timestamp**: `"1518863400"` (string of digits)

**Parsing**:
```python
from datetime import datetime

if call_date.isdigit():
    call_datetime = datetime.fromtimestamp(int(call_date))
else:
    call_datetime = datetime.fromisoformat(call_date.replace("Z", "+00:00"))

call_date_str = call_datetime.strftime("%Y-%m-%d")  # "2024-12-18"
```

## Important Notes
- Always use **POST** requests with JSON body filter, never GET with query params
- Call IDs are passed as array: `{"filter": {"callIds": [call_id]}}`
- Response is always array even for single call: `response["calls"][0]` or `response["callTranscripts"][0]`
- Speaker IDs are strings: `"0"`, `"1"`, `"2"`, etc.
- Topic field can be empty string or missing
- Participants have `affiliation`: `"Internal"` (your team) or `"External"` (customer)

## Error Handling
```python
response.raise_for_status()  # Raises HTTPError for 4xx/5xx responses

# Check if call exists
if not response.json().get("calls") or not response.json()["calls"]:
    raise ValueError(f"Call {call_id} not found")
```

## Use Cases in This Project
1. **Fetch call details**: Use `/v2/calls/extensive` to get title, date, participants
2. **Get transcript**: Use `/v2/calls/transcript` to get full conversation
3. **Extract account**: Parse customer email domain to get company name for Google Drive search
4. **Format transcript**: Combine speaker segments with topics for Claude processing
