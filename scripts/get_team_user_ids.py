#!/usr/bin/env python3
"""
Helper script to get Gong user IDs for your team by manager.

This script helps you populate the GONG_PRIMARY_USER_IDS environment variable
which drastically reduces API calls when fetching calls for accounts.

Usage:
    # List all managers to find your manager's user ID
    uv run python scripts/get_team_user_ids.py --list-managers

    # Get team member IDs for a specific manager
    uv run python scripts/get_team_user_ids.py --manager-id 563515258458745
"""

import sys
import os
import argparse
from dotenv import load_dotenv
import requests

# Load environment variables
load_dotenv()


def fetch_all_users():
    """
    Fetch all users from Gong API with pagination.

    Returns:
        list: All users from Gong workspace
    """
    api_key = os.getenv("GONG_API_KEY")
    api_secret = os.getenv("GONG_API_SECRET")

    if not api_key or not api_secret:
        raise ValueError("GONG_API_KEY and GONG_API_SECRET must be set in .env")

    auth = (api_key, api_secret)

    all_users = []
    cursor = None
    page_num = 0

    print("\n[1/2] Fetching all users from Gong API...")

    while True:
        # Build request body
        body = {"filter": {}}
        if cursor:
            body["cursor"] = cursor

        response = requests.post(
            "https://api.gong.io/v2/users/extensive",
            auth=auth,
            headers={"Content-Type": "application/json"},
            json=body
        )

        if response.status_code != 200:
            print(f"❌ Gong API Error: {response.status_code}")
            print(f"Response: {response.text}")
            response.raise_for_status()

        data = response.json()
        page_users = data.get("users", [])
        all_users.extend(page_users)

        page_num += 1
        print(f"      Page {page_num}: Fetched {len(page_users)} users (total: {len(all_users)})")

        cursor = data.get("records", {}).get("cursor")
        if not cursor:
            break

    print(f"      ✓ Total users fetched: {len(all_users)}")
    return all_users


def list_all_users_with_managers(users):
    """
    List all users with their manager information.

    Args:
        users: List of user objects from Gong API

    Returns:
        list: List of dicts with user info and their manager's info
    """
    users_with_managers = []

    for user in users:
        user_id = user.get("id")
        first_name = user.get("firstName", "")
        last_name = user.get("lastName", "")
        email = user.get("emailAddress", "")
        manager_id = user.get("managerId")
        active = user.get("active", False)

        user_info = {
            "user_id": user_id,
            "name": f"{first_name} {last_name}".strip(),
            "email": email,
            "active": active,
            "manager_id": manager_id,
            "manager_name": None,
            "manager_email": None
        }

        # Find manager's details if they have one
        if manager_id:
            manager = next((u for u in users if u.get("id") == manager_id), None)
            if manager:
                manager_first = manager.get("firstName", "")
                manager_last = manager.get("lastName", "")
                user_info["manager_name"] = f"{manager_first} {manager_last}".strip()
                user_info["manager_email"] = manager.get("emailAddress", "")

        users_with_managers.append(user_info)

    return users_with_managers


def get_team_members(users, manager_id):
    """
    Get all users under a specific manager.

    Args:
        users: List of user objects from Gong API
        manager_id: Manager's user ID to filter by

    Returns:
        list: Users who report to the specified manager
    """
    team_members = []

    for user in users:
        if user.get("managerId") == manager_id:
            # Only include active users
            if user.get("active", False):
                team_members.append(user)

    return team_members


def main():
    parser = argparse.ArgumentParser(
        description="Get Gong user IDs for your team by manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List all managers
  uv run python scripts/get_team_user_ids.py --list-managers

  # Get team IDs for a specific manager
  uv run python scripts/get_team_user_ids.py --manager-id 563515258458745
        """
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--list-managers",
        action="store_true",
        help="List all managers in the workspace"
    )
    group.add_argument(
        "--manager-id",
        type=str,
        help="Get team member IDs for this manager"
    )

    args = parser.parse_args()

    try:
        # Fetch all users
        users = fetch_all_users()

        if args.list_managers:
            # List all users with their managers
            print("\n[2/2] Listing all users with manager information...")
            users_with_managers = list_all_users_with_managers(users)

            # Filter to only active users for cleaner output
            active_users = [u for u in users_with_managers if u["active"]]

            print(f"\n{'='*80}")
            print(f"Found {len(active_users)} active users:")
            print(f"{'='*80}\n")

            # Sort by manager name, then by user name
            sorted_users = sorted(
                active_users,
                key=lambda x: (x["manager_name"] or "ZZZ", x["name"])
            )

            current_manager = None
            for user_info in sorted_users:
                manager_name = user_info["manager_name"] or "No Manager"

                # Print manager header when it changes
                if manager_name != current_manager:
                    if current_manager is not None:
                        print()  # Blank line between manager groups
                    print(f"--- Manager: {manager_name} (ID: {user_info['manager_id'] or 'N/A'}) ---")
                    current_manager = manager_name

                print(f"  User ID: {user_info['user_id']}")
                print(f"  Name: {user_info['name']}")
                print(f"  Email: {user_info['email']}")
                print()

            print(f"{'='*80}")
            print("Next step: Find your manager's ID above, then run:")
            print("  uv run python scripts/get_team_user_ids.py --manager-id <manager-id>")
            print(f"{'='*80}")

        elif args.manager_id:
            # Get team members for specific manager
            manager_id = args.manager_id

            print(f"\n[2/2] Finding team members for manager {manager_id}...")

            # First, find the manager's name
            manager = next((u for u in users if u.get("id") == manager_id), None)
            if not manager:
                print(f"❌ Error: Manager with ID {manager_id} not found")
                sys.exit(1)

            manager_name = f"{manager.get('firstName', '')} {manager.get('lastName', '')}".strip()
            print(f"      Manager: {manager_name} ({manager.get('emailAddress', 'No email')})")

            # Get team members
            team_members = get_team_members(users, manager_id)

            if not team_members:
                print(f"\n⚠️  No team members found under manager {manager_id}")
                sys.exit(0)

            print(f"\n{'='*60}")
            print(f"Found {len(team_members)} team members:")
            print(f"{'='*60}\n")

            user_ids = []
            for user in sorted(team_members, key=lambda x: (x.get('lastName', ''), x.get('firstName', ''))):
                user_id = user.get("id")
                first_name = user.get("firstName", "")
                last_name = user.get("lastName", "")
                email = user.get("emailAddress", "")
                title = user.get("title", "No title")

                user_ids.append(user_id)

                print(f"{first_name} {last_name}")
                print(f"  Email: {email}")
                print(f"  Title: {title}")
                print(f"  User ID: {user_id}")
                print()

            # Output for .env
            print(f"{'='*60}")
            print("Copy this to your .env file:")
            print(f"{'='*60}\n")
            print(f"GONG_PRIMARY_USER_IDS={','.join(user_ids)}")
            print()

    except Exception as e:
        print(f"\n{'='*60}")
        print("❌ ERROR")
        print(f"{'='*60}")
        print(f"{str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
