#!/usr/bin/env python3
"""
Trigger script for Account Intelligence workflow.

Usage:
    uv run python trigger_intelligence.py --account-name "Company Name" [--max-calls 30]

Examples:
    uv run python trigger_intelligence.py --account-name "Acme Corp"
    uv run python trigger_intelligence.py --account-name "Example Inc" --max-calls 50
"""

import asyncio
import os
import sys
import argparse
from dotenv import load_dotenv
from temporalio.client import Client
from workflow_intelligence import AccountIntelligenceWorkflow


async def main():
    parser = argparse.ArgumentParser(
        description="Trigger Account Intelligence workflow for a customer account"
    )
    parser.add_argument(
        "--account-name",
        required=True,
        help="Company/account name to search for in Gong (e.g., 'Acme Corp')"
    )
    parser.add_argument(
        "--max-calls",
        type=int,
        default=30,
        help="Maximum number of calls to analyze (default: 30)"
    )

    args = parser.parse_args()

    load_dotenv()

    # Connect to Temporal Cloud
    print("🔌 Connecting to Temporal Cloud...")
    client = await Client.connect(
        os.getenv("TEMPORAL_ADDRESS"),
        namespace=os.getenv("TEMPORAL_NAMESPACE"),
        api_key=os.getenv("TEMPORAL_API_KEY"),
        tls=True,
    )

    # Generate workflow ID from account name
    workflow_id = f"intelligence-{args.account_name.lower().replace(' ', '-')}-{os.urandom(4).hex()}"

    # Start workflow
    print(f"\n🚀 Starting Account Intelligence workflow")
    print(f"   Account: {args.account_name}")
    print(f"   Max Calls: {args.max_calls}")
    print(f"   Workflow ID: {workflow_id}")

    handle = await client.start_workflow(
        AccountIntelligenceWorkflow.run,
        args=[args.account_name, args.max_calls],
        id=workflow_id,
        task_queue="gong-notes-queue",
    )

    print(f"\n📋 Workflow started!")
    print(f"🔗 Temporal UI: https://cloud.temporal.io/namespaces/{os.getenv('TEMPORAL_NAMESPACE')}/workflows/{handle.id}")
    print("\n⏳ Processing (this may take 2-5 minutes for 30 calls)...\n")

    # Wait for result (no timeout - let it run)
    try:
        result = await handle.result()
        print("\n" + "="*60)
        print("✅ WORKFLOW COMPLETE")
        print("="*60)
        print(result)
    except Exception as e:
        print(f"\n❌ Workflow failed: {str(e)}")
        print(f"\nCheck Temporal UI for details:")
        print(f"https://cloud.temporal.io/namespaces/{os.getenv('TEMPORAL_NAMESPACE')}/workflows/{handle.id}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
