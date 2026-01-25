# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Automated Account Intelligence System

## What This Does
Temporal workflow that analyzes ALL Gong calls for a customer account to generate holistic intelligence:
1. **Call Summaries**: Each call summarized independently (parallelizable)
2. **Account Intelligence**: Synthesized view combining all calls into unified assessment

Solves: Commercial SAs needing comprehensive account understanding before strategic calls.

## Tech Stack
- Python 3.12+ with UV for package management
- Temporal Python SDK connecting to Temporal Cloud
- APIs: Gong, Anthropic (Claude Sonnet 4.5), Google Drive/Docs
- macOS development environment

## Architecture Overview

### Two-Phase Processing Pattern

**Phase 1: Parallel Call Summarization**
- Fetch all call IDs for account from Gong API
- Summarize each call independently (can run in parallel)
- Append summaries to intermediate storage doc: `[Account Name] - Call Summaries`

**Phase 2: Holistic Synthesis**
- Read all summaries from intermediate doc
- Send to Claude for holistic analysis
- Write synthesized intelligence to output doc: `[Account Name] - Intelligence`

### External Storage Pattern (Bypasses 2MB Temporal Limit)

**Problem**: Temporal gRPC has 2MB limit per activity input/output. Passing 50 call summaries through activity parameters could exceed this.

**Solution**: Store summaries in Google Doc, pass only doc URL (tiny string) through activities.

```
┌─────────────────────────────────────────────────────────────┐
│                    Temporal Workflow                        │
├─────────────────────────────────────────────────────────────┤
│  Activity 1: fetch_all_call_ids(account_email)             │
│              → Returns: List[str] (call IDs)                │
│                                                             │
│  Activity 2: find_or_create_summaries_doc(account_email)   │
│              → Returns: str (doc URL)                       │
│                                                             │
│  Activity 3: summarize_and_append(call_id, doc_url)        │
│              [Run in parallel for each call]                │
│              → Writes to Google Doc (bypasses 2MB limit)    │
│              → Returns: None                                │
│                                                             │
│  Activity 4: synthesize_intelligence(summaries_doc_url)    │
│              → Reads from Google Doc (input is just URL)    │
│              → Returns: dict{commercial, technical, next}   │
│                                                             │
│  Activity 5: write_intelligence_doc(intel, account_email)  │
│              → Writes to "[Account] - Intelligence" doc     │
│              → Returns: str (doc URL)                       │
└─────────────────────────────────────────────────────────────┘
```

### Document Structure

**Intermediate Storage: `[Account Name] - Call Summaries`**
```
=== CALL SUMMARY: 2025-12-15 - Discovery Call ===
Call ID: 123456789
Participants: John (Customer), Sarah (AE), Mike (SA)
Duration: 45 minutes

Summary:
[300-word summary with key points, technical requirements, pain points]

===

=== CALL SUMMARY: 2025-12-18 - Technical Deep Dive ===
Call ID: 987654321
...
```

**Output: `[Account Name] - Intelligence`**
```
# Account Intelligence: [Account Name]
Last Updated: 2025-12-28
Total Calls Analyzed: 12

## Commercial Summary
[Holistic view of deal stage, key stakeholders, budget, timeline, competitors]

## Technical Summary
[Architecture requirements, integrations, security concerns, technical blockers]

## Key Pain Points
[Prioritized list of pain points mentioned across all calls]

## Recommended Next Steps
[Strategic recommendations based on full account history]

## Risk Assessment
[Deal risks, technical risks, competitive risks]

## Call History
[Chronological list of calls with dates and one-sentence summaries]
```

## Workflow Architecture

**7 Activities** in [activities.py](activities.py) executed by [workflow_intelligence.py](workflow_intelligence.py):

1. **`fetch_all_call_ids`** - Search Gong API with time-windowing + LLM filtering
2. **`find_or_create_summaries_doc`** - Navigate Drive folder hierarchy, find/create doc
3. **`fetch_and_summarize_call`** - (Parallel) Fetch transcript + Claude REPORTER summary
4. **`append_summary_to_doc`** - (Parallel, idempotent) Prepend summary to doc
5. **`read_summaries_doc`** - Read all summaries and sort chronologically
6. **`synthesize_intelligence`** - Claude ANALYST synthesis (pain points, next steps, risks)
7. **`write_intelligence_to_doc`** - (Idempotent) Write intelligence to doc top

**Key Patterns**:
- Parallel execution: Steps 3-4 run concurrently for all calls
- Idempotency: Activities 4 & 7 check for existing content before writing
- External storage: Pass doc URLs between activities (not full content) to avoid 2MB Temporal limit
- Stateless activities: No instance variables, all state in parameters or external docs

## Claude Prompts

Two-role prompting strategy for quality:

1. **REPORTER role** ([activities.py:604-661](activities.py#L604-L661)): Extracts facts from individual calls
   - ~300 word summaries
   - Sections: Call Type, Participants, What Happened, Use Case, Technical Details, Commercial Details, Risks, Action Items
   - Temporal-specific context: OSS vs Cloud, migration patterns

2. **ANALYST role** ([activities.py:856-911](activities.py#L856-L911)): Synthesizes patterns across ALL calls
   - Returns structured JSON
   - Sections: Quick Context, Blocking Progress, Next Actions, Risks, Call History
   - Scannable format for SA call prep
   - Looks for patterns, contradictions, gaps in discussion


## Environment Variables

Required in `.env` (copy from `.env.example`):

```bash
# Temporal Cloud
TEMPORAL_NAMESPACE=...
TEMPORAL_ADDRESS=...
TEMPORAL_API_KEY=...

# Gong API
GONG_API_KEY=...
GONG_API_SECRET=...
GONG_PRIMARY_USER_IDS=...  # CRITICAL for efficiency (125+ API calls → 5)

# Claude
ANTHROPIC_API_KEY=...
ANTHROPIC_MODEL=claude-sonnet-4-5-20250929  # Optional, defaults to Sonnet 4.5

# Google Drive/Docs
GOOGLE_APPLICATION_CREDENTIALS=path/to/service-account.json
GOOGLE_DRIVE_ACCOUNTS_ROOT_FOLDER_ID=...  # Your "Accounts & Prospects" folder ID
```

**Getting `GONG_PRIMARY_USER_IDS` (one-time setup)**:
```bash
# 1. List all users, find your manager
uv run python scripts/get_team_user_ids.py --list-managers

# 2. Get team member IDs for that manager
uv run python scripts/get_team_user_ids.py --manager-id <manager-user-id>

# 3. Copy comma-separated list to .env
```

## Common Commands

**Setup**:
```bash
uv sync                      # Install dependencies
cp .env.example .env         # Configure environment
```

**Running**:
```bash
# Terminal 1: Start worker
uv run python worker.py

# Terminal 2: Trigger workflow
uv run python trigger_intelligence.py --account-name "Company Name" --max-calls 30
```

**Testing Individual Activities**:
```bash
# Test Gong call fetching (Activity 1)
uv run python scripts/test_gong_fetch_all.py "Company Name"

# Test call summarization (Activity 3a)
uv run python scripts/test_summarize_call.py <call-id>

# Test doc finding/creation (Activity 2)
uv run python scripts/test_find_doc.py "Company Name"

# Test intelligence synthesis (Activity 4b)
uv run python scripts/test_synthesize.py

# Test summary appending (Activity 3b)
uv run python scripts/test_append_summary.py <call-id> <doc-url>
```

## System Status

**✅ FULLY OPERATIONAL** (as of 2026-01-10)

All 7 activities implemented and tested. System can:
- Fetch all Gong calls for an account (efficient time-windowing + LLM filtering)
- Summarize calls in parallel with Claude REPORTER role
- Synthesize holistic intelligence with Claude ANALYST role
- Write formatted output to Google Docs with idempotent operations

See [README.md](README.md) for architecture details and limitations.

## 📋 CURRENT PRIORITIES

### ✅ Priority 1: Rich Text Formatting in Google Docs (COMPLETED)

**Implementation**: Added rich text formatting to both intelligence synthesis and call summaries.

**Changes Made**:
- Updated `write_intelligence_to_doc()` ([activities.py:940-1273](activities.py#L940-L1273))
  - Bold, 16pt title: "ACCOUNT INTELLIGENCE"
  - Bold, 12pt section headers: "QUICK CONTEXT", "BLOCKING PROGRESS", etc.
  - Bold metadata labels: "Account:", "Last Updated:", "Total Calls:"
  - Native Google Docs bullets for all lists
- Updated `append_summary_to_doc()` ([activities.py:692-793](activities.py#L692-L793))
  - Bold call summary headers: "=== CALL SUMMARY: ..."
  - Bold metadata labels: "Call ID:", "Participants:", "Duration:"

**Testing**:
```bash
uv run python trigger_intelligence.py --account-name "Test Company" --max-calls 5
```

**Expected Output**: Professional, scannable Google Doc with bold headers and proper bullet formatting.

### Priority 2 (NEXT): Slack Integration for Quick Prep

**Status**: Ready to implement after Priority 1 completion.

**Goal**: Enable SAs to get quick account context via Slack without opening docs.

**Use Cases**:
1. Pre-call prep: "What was discussed last time?"
2. Qualification: "Is this technically ready for SA engagement?"
3. Quick context: "Summarize last 2-3 calls"

**Implementation Options**:

**Option A: Slack Bot + Existing Workflow** (Recommended)
- Create Slack bot that listens for `/prep <account-name>` command
- Trigger `AccountIntelligenceWorkflow` with small max_calls (e.g., 5 most recent)
- Parse intelligence JSON from workflow result
- Format as concise Slack message with key sections:
  - Use case (1 line)
  - Deal stage (1 line)
  - Top 2 blockers
  - Top 3 next actions
  - Link to full doc
- Estimated: 200-300 lines (Slack SDK + formatter)

**Option B: Lightweight Summary Workflow**
- Create new `QuickPrepWorkflow` that:
  - Fetches only last 3-5 calls
  - Skips writing to Google Docs
  - Returns condensed summary optimized for Slack
  - Uses simpler Claude prompt focused on "what SA needs to know right now"
- Estimated: 100 lines workflow + 200 lines Slack bot

**Tech Requirements**:
- Slack SDK for Python (`slack-bolt`)
- Slack app with slash command permissions
- Environment variables: `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`
- Deployment: Run Slack listener process alongside Temporal worker

**File Structure**:
```
├── slack_bot.py              # Slack command listener
├── slack_formatter.py        # Format intelligence for Slack
├── workflow_quick_prep.py    # Optional: lightweight variant
└── .env                      # Add Slack tokens
```

**Next Steps**:
1. Decide on Option A vs Option B (A is faster to ship, B is cleaner separation)
2. Set up Slack app in workspace
3. Implement bot listener
4. Test with real account

## Critical Design Patterns

1. **External Storage Pattern**: Activities pass doc URLs (not content) to avoid 2MB Temporal limit
2. **Two-Phase Processing**: Summarize → Synthesize (compresses 30 calls to ~9K words → 800 word brief)
3. **Idempotency**: Activities check for existing content before writing (safe to retry)
4. **Parallel Execution**: Call summarization runs concurrently (30 calls processed in ~60-90 seconds)
5. **LLM Filtering**: Uses Claude to disambiguate company names and folder matches (handles "Acme Corp" vs "acme.io")

## Key Files

- [activities.py](activities.py) - 7 Temporal activities (1100 lines)
- [workflow_intelligence.py](workflow_intelligence.py) - Main workflow orchestration (164 lines)
- [worker.py](worker.py) - Temporal worker process
- [trigger_intelligence.py](trigger_intelligence.py) - Manual workflow trigger
- [.claude/skills/](..claude/skills/) - API reference docs for Gong and Google APIs
