#!/usr/bin/env python3
"""
Test script for Activity 1: fetch_all_call_ids()

Tests Gong API pagination and filtering logic to fetch all calls for an account.

Usage:
    uv run python scripts/test_gong_fetch_all.py <company-name>
"""

import sys
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
import requests

# Load environment variables
load_dotenv()

# Common TLD suffixes to strip when formatting account names for display
COMMON_TLDS = [".com", ".io", ".ai", ".net", ".org", ".co", ".edu"]


def normalize_company_name(name: str) -> str:
    """Normalize company name for comparison (lowercase, no spaces/hyphens)"""
    return name.lower().replace(" ", "").replace("-", "")


def extract_company_from_email(email: str) -> str:
    """
    Extract normalized company name from email domain for matching.

    NOTE: This is only used for display purposes (formatting detected_account_name).
    Call matching is done purely via call titles, NOT email domains.

    Examples:
        user@acme.com → acme
        john@example.ai → example
        person@company-corp.io → companycorp
    """
    if "@" not in email:
        return ""

    domain = email.split("@")[1]

    # Normalize (remove hyphens, dots) but don't strip TLDs here
    # TLD stripping only happens when formatting for display
    return normalize_company_name(domain)


def fetch_all_call_ids(company_name: str, max_calls: int = 50, window_days: int = 30):
    """
    Fetch all call IDs for an account from Gong API using time-windowing strategy.

    Args:
        company_name: Company name to filter calls (lowercase, e.g., 'acme')
        max_calls: Maximum calls to return (default: 50)
        window_days: Size of time window in days (default: 30)

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
    normalized_company = normalize_company_name(company_name)

    print(f"\n{'='*60}")
    print(f"[1/4] Searching for calls from company: {company_name}")
    print(f"      (normalized search term: {normalized_company})")
    print(f"      Using time-windowing strategy: {window_days}-day windows")
    if user_ids:
        print(f"      ✅ PRIMARY USER FILTER: {len(user_ids)} users (massive efficiency boost!)")
    else:
        print(f"      ⚠️  No primaryUserIds filter - will be slow (see .env.example)")
    print(f"{'='*60}\n")

    matching_calls = []
    total_api_calls = 0
    consecutive_empty_windows = 0
    max_consecutive_empty = 4  # Stop after 4 months of no activity

    print(f"[2/4] Fetching and filtering calls (time windows, newest first)...")

    # Work backwards from today in time windows (max 2 years = 24 months)
    for months_back in range(24):
        # Calculate window boundaries
        end_date = datetime.now() - timedelta(days=window_days * months_back)
        start_date = end_date - timedelta(days=window_days)

        # Format dates for Gong API
        from_date = start_date.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        to_date = end_date.strftime("%Y-%m-%dT%H:%M:%S+00:00")

        print(f"      Window {months_back + 1}: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}...", end="", flush=True)

        # Fetch all calls in this window (may require pagination within window)
        cursor = None
        window_matches = 0
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
                print(f"\n❌ Gong API Error: {response.status_code}")
                print(f"Response: {response.text}")
                response.raise_for_status()

            data = response.json()
            page_calls = data.get("calls", [])

            # Filter by call title ONLY - simple and reliable
            for call in page_calls:
                call_title = call.get("metaData", {}).get("title", "").lower()
                if normalized_company in call_title:
                    matching_calls.append(call)
                    window_matches += 1

            # Check for pagination within this window
            records_info = data.get("records", {})
            cursor = records_info.get("cursor")

            if not cursor:
                break  # No more pages in this window

        print(f" {window_matches} matches ({window_api_calls} API calls)")

        # Track consecutive empty windows
        if window_matches == 0:
            consecutive_empty_windows += 1
            # Stop if we've found some calls but hit 4 consecutive empty months
            if consecutive_empty_windows >= max_consecutive_empty and len(matching_calls) > 0:
                print(f"      ✓ No activity for {max_consecutive_empty} consecutive windows (dormant account), stopping")
                print(f"      Total API calls: {total_api_calls}")
                break
        else:
            consecutive_empty_windows = 0  # Reset counter when we find matches

        # Stop early if we have enough matches
        if len(matching_calls) >= max_calls:
            print(f"      ✓ Found {max_calls}+ matching calls, stopping early")
            print(f"      Total API calls: {total_api_calls} (vs 125+ with old approach!)")
            break

    # Extract account name from first matching call for display purposes only
    detected_account_name = ""
    if matching_calls:
        first_call = matching_calls[0]
        for party in first_call.get("parties", []):
            email = party.get("emailAddress", "")
            if normalized_company in extract_company_from_email(email):
                # Extract domain and strip TLD for pretty display
                domain = email.split("@")[1] if "@" in email else ""
                for tld in COMMON_TLDS:
                    if domain.endswith(tld):
                        domain = domain[:-len(tld)]
                        break
                detected_account_name = domain.replace("-", " ").title()
                break

    print(f"\n[3/4] Finalizing results")
    print(f"      Total matching calls: {len(matching_calls)}")
    print(f"      Total API calls: {total_api_calls} (time-windowing strategy)")
    print(f"      Detected account name: {detected_account_name}")

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
    limited_count = len(call_ids)

    print(f"\n[4/4] Results:")
    print(f"      Total matching calls: {total_count}")
    print(f"      Calls to process: {limited_count} (limited to {max_calls})")

    if total_count > max_calls:
        print(f"\n⚠️  Warning: Account has {total_count} calls, but only processing {max_calls} most recent")

    # Print sample of call IDs
    print(f"\n{'='*60}")
    print(f"Most Recent Calls (up to 20):")
    print(f"{'='*60}")
    for i, call_id in enumerate(call_ids[:20], 1):
        # Find the call metadata for display
        call = next((c for c in matching_calls if c.get("metaData", {}).get("id") == call_id), None)
        if call:
            metadata = call.get("metaData", {})
            title = metadata.get("title", "Untitled")
            date = metadata.get("scheduled", "Unknown date")
            print(f"{i}. {call_id}")
            print(f"   Title: {title}")
            print(f"   Date: {date}")

    if limited_count > 20:
        print(f"\n... and {limited_count - 20} more calls\n")

    return call_ids, total_count, detected_account_name


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: uv run python scripts/test_gong_fetch_all.py <company-name>")
        print("Example: uv run python scripts/test_gong_fetch_all.py acme")
        sys.exit(1)

    company_name = sys.argv[1]

    try:
        call_ids, total, account_name = fetch_all_call_ids(company_name)

        print(f"\n{'='*60}")
        print("✅ TEST PASSED")
        print(f"{'='*60}")
        print(f"Company: {company_name}")
        print(f"Account Name: {account_name}")
        print(f"Call IDs Fetched: {len(call_ids)}")
        print(f"Total Calls: {total}")

    except Exception as e:
        print(f"\n{'='*60}")
        print("❌ TEST FAILED")
        print(f"{'='*60}")
        print(f"Error: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
