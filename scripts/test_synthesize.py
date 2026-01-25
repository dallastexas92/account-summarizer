#!/usr/bin/env python3
"""
Test script for Activities 4 & 5: synthesize_intelligence() + write_intelligence_doc()

Reads call summaries doc, generates holistic account intelligence, and writes to intelligence doc.

Usage:
    uv run python scripts/test_synthesize.py <summaries-doc-url> <account-name>
"""

import sys
import os
import json
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from anthropic import Anthropic

# Load environment variables
load_dotenv()


def read_summaries_doc(doc_url: str) -> str:
    """
    Read full content from summaries doc.

    Args:
        doc_url: Google Doc URL

    Returns:
        Full document text content
    """
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not credentials_path:
        raise ValueError("GOOGLE_APPLICATION_CREDENTIALS must be set in .env")

    print(f"\n{'='*60}")
    print(f"[1/4] Reading summaries doc")
    print(f"{'='*60}\n")

    # Extract doc ID from URL
    doc_id = doc_url.split("/d/")[1].split("/")[0]
    print(f"      Doc ID: {doc_id}")

    # Authenticate
    credentials = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=["https://www.googleapis.com/auth/documents"]
    )
    service = build("docs", "v1", credentials=credentials)

    # Fetch document
    print(f"      Fetching document content...")
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

    # Count summaries (each has "=== CALL SUMMARY:" header)
    num_summaries = full_text.count("=== CALL SUMMARY:")

    print(f"      ✓ Read {len(full_text)} characters")
    print(f"      ✓ Found {num_summaries} call summaries")

    return full_text


def synthesize_with_claude(summaries_text: str, account_name: str) -> dict:
    """
    Send all call summaries to Claude for synthesis.

    Role: ANALYST - analyzes patterns across ALL calls to generate intelligence.
    This is different from the REPORTER role used for individual call summaries.

    Args:
        summaries_text: Full text from summaries doc
        account_name: Account name

    Returns:
        Dict with intelligence sections:
        - commercial_summary
        - technical_summary
        - pain_points (list)
        - next_steps (list)
        - risk_assessment
        - call_history (list of dicts)
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY must be set in .env")

    client = Anthropic(api_key=api_key)

    print(f"\n{'='*60}")
    print(f"[2/4] Synthesizing intelligence with Claude")
    print(f"{'='*60}\n")

    # Count call summaries for context
    num_calls = summaries_text.count("=== CALL SUMMARY:")

    # ANALYST role prompt - cross-call pattern analysis
    prompt = f"""You are an ANALYST generating holistic account intelligence by analyzing ALL calls for a customer.

CONTEXT:
- Company: Temporal Technologies
- Product: Temporal Cloud (managed SaaS offering)
- Account: {account_name}
- Total Calls: {num_calls}

YOUR ROLE:
- Analyze patterns across ALL calls (not individual call details)
- Identify trends, changes, and evolution over time
- Be HONEST about risks and challenges (don't sugarcoat)
- Provide actionable next steps for the SA/AE team

CALL SUMMARIES:
{summaries_text}

Generate account intelligence in the following JSON structure:

{{
  "commercial_summary": "2-3 paragraphs covering: deal stage, key stakeholders, budget/timeline, competitive situation, likelihood to close. Be specific about numbers and dates mentioned.",

  "technical_summary": "2-3 paragraphs covering: current architecture, Temporal usage (OSS vs Cloud), key requirements, integrations planned, technical blockers. Include specific technical details.",

  "pain_points": [
    "Specific pain point 1 with context",
    "Specific pain point 2 with context",
    "Specific pain point 3 with context"
  ],

  "next_steps": [
    "Specific action 1 (who should do what, when)",
    "Specific action 2 (who should do what, when)",
    "Specific action 3 (who should do what, when)"
  ],

  "risk_assessment": "2-3 paragraphs covering: commercial risks (budget, timeline, competition), technical risks (complexity, integrations, blockers), relationship risks (stakeholder changes, engagement level). Be candid about what could prevent deal from closing.",

  "call_history": [
    {{"date": "YYYY-MM-DD", "title": "Call title", "one_sentence": "One sentence summary"}},
    {{"date": "YYYY-MM-DD", "title": "Call title", "one_sentence": "One sentence summary"}}
  ]
}}

IMPORTANT:
- Return ONLY valid JSON (no markdown code blocks, no extra text)
- Be specific with names, numbers, dates, and technical details
- For call_history, extract date/title from each "=== CALL SUMMARY:" header
- Sort call_history chronologically (oldest to newest)
- If information is missing or unclear, say so explicitly rather than making assumptions
"""

    print(f"      Sending {len(summaries_text)} characters to Claude Sonnet 4.5...")
    print(f"      Analyzing {num_calls} call summaries...")

    response = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=4000,  # Synthesis can be longer than individual summaries
        messages=[{"role": "user", "content": prompt}]
    )

    response_text = response.content[0].text.strip()

    print(f"      ✓ Intelligence generated: {len(response_text)} characters")

    # Parse JSON response
    print(f"      Parsing JSON response...")

    # Remove markdown code blocks if Claude added them
    if response_text.startswith("```"):
        # Find first { and last }
        start_idx = response_text.find("{")
        end_idx = response_text.rfind("}") + 1
        response_text = response_text[start_idx:end_idx]

    try:
        intelligence = json.loads(response_text)
        print(f"      ✓ Successfully parsed JSON")
        print(f"         - Commercial summary: {len(intelligence.get('commercial_summary', ''))} chars")
        print(f"         - Technical summary: {len(intelligence.get('technical_summary', ''))} chars")
        print(f"         - Pain points: {len(intelligence.get('pain_points', []))} items")
        print(f"         - Next steps: {len(intelligence.get('next_steps', []))} items")
        print(f"         - Call history: {len(intelligence.get('call_history', []))} calls")
        return intelligence
    except json.JSONDecodeError as e:
        print(f"      ✗ JSON parsing failed: {e}")
        print(f"      Response preview: {response_text[:500]}...")
        raise Exception(f"Failed to parse Claude's JSON response: {e}")


def format_intelligence_preview(intelligence: dict, account_name: str) -> str:
    """
    Format intelligence dict as readable text for preview.

    Args:
        intelligence: Intelligence dict from Claude
        account_name: Account name

    Returns:
        Formatted string for display
    """
    output = []
    output.append(f"# Account Intelligence: {account_name}")
    output.append(f"Total Calls Analyzed: {len(intelligence.get('call_history', []))}")
    output.append("")

    output.append("## Commercial Summary")
    output.append(intelligence.get("commercial_summary", "N/A"))
    output.append("")

    output.append("## Technical Summary")
    output.append(intelligence.get("technical_summary", "N/A"))
    output.append("")

    output.append("## Key Pain Points")
    for i, pain_point in enumerate(intelligence.get("pain_points", []), 1):
        output.append(f"{i}. {pain_point}")
    output.append("")

    output.append("## Recommended Next Steps")
    for i, step in enumerate(intelligence.get("next_steps", []), 1):
        output.append(f"{i}. {step}")
    output.append("")

    output.append("## Risk Assessment")
    output.append(intelligence.get("risk_assessment", "N/A"))
    output.append("")

    output.append("## Call History")
    for call in intelligence.get("call_history", []):
        output.append(f"- {call.get('date', 'N/A')} | {call.get('title', 'N/A')} | {call.get('one_sentence', 'N/A')}")

    return "\n".join(output)


def format_intelligence_for_doc(intelligence: dict, account_name: str) -> str:
    """
    Format intelligence dict for Google Doc insertion.

    Args:
        intelligence: Intelligence dict from Claude
        account_name: Account name

    Returns:
        Formatted string ready for doc insertion with proper spacing
    """
    from datetime import datetime

    output = []
    output.append("=" * 60)
    output.append("ACCOUNT INTELLIGENCE")
    output.append("=" * 60)
    output.append(f"Account: {account_name}")
    output.append(f"Last Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    output.append(f"Total Calls Analyzed: {len(intelligence.get('call_history', []))}")
    output.append("")

    output.append("COMMERCIAL SUMMARY")
    output.append("-" * 60)
    output.append(intelligence.get("commercial_summary", "N/A"))
    output.append("")

    output.append("TECHNICAL SUMMARY")
    output.append("-" * 60)
    output.append(intelligence.get("technical_summary", "N/A"))
    output.append("")

    output.append("KEY PAIN POINTS")
    output.append("-" * 60)
    for i, pain_point in enumerate(intelligence.get("pain_points", []), 1):
        output.append(f"{i}. {pain_point}")
    output.append("")

    output.append("RECOMMENDED NEXT STEPS")
    output.append("-" * 60)
    for i, step in enumerate(intelligence.get("next_steps", []), 1):
        output.append(f"{i}. {step}")
    output.append("")

    output.append("RISK ASSESSMENT")
    output.append("-" * 60)
    output.append(intelligence.get("risk_assessment", "N/A"))
    output.append("")

    output.append("CALL HISTORY")
    output.append("-" * 60)
    for call in intelligence.get("call_history", []):
        output.append(f"• {call.get('date', 'N/A')} | {call.get('title', 'N/A')}")
        output.append(f"  {call.get('one_sentence', 'N/A')}")
    output.append("")
    output.append("=" * 60)
    output.append("END ACCOUNT INTELLIGENCE")
    output.append("=" * 60)
    output.append("\n\n")  # Extra spacing before call summaries

    return "\n".join(output)


def write_intelligence_to_doc(doc_url: str, intelligence: dict, account_name: str) -> None:
    """
    Write intelligence to the TOP of the summaries doc.

    Replaces existing intelligence section if found, otherwise prepends to beginning.

    Args:
        doc_url: Google Doc URL
        intelligence: Intelligence dict from Claude
        account_name: Account name
    """
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not credentials_path:
        raise ValueError("GOOGLE_APPLICATION_CREDENTIALS must be set in .env")

    print(f"\n{'='*60}")
    print(f"[3/4] Writing intelligence to Google Doc")
    print(f"{'='*60}\n")

    # Extract doc ID from URL
    doc_id = doc_url.split("/d/")[1].split("/")[0]
    print(f"      Doc ID: {doc_id}")

    # Authenticate
    credentials = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=["https://www.googleapis.com/auth/documents"]
    )
    service = build("docs", "v1", credentials=credentials)

    # Read existing doc to check for intelligence section
    print(f"      Reading existing doc content...")
    doc = service.documents().get(documentId=doc_id).execute()

    # Format intelligence text
    intelligence_text = format_intelligence_for_doc(intelligence, account_name)

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
                        # Found start of intelligence section
                        intelligence_start_index = element.get("startIndex")
                    elif end_marker in text and intelligence_start_index is not None:
                        # Found end of intelligence section
                        intelligence_end_index = element.get("endIndex")
                        break

    requests_body = []

    if intelligence_start_index is not None and intelligence_end_index is not None:
        # Replace existing intelligence section
        print(f"      Found existing intelligence section at indices {intelligence_start_index}-{intelligence_end_index}")
        print(f"      Replacing with updated intelligence...")

        # Delete old section, then insert new at same location
        requests_body = [
            {
                "deleteContentRange": {
                    "range": {
                        "startIndex": intelligence_start_index,
                        "endIndex": intelligence_end_index
                    }
                }
            },
            {
                "insertText": {
                    "location": {"index": intelligence_start_index},
                    "text": intelligence_text
                }
            }
        ]
    else:
        # No existing intelligence section - prepend to beginning
        print(f"      No existing intelligence section found")
        print(f"      Prepending intelligence to beginning of doc...")

        requests_body = [
            {
                "insertText": {
                    "location": {"index": 1},  # Beginning of doc
                    "text": intelligence_text
                }
            }
        ]

    service.documents().batchUpdate(
        documentId=doc_id,
        body={"requests": requests_body}
    ).execute()

    print(f"      ✓ Successfully wrote intelligence ({len(intelligence_text)} characters)")
    print(f"      ✓ View doc: {doc_url}")


def main():
    if len(sys.argv) < 3:
        print("Usage: uv run python scripts/test_synthesize.py <summaries-doc-url> <account-name>")
        print("Example: uv run python scripts/test_synthesize.py 'https://docs.google.com/document/d/YOUR_DOC_ID/edit' 'Acme Corp'")
        sys.exit(1)

    summaries_doc_url = sys.argv[1]
    account_name = sys.argv[2]

    try:
        # Step 1: Read summaries doc
        summaries_text = read_summaries_doc(summaries_doc_url)

        if not summaries_text.strip():
            print("\n⚠️  WARNING: Summaries doc is empty!")
            print("   Make sure you've run test_append_summary.py first to populate it.")
            sys.exit(1)

        # Step 2: Synthesize with Claude
        intelligence = synthesize_with_claude(summaries_text, account_name)

        # Step 3: Write to doc
        write_intelligence_to_doc(summaries_doc_url, intelligence, account_name)

        # Step 4: Preview (terminal output)
        print(f"\n{'='*60}")
        print(f"[4/4] Intelligence Preview")
        print(f"{'='*60}\n")

        formatted = format_intelligence_preview(intelligence, account_name)
        print(formatted)

        # Step 5: Success
        print(f"\n{'='*60}")
        print("✅ TEST PASSED")
        print(f"{'='*60}")
        print(f"Account: {account_name}")
        print(f"Calls analyzed: {len(intelligence.get('call_history', []))}")
        print(f"Intelligence written to: {summaries_doc_url}")

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
