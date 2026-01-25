#!/usr/bin/env python3
"""
Generate a Google API access token from service account credentials.

Usage: uv run python scripts/get-token.py

Useful for testing Google Drive/Docs API calls with curl or other tools.
Requires GOOGLE_APPLICATION_CREDENTIALS to be set in .env
"""
import os
from dotenv import load_dotenv
from google.oauth2 import service_account
from google.auth.transport.requests import Request

load_dotenv()

# Load your service account credentials from environment variable
credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

if not credentials_path:
    raise ValueError("GOOGLE_APPLICATION_CREDENTIALS not set in .env")

credentials = service_account.Credentials.from_service_account_file(
    credentials_path,
    scopes=['https://www.googleapis.com/auth/drive']
)

credentials.refresh(Request())
print(credentials.token)