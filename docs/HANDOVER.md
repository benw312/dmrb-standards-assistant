# Handover: where the project stands

Read this first in a new Claude Code session, then `PLAN.md` (checklist) and `DECISIONS.md`
(why things are the way they are). Last updated 2026-10-22, end of Phase 1.

## Status

| Phase | State |
|---|---|
| 0. Setup | Done. Azure resources live, smoke test 4/4 (`uv run python -m core.smoke_test`) |
| 1. Ingest | Done. 4 standards parsed (313 pages, ~$3.13) and chunked into 654 chunks |
| 2. Index | Index built (654 documents). `evals/recall.py` ready; waiting on Ben's 20 retrieval questions |
| 3–7 | Not started. `agent/answer.py`, `agent/citations.py`, `agent/tools/check_limit.py` already built and tested for Phases 3–4 |

Open PRs at handover: #12 (chunker) and the PR adding this document.

## Azure as built

Personal pay-as-you-go subscription (the free trial gave 0 quota for most models). £20/month
budget alert. All resource names are below; none are secrets, and auth is keyless.

| Resource | Name | Region | Notes |
|---|---|---|---|
| Resource group | `rg-dmrb-assistant` | Sweden Central | `scripts/azure_teardown.sh` deletes everything |
| Foundry resource | `fdy-dmrb-bw01` | Sweden Central | project `dmrb-assistant` (default project) |
| Chat model | `gpt-5.4-mini` | Data Zone Standard (EU) | 50K TPM. Global Standard had 0 quota |
| Embeddings | `text-embedding-3-small` | Global Standard | 1536 dimensions |
| AI Search | `srch-dmrb-bw01` | **UK South** | Free tier, RBAC on. Sweden Central was out of Search capacity |
| Document Intelligence | `di-dmrb-bw01` | Sweden Central | S0 (F0 only reads 2 pages per document) |
| Spare | `bwallbridge-8524-resource` | Sweden Central | Empty Foundry resource made by the portal; safe to delete |

Not available yet: Claude Haiku 5.5 / Opus 5.5 and full-size `gpt-5.4` have 0 quota in every
region. Ben parked the quota request; use what's deployed until it becomes a problem.
Not done yet: connecting the Search service to the Foundry project (needed only for the Phase 3
built-in search tool).

`.env` (never read by Claude) holds: `AZURE_AI_PROJECT_ENDPOINT`, `CHAT_DEPLOYMENT`,
`EMBEDDING_DEPLOYMENT`, `AZURE_SEARCH_ENDPOINT`, `AZURE_SEARCH_INDEX` (`dmrb-clauses`),
`AZURE_DOCINTEL_ENDPOINT`. `core/settings.py` validates them without ever printing values.

## Commands

```bash
uv run pytest                                  # offline tests (CI runs these)
uv run python -m core.smoke_test               # live check of all four Azure services
uv run python -m ingest.parse                  # PDFs -> data/parsed/ (cached; billed per page)
uv run python -m ingest.chunk                  # data/parsed/ -> data/chunks/*.jsonl + summary
uv run python -m ingest.chunk --show CD109-2.2 # print one chunk with heading path and page
uv run python -m index.rebuild                 # embed chunks, recreate the index (~1 min, <1p)
uv run python -m evals.recall                  # recall@5 per mode on evals/retrieval.yaml
uv run python -m evals.recall --modes semantic --k 8
```

## Pipeline

```
data/pdfs/*.pdf  (git-ignored; listed in data/sources.yaml)
  -> ingest/parse.py      Document Intelligence prebuilt-layout; footer checked against
                          sources.yaml before paying; cached by PDF SHA-256
  -> data/parsed/         <DOC>.md, <DOC>.layout.json, <DOC>.meta.json (git-ignored)
  -> ingest/layout.py     reading order from line geometry (DI Markdown order is unreliable)
  -> ingest/chunk.py      clause / table / glossary chunks, GG 101 rules from ingest/conventions.py
  -> data/chunks/         <DOC>.jsonl of ingest/models.Chunk (git-ignored)
  -> index/rebuild.py     embeds heading_path + text, recreates index `dmrb-clauses`
  -> index/search.py      keyword | vector | hybrid | semantic (production: semantic)
  -> evals/recall.py      recall@k and MRR per mode, Markdown table for the README
```

Chunk IDs: `CD109-5.3.1` (clause), `CD109-Table-3.5`, `CD109-Glossary`, annex `CD109-E/1.1`,
appendix `CD123-A2.1`. Long clauses split into `CD109-5.3.1#2`; `citation_id` stays the clause.
Answers cite inline as `[CD 109 cl. 5.3.1]`, `[CD 109 Table 3.5]`, `[CD 109 Glossary]`.

## Working agreements

- **Git:** `main` is protected (PR + green CI, linear history, squash merge). Claude Code
  branches, commits with conventional messages, opens a PR with summary and test plan; **Ben
  reviews and merges**. Stage named files, never `git add -A` (it once committed a PDF).
- **After writing code:** run a reviewer subagent, fix its findings with regression tests, then
  open the PR. Every review so far has found real bugs.
- **Billable `az` commands:** Claude prints them; Ben runs them. Read-only `az` checks are fine.
- **Ground truth is Ben's:** `evals/golden.jsonl` and `evals/retrieval.yaml` are written by
  Ben only (Claude may write the code that loads them).
- **Licence:** DMRB is Crown copyright under the Open Government Licence v3.0. Short attributed
  extracts are fine as test fixtures; PDFs and parsed text stay out of git.
- In Claude Code, `!` before a command runs it in the session. In a normal terminal, leave it off.
- A safety hook blocks any Bash command containing `-n` next to `git commit` (it reads it as
  `--no-verify`), so keep `bash -n` checks in a separate command.

## Gotchas learned the hard way

- The **project** endpoint's `/openai/v1` serves chat but returns 404 for embeddings; embeddings
  use the resource-level endpoint (`Settings.openai_v1_endpoint`).
- Models go "Deprecated" ~12 months after launch and new subscriptions can't deploy them, so the
  guide's `gpt-4.1-mini` was unavailable. Check the model catalog, not the guide.
- In the portal, "default settings aren't available" means the default deployment type has no
  quota: use **Customize** and pick a type that does (never anything "Provisioned").
- DI attaches only ~1 in 3 table captions and sometimes the wrong one; the line above wins.
- DI tags page footers inconsistently; `ingest/layout.py` also drops footer text by regex.

## Adding another standard

1. Download the PDF to `data/pdfs/`; add it to `data/sources.yaml` (code, title, version and
   date exactly as printed in the PDF's page footer, url, filename).
2. `uv run python -m ingest.parse "CD 116"`: refuses to pay if the footer doesn't match.
3. `uv run python -m ingest.chunk "CD 116"` and read the summary line. It works for current-
   format DMRB documents (GG 101 style). Bring it to Claude Code if any of these look off:
   - **warnings > 0**: a clause whose verb contradicts its GG 101 numbering level. This has
     caught every clause-boundary bug so far.
   - requirement + advice counts far from the PDF's own clause numbering;
   - many "uncaptioned tables skipped" pages that hold real content;
   - "lines outside clauses" much higher than ~250 (the front matter).
   Superseded old-format standards (e.g. TD 9/93), MCHW and IANs won't fit these rules.

## Next steps

**Ben**
- Write 20 retrieval questions in `evals/retrieval.yaml`: copy a block, edit it (the file's
  comments show the layout). `expected` lists every clause or table that counts as a correct
  hit (any one is enough), in citation form: `CD 109 cl. 4.5`, `CD 109 Table 3.5`,
  `CD 123 cl. E/1.1`. The loader normalises them and names any bad entry. Mix lookups, table
  values, wording that differs from the standard ("banking on a bend"), and at least one
  question per standard.
- Check the 20 chunks in `data/chunks/review_sample.md` against the PDFs.
- Add each standard's document page URL to `data/sources.yaml`.
- Later: `evals/golden.jsonl` (30–50 questions with answers) for Phase 6.

**Claude Code: Phase 2**
1. `index/schema.py`: one field per Chunk attribute; `text` searchable; `doc_code`,
   `citation_id`, `kind`, `requirement_type` filterable; 1536-dim vector; semantic config.
   Document key must be an encoding of the chunk ID (`/` and `#` aren't valid key characters).
2. `index/rebuild.py` (`python -m index.rebuild`): embed `heading_path + text`, upload in
   batches of 100, recreate the index from `data/chunks/`.
3. `evals/recall.py`: load `evals/retrieval.yaml`, run hybrid + semantic search, report
   recall@5 per question and overall; README gets the number. Below ~80%: fix chunking or the
   embedding text before Phase 3.
