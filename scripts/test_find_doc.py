#!/usr/bin/env python3
"""
Test script for Activity 2: find_or_create_summaries_doc()

Tests Google Drive search and doc creation for Account Name - LLM - Summary docs.

Usage:
    uv run python scripts/test_find_doc.py <account-name>
"""

import sys
import os
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from anthropic import Anthropic

# Load environment variables
load_dotenv()


def find_or_create_summaries_doc(account_name: str) -> str:
    """
    Find or create [Account Name] - LLM - Summary doc using LLM-powered search.

    Args:
        account_name: Account name (e.g., "Acme Corp")

    Returns:
        Doc URL
    """
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")

    if not credentials_path or not anthropic_api_key:
        raise ValueError("GOOGLE_APPLICATION_CREDENTIALS and ANTHROPIC_API_KEY must be set in .env")

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

    print(f"\n{'='*60}")
    print(f"[1/5] Searching for existing doc by title")
    print(f"{'='*60}\n")
    print(f"      Target doc title: {doc_title}")

    # Step 1: Search for exact doc title
    doc_query = f"name = '{doc_title}' and mimeType='application/vnd.google-apps.document'"
    print(f"      Drive query: {doc_query}")

    doc_results = drive_service.files().list(
        q=doc_query,
        fields="files(id, name)",
        corpora='allDrives',
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()

    existing_docs = doc_results.get("files", [])
    print(f"      Results: Found {len(existing_docs)} matching doc(s)")

    if existing_docs:
        doc_id = existing_docs[0]["id"]
        doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"
        print(f"      ✓ Doc already exists!")
        print(f"        Doc ID: {doc_id}")
        print(f"        Doc URL: {doc_url}")
        return doc_url

    print(f"      ✗ No existing doc found")
    print(f"      → Will search for account folder to create doc")

    # Step 2: Search for account folder by name
    print(f"\n{'='*60}")
    print(f"[2/5] Searching for account folder")
    print(f"{'='*60}\n")
    print(f"      Target folder name: {account_name}")

    folder_query = f"name contains '{account_name}' and mimeType='application/vnd.google-apps.folder'"
    print(f"      Drive query: {folder_query}")

    folder_results = drive_service.files().list(
        q=folder_query,
        fields="files(id, name)",
        corpora='allDrives',
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()

    folders = folder_results.get("files", [])
    print(f"      Results: Found {len(folders)} matching folder(s)")

    if len(folders) == 0:
        print(f"      ✗ No account folder found")
        raise Exception(f"Could not find account folder '{account_name}'. Please create it manually first.")
    elif len(folders) > 1:
        print(f"      ⚠️  Multiple folders found with name '{account_name}':")
        for i, folder in enumerate(folders, 1):
            print(f"         {i}. {folder['name']} (ID: {folder['id']})")
        print(f"      → Using LLM to disambiguate...")

        # Step 3: Use LLM to pick correct folder
        print(f"\n{'='*60}")
        print(f"[3/5] Using Claude to identify correct folder")
        print(f"{'='*60}\n")

        folder_list = "\n".join([
            f"{i+1}. Folder: {f['name']} (ID: {f['id']})"
            for i, f in enumerate(folders)
        ])

        prompt = f"""You are helping identify the correct Google Drive folder for account: {account_name}

Multiple folders found with this name:
{folder_list}

Which folder is most likely the correct account folder? Return ONLY the folder number (1, 2, 3, etc.) with no explanation."""

        print(f"      Sending prompt to Claude...")
        print(f"      Model: claude-sonnet-4-5-20250929")

        response = claude_client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=10,
            messages=[{"role": "user", "content": prompt}]
        )

        choice_text = response.content[0].text.strip()
        print(f"      Claude's response: '{choice_text}'")

        try:
            choice_idx = int(choice_text) - 1
            if 0 <= choice_idx < len(folders):
                selected_folder = folders[choice_idx]
                print(f"      ✓ Claude selected folder #{choice_idx + 1}: {selected_folder['name']}")
                print(f"        Folder ID: {selected_folder['id']}")
            else:
                selected_folder = folders[0]
                print(f"      ⚠️  Claude's choice out of range, using first folder")
                print(f"        Selected: {selected_folder['name']} (ID: {selected_folder['id']})")
        except ValueError:
            selected_folder = folders[0]
            print(f"      ⚠️  Could not parse Claude's response as number, using first folder")
            print(f"        Selected: {selected_folder['name']} (ID: {selected_folder['id']})")

        account_folder_id = selected_folder["id"]
    else:
        account_folder_id = folders[0]["id"]
        print(f"      ✓ Found unique account folder: {folders[0]['name']}")
        print(f"        Folder ID: {account_folder_id}")
        print(f"\n      [Step 3 skipped - only one folder found]")

    # Step 4: Create doc
    print(f"\n{'='*60}")
    print(f"[4/5] Creating summary doc")
    print(f"{'='*60}\n")

    print(f"      Doc title: {doc_title}")
    print(f"      Parent folder ID: {account_folder_id}")

    doc_metadata = {
        "name": doc_title,
        "mimeType": "application/vnd.google-apps.document",
        "parents": [account_folder_id]
    }
    print(f"      Calling Drive API files().create()...")

    created_doc = drive_service.files().create(
        body=doc_metadata,
        fields="id",
        supportsAllDrives=True
    ).execute()

    doc_id = created_doc["id"]
    doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"

    print(f"      ✓ Doc created successfully!")
    print(f"        Doc ID: {doc_id}")

    # Step 5: Final summary
    print(f"\n{'='*60}")
    print(f"[5/5] Summary")
    print(f"{'='*60}\n")

    print(f"      Account: {account_name}")
    print(f"      Doc title: {doc_title}")
    print(f"      Doc URL: {doc_url}")
    print(f"      Location: Inside folder ID {account_folder_id}")

    return doc_url


def main():
    if len(sys.argv) < 2:
        print("Usage: uv run python scripts/test_find_doc.py <account-name>")
        print("Example: uv run python scripts/test_find_doc.py 'Acme Corp'")
        sys.exit(1)

    account_name = sys.argv[1]

    try:
        doc_url = find_or_create_summaries_doc(account_name)

        print(f"\n{'='*60}")
        print("✅ TEST PASSED")
        print(f"{'='*60}")
        print(f"Account: {account_name}")
        print(f"Doc URL: {doc_url}")
        print(f"\nDoc is ready for call summaries to be appended.")

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
