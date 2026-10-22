# DMRB standards assistant

Agent that answers DMRB questions with clause citations, built on Azure AI Foundry.
Plan: PLAN.md. Decisions log: DECISIONS.md (append one entry per design choice).

## Stack
Python 3.12, uv, azure-ai-projects 2.x, azure-search-documents, azure-ai-documentintelligence,
azure-ai-evaluation, Pydantic, pytest. Auth: DefaultAzureCredential, never API keys in code.

## Rules
- The model never does arithmetic: numeric checks go through `check_limit()`.
- Every answer cites clause IDs; citations are verified against retrieved chunks in code.
- Never read, print or commit `.env`.
- Never run `az` commands that create or scale billable resources. Print them for me to run
  (see `scripts/azure_setup.sh`).
- Never edit `evals/golden.jsonl`. Ground truth is written by me.
- Write a test with every module; run `uv run pytest` before saying a task is done.
- Explain non-obvious code in a short comment aimed at a reviewer.
