#!/usr/bin/env python3
"""
Test script for Activity 3: summarize_and_append_to_doc()

Tests Claude API call summarization for a single call.

Usage:
    uv run python scripts/test_summarize_call.py <call-id>
"""

import sys
import os
from datetime import datetime
from dotenv import load_dotenv
import requests
from anthropic import Anthropic

# Load environment variables
load_dotenv()

# Target summary length
TARGET_SUMMARY_LENGTH = 300  # words


def fetch_gong_call(call_id: str) -> dict:
    """
    Fetch call metadata and transcript from Gong API.

    Returns:
        dict with keys: call_id, title, call_date, duration, participants, transcript_text
    """
    api_key = os.getenv("GONG_API_KEY")
    api_secret = os.getenv("GONG_API_SECRET")

    if not api_key or not api_secret:
        raise ValueError("GONG_API_KEY and GONG_API_SECRET must be set in .env")

    auth = (api_key, api_secret)
    headers = {"Content-Type": "application/json"}

    print(f"\n{'='*60}")
    print(f"[1/3] Fetching call data from Gong")
    print(f"{'='*60}\n")

    # Fetch call metadata
    print(f"      Fetching metadata for call {call_id}...")
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
    print(f"      Fetching transcript...")
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
        # Unix timestamp
        call_datetime = datetime.fromtimestamp(int(call_date_raw))
    elif isinstance(call_date_raw, str):
        # ISO format
        call_datetime = datetime.fromisoformat(call_date_raw.replace("Z", "+00:00"))
    else:
        # Assume it's already a timestamp
        call_datetime = datetime.fromtimestamp(call_date_raw)

    call_date_str = call_datetime.strftime("%Y-%m-%d")

    # Extract participant names
    parties = call_data.get("parties", [])
    participant_names = [
        p.get("name", "Unknown")
        for p in parties
        if p.get("emailAddress") and not p.get("emailAddress", "").endswith("@temporal.io")
    ]

    print(f"      ✓ Fetched call: {metadata.get('title', 'Untitled')}")
    print(f"      ✓ Date: {call_date_str}")
    print(f"      ✓ Duration: {metadata.get('duration', 0)} seconds")
    print(f"      ✓ External participants: {len(participant_names)}")

    return {
        "call_id": call_id,
        "title": metadata.get("title", "Untitled"),
        "call_date": call_date_str,
        "duration": metadata.get("duration", 0),
        "participants": participant_names,
        "transcript_text": transcript_text
    }


def summarize_with_claude(call_data: dict) -> str:
    """
    Send call transcript to Claude for summarization.

    Role: FACTUAL REPORTER - captures what happened on THIS call only.
    The synthesis step will handle cross-call analysis and intelligence.

    Returns:
        Formatted summary string (~300 words)
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY must be set in .env")

    client = Anthropic(api_key=api_key)

    print(f"\n{'='*60}")
    print(f"[2/3] Summarizing with Claude")
    print(f"{'='*60}\n")

    # Simplified prompt: REPORTER role, not ANALYST role
    prompt = f"""You are a FACTUAL REPORTER capturing what happened on a Temporal sales call. Your role is to document ONLY what was discussed on THIS specific call.

CONTEXT:
- Company: Temporal Technologies
- Product: Temporal Cloud (managed SaaS offering)
- Key distinction: Many prospects use Temporal OSS (open-source, self-hosted) and are evaluating Temporal Cloud (paid, managed service)
- When summarizing, clearly distinguish between OSS usage vs Cloud evaluation/migration

DO NOT:
- Speculate about overall deal health or account status
- Analyze patterns across previous calls
- Make recommendations or predictions
- Add business intelligence commentary

DO:
- Capture concrete facts from THIS call
- Adapt your summary format based on the call type (Technical/Discovery/Commercial/Check-in)
- Include specific details: names, numbers, dates, requirements, concerns
- Note what was decided and what's next
- Clarify if discussing OSS usage, Cloud migration, or net-new Cloud adoption

First, infer the call type from the content, then create a ~{TARGET_SUMMARY_LENGTH}-word summary with appropriate sections:

For TECHNICAL calls:
- Purpose: Why this call happened
- Technical Discussion: Architecture, integrations, requirements covered
- Blockers/Concerns: Technical issues or open questions raised
- Decisions: What was decided
- Action Items: Specific next steps (who, what, when)

For DISCOVERY calls:
- Purpose: Why this call happened
- Current State: How they operate today (from THIS call)
- Desired State: What outcomes they want (from THIS call)
- Decision Process: Timeline, budget, stakeholders mentioned
- Action Items: Specific next steps (who, what, when)

For COMMERCIAL calls:
- Purpose: Why this call happened
- Commercial Discussion: Pricing, budget, timeline, terms discussed
- Stakeholder Sentiment: Customer tone and engagement on THIS call
- Concerns: Any objections or concerns raised
- Action Items: Specific next steps (who, what, when)

For CHECK-IN/SYNC calls:
- Purpose: Why this call happened
- Status Updates: Progress since last interaction
- Issues Discussed: Problems or blockers raised
- Attendee Engagement: Who attended and participation level
- Action Items: Specific next steps (who, what, when)

CALL TITLE: {call_data['title']}
CALL DATE: {call_data['call_date']}

CALL TRANSCRIPT:
{call_data['transcript_text']}
"""

    print(f"      Sending transcript to Claude Sonnet 4.5...")
    print(f"      Target length: ~{TARGET_SUMMARY_LENGTH} words")

    response = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=1000,  # ~750 words max output
        messages=[{"role": "user", "content": prompt}]
    )

    summary = response.content[0].text.strip()
    word_count = len(summary.split())

    print(f"      ✓ Summary generated: {word_count} words")

    return summary


def format_call_summary(call_data: dict, summary: str) -> str:
    """
    Format the summary with call metadata.

    Returns:
        Formatted string ready to append to Google Doc
    """
    participants_str = ", ".join(call_data["participants"]) if call_data["participants"] else "No external participants"

    formatted = f"""=== CALL SUMMARY: {call_data['call_date']} - {call_data['title']} ===
Call ID: {call_data['call_id']}
Participants: {participants_str}
Duration: {call_data['duration'] // 60} minutes

{summary}

===
"""
    return formatted


def main():
    if len(sys.argv) < 2:
        print("Usage: uv run python scripts/test_summarize_call.py <call-id>")
        print("Example: uv run python scripts/test_summarize_call.py 7782342274025937895")
        sys.exit(1)

    call_id = sys.argv[1]

    try:
        # Step 1: Fetch call from Gong
        call_data = fetch_gong_call(call_id)

        # Step 2: Summarize with Claude
        summary = summarize_with_claude(call_data)

        # Step 3: Format for output
        print(f"\n{'='*60}")
        print(f"[3/3] Formatted Summary")
        print(f"{'='*60}\n")

        formatted_summary = format_call_summary(call_data, summary)
        print(formatted_summary)

        print(f"\n{'='*60}")
        print("✅ TEST PASSED")
        print(f"{'='*60}")
        print(f"Summary length: {len(summary.split())} words (target: {TARGET_SUMMARY_LENGTH})")
        print(f"\nThis summary is ready to append to Google Docs.")

    except Exception as e:
        print(f"\n{'='*60}")
        print("❌ TEST FAILED")
        print(f"{'='*60}")
        print(f"Error: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
