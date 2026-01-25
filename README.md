# Account Intelligence System

Temporal workflow that analyzes ALL Gong calls for an account to generate holistic intelligence with Claude AI.

## What It Does

Analyzes all calls for a customer account to generate comprehensive intelligence:

1. **Fetches all call IDs** from Gong for the account (up to 30 most recent)
2. **Summarizes each call** in parallel using Claude (REPORTER role - factual)
3. **Synthesizes intelligence** across all calls using Claude (ANALYST role - patterns)
4. **Writes to Google Doc**: `[Account Name] - LLM - Summary`
   - Intelligence report at top (pain points, next steps, risks)
   - Individual call summaries below

Solves: SAs need quick account context before calls but lack time to read 20+ transcripts.

## Quick Start

```bash
# Install dependencies
uv sync

# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Get your team's Gong user IDs (one-time setup)
uv run python scripts/get_team_user_ids.py --list-managers
uv run python scripts/get_team_user_ids.py --manager-id <manager-id>
# Add output to .env as GONG_PRIMARY_USER_IDS

# Terminal 1: Start worker
uv run python worker.py

# Terminal 2: Trigger Account Intelligence workflow
uv run python trigger_intelligence.py --account-name "Company Name" --max-calls 30
```

## Requirements

- Python 3.12+
- [UV](https://docs.astral.sh/uv/) package manager
- API keys for: Temporal Cloud, Gong, Anthropic, Google Cloud
- Google service account with Drive + Docs access

## Environment Setup

Copy `.env.example` to `.env` and configure:

- **Temporal Cloud**: namespace, address, API key
- **Gong**: API key + secret
- **Anthropic**: API key for Claude
- **Google**: Service account JSON path
- **Test**: Google Doc URL for testing

## Google Docs Setup

1. Ensure service account has edit access to your shared drive
2. Workflow auto-finds/creates doc: `[Account Name] - LLM - Summary`
3. Intelligence written to top, call summaries appended chronologically

## Testing

```bash
# Test individual activities
uv run python scripts/test_gong_fetch_all.py "Company Name"
uv run python scripts/test_summarize_call.py <call-id>
uv run python scripts/test_find_doc.py "Company Name"
uv run python scripts/test_synthesize.py
```

## Architecture

**AccountIntelligenceWorkflow** - 7 activities, parallel execution:

1. `fetch_all_call_ids` - Search Gong by account name (time-windowed + team filter)
2. `find_or_create_summaries_doc` - Find/create `[Account] - LLM - Summary` doc
3. `fetch_and_summarize_call` (parallel) - Fetch transcript + Claude summarize
4. `append_summary_to_doc` (parallel) - Prepend summary to doc (idempotent)
5. `read_summaries_doc` - Read all summaries from doc
6. `synthesize_intelligence` - Claude ANALYST synthesis (pain points, next steps, risks)
7. `write_intelligence_to_doc` - Write intelligence to doc top (idempotent)

**Key optimizations**:
- Parallel summarization: 30 calls processed concurrently
- Gong API efficiency: Time-windowing + primaryUserIds filter (3-5 API calls vs 125+)
- Idempotent activities: Safe to retry without duplicates

See [.claude/CLAUDE.md](.claude/CLAUDE.md) for detailed documentation.

## Project Structure

```
├── activities.py                  # Temporal activities
├── workflow_intelligence.py       # Account Intelligence workflow
├── worker.py                      # Temporal worker
├── trigger_intelligence.py        # Workflow trigger
├── scripts/
│   ├── get_team_user_ids.py     # Helper: Get Gong user IDs
│   ├── test_gong_fetch_all.py   # Test: Fetch all calls for account
│   ├── test_summarize_call.py   # Test: Summarize single call
│   ├── test_find_doc.py         # Test: Find/create summaries doc
│   └── test_synthesize.py       # Test: Intelligence synthesis
├── .claude/
│   ├── CLAUDE.md                # Detailed documentation
│   └── skills/                  # API reference docs
└── pyproject.toml               # Dependencies
```

## Known Limitations

- Max 30 calls per run (configurable via `--max-calls`)
- Manual trigger only (no webhook listener)
- Call type-specific prompts not yet implemented

## Future Enhancements

- Incremental synthesis (update intelligence for new calls without full re-synthesis)
- Gong call caching (avoid re-fetching unchanged transcripts)
- Enhanced synthesis prompt (use case, competitive intel, business impact)
- Gong webhook integration for automatic processing
- Salesforce integration

## License

MIT
