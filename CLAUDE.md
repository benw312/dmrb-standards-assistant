# DMRB standards assistant

Agent that answers DMRB questions with clause citations, built on Azure AI Foundry.
Plan: PLAN.md. Decisions log: DECISIONS.md (append one entry per design choice).
**New session? Read docs/HANDOVER.md first**: status, Azure resources, commands, working
agreements and next steps.

## Stack
Python 3.12, uv, azure-ai-projects 2.x, azure-search-documents, azure-ai-documentintelligence,
azure-ai-evaluation, Pydantic, pytest. Auth: DefaultAzureCredential, never API keys in code.

## Rules
- The model never does arithmetic: numeric checks go through `check_limit()`.
- Every answer cites clause IDs; citations are verified against retrieved chunks in code.
- Never read, print or commit `.env`.
- Never run `az` commands that create or scale billable resources. Print them for me to run
  (see `scripts/azure_setup.sh`).
- Never edit `evals/golden.yaml` or `evals/retrieval.yaml`. Ground truth is written by me.
- Write a test with every module; run `uv run pytest` before saying a task is done.
- Explain non-obvious code in a short comment aimed at a reviewer.
- Work on a branch and open a PR for me to review and merge; never push to `main`. Stage
  named files, not `git add -A`.
- After writing code, run a reviewer subagent and fix what it finds before opening the PR.
