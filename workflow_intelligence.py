import asyncio
from datetime import timedelta
from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from activities import (
        fetch_all_call_ids,
        find_or_create_summaries_doc,
        fetch_and_summarize_call,
        append_summary_to_doc,
        read_summaries_doc,
        synthesize_intelligence,
        write_intelligence_to_doc,
    )


@workflow.defn
class AccountIntelligenceWorkflow:
    """
    Workflow that generates account intelligence from ALL Gong calls.

    Analyzes all calls for an account to generate holistic intelligence:
    - Individual call summaries (REPORTER role)
    - Cross-call intelligence synthesis (ANALYST role)

    Flow:
    1. Fetch all call IDs from Gong (Activity 1)
    2. Find/create summaries doc (Activity 2)
    3. Parallel: Fetch & summarize each call (Activity 3a)
    4. Parallel: Append summaries to doc (Activity 3b)
    5. Read all summaries from doc (Activity 4a)
    6. Synthesize intelligence with Claude (Activity 4b)
    7. Write intelligence to top of doc (Activity 5)
    """

    @workflow.run
    async def run(self, account_name: str, max_calls: int = 30) -> str:
        """
        Main workflow execution.

        Args:
            account_name: Company name to search for in Gong
            max_calls: Maximum calls to analyze (default: 30)

        Returns:
            Summary message with doc URL and call count
        """
        retry_policy = RetryPolicy(
            maximum_attempts=3,
            initial_interval=timedelta(seconds=1),
            maximum_interval=timedelta(seconds=10),
        )

        workflow.logger.info(f"Starting account intelligence workflow for: {account_name}")

        # Step 1: Fetch all call IDs from Gong
        workflow.logger.info("[Step 1/7] Fetching call IDs from Gong...")
        call_ids, total_count, detected_account = await workflow.execute_activity(
            fetch_all_call_ids,
            args=[account_name, max_calls],
            start_to_close_timeout=timedelta(minutes=5),  # Can take 1-2 min with 30+ API calls
            retry_policy=retry_policy,
        )

        if not call_ids:
            workflow.logger.warning(f"No calls found for {account_name}")
            return f"No calls found for {account_name}"

        workflow.logger.info(f"Found {len(call_ids)} calls to process (total: {total_count})")

        # Use detected account name if found
        account_display_name = detected_account if detected_account else account_name

        # Step 2: Find or create summaries doc
        workflow.logger.info("[Step 2/7] Finding or creating summaries doc...")
        summaries_doc_url = await workflow.execute_activity(
            find_or_create_summaries_doc,
            args=[account_display_name],
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=retry_policy,
        )

        workflow.logger.info(f"Using doc: {summaries_doc_url}")

        # Step 3a: Fetch and summarize each call IN PARALLEL
        workflow.logger.info(f"[Step 3/7] Fetching and summarizing {len(call_ids)} calls in parallel...")
        summarize_tasks = [
            workflow.execute_activity(
                fetch_and_summarize_call,
                args=[call_id],
                start_to_close_timeout=timedelta(minutes=5),  # Gong transcript + Claude can take 30-60s
                retry_policy=retry_policy,
            )
            for call_id in call_ids
        ]
        summaries_with_ids = await asyncio.gather(*summarize_tasks)

        workflow.logger.info(f"Generated {len(summaries_with_ids)} summaries")

        # Step 3b: Append summaries to doc IN PARALLEL (with idempotency)
        workflow.logger.info("[Step 4/7] Appending summaries to doc in parallel...")
        append_tasks = [
            workflow.execute_activity(
                append_summary_to_doc,
                args=[summaries_doc_url, summary, call_id],
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=retry_policy,
            )
            for summary, call_id in summaries_with_ids
        ]
        append_results = await asyncio.gather(*append_tasks)

        # Count how many summaries were actually added (vs skipped due to idempotency)
        new_summaries_added = sum(1 for was_added in append_results if was_added)

        workflow.logger.info(f"Appended {new_summaries_added} new summaries ({len(call_ids) - new_summaries_added} already existed)")

        # If no new summaries were added, skip synthesis and exit early
        if new_summaries_added == 0:
            workflow.logger.info("No new summaries added - all calls already in doc. Skipping synthesis.")
            return f"""Account Intelligence: No Updates Needed

Account: {account_display_name}
All {len(call_ids)} calls already processed. No new intelligence to generate.
Document: {summaries_doc_url}"""

        # Step 4a: Read all summaries from doc
        workflow.logger.info("[Step 5/7] Reading all summaries from doc...")
        summaries_text = await workflow.execute_activity(
            read_summaries_doc,
            args=[summaries_doc_url],
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=retry_policy,
        )

        # Step 4b: Synthesize intelligence with Claude
        workflow.logger.info("[Step 6/7] Synthesizing intelligence with Claude...")
        intelligence = await workflow.execute_activity(
            synthesize_intelligence,
            args=[summaries_text, account_display_name],
            start_to_close_timeout=timedelta(minutes=5),  # Longer for synthesis
            retry_policy=retry_policy,
        )

        workflow.logger.info(f"Intelligence synthesized: {len(intelligence.get('pain_points', []))} pain points")

        # Step 5: Write intelligence to top of doc
        workflow.logger.info("[Step 7/7] Writing intelligence to doc...")
        await workflow.execute_activity(
            write_intelligence_to_doc,
            args=[summaries_doc_url, intelligence, account_display_name],
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=retry_policy,
        )

        result_message = f"""Account Intelligence Complete!

Account: {account_display_name}
Calls Analyzed: {len(call_ids)} of {total_count} total
Document: {summaries_doc_url}

Intelligence includes:
- {len(intelligence.get('pain_points', []))} key pain points
- {len(intelligence.get('next_steps', []))} recommended next steps
- {len(intelligence.get('call_history', []))} calls in history

View the intelligence and call summaries at:
{summaries_doc_url}
"""

        workflow.logger.info(f"\n{'='*60}\n[WORKFLOW COMPLETE] ✅ Success!\n{'='*60}\n")
        workflow.logger.info(result_message)

        return result_message
