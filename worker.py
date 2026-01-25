import asyncio
import logging
import os
from dotenv import load_dotenv
from temporalio.client import Client
from temporalio.worker import Worker
from workflow_intelligence import AccountIntelligenceWorkflow
from activities import (
    fetch_all_call_ids,
    find_or_create_summaries_doc,
    fetch_and_summarize_call,
    append_summary_to_doc,
    read_summaries_doc,
    synthesize_intelligence,
    write_intelligence_to_doc,
)

# Configure logging to see activity logs in terminal
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s:%(name)s: %(message)s'
)

# Reduce noise from third-party libraries
logging.getLogger('googleapiclient.discovery_cache').setLevel(logging.ERROR)
logging.getLogger('httpx').setLevel(logging.WARNING)


async def main():
    load_dotenv()

    # Connect to Temporal Cloud
    client = await Client.connect(
        os.getenv("TEMPORAL_ADDRESS"),
        namespace=os.getenv("TEMPORAL_NAMESPACE"),
        api_key=os.getenv("TEMPORAL_API_KEY"),
        tls=True,
    )

    # Create worker for Account Intelligence workflow
    worker = Worker(
        client,
        task_queue="gong-notes-queue",
        workflows=[AccountIntelligenceWorkflow],
        activities=[
            fetch_all_call_ids,
            find_or_create_summaries_doc,
            fetch_and_summarize_call,
            append_summary_to_doc,
            read_summaries_doc,
            synthesize_intelligence,
            write_intelligence_to_doc,
        ],
    )

    print("🚀 Worker started. Waiting for workflows...")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
