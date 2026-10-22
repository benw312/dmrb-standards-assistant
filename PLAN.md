# Plan: DMRB standards assistant

Source: `docs/DMRB Standards Assistant on Azure AI Foundry — Build Guide.pdf`.
Pace: **two-weekend sprint**. Scope: **CD 109, CD 123, CG 501** first, widen once the loop works.

Legend: **[Ben]** = do yourself, **[CC]** = Claude Code builds it, you review and can explain every line.

---

## Before weekend 1 (one evening)

### Phase 0: Azure setup
- [x] [CC] Repo skeleton, `pyproject.toml` (uv, Python 3.12), `.gitignore`, `.env.example`,
      `.claude/settings.json` lockdown, `scripts/azure_setup.sh`, `scripts/azure_teardown.sh`
- [x] [Ben] Personal Azure account (upgraded to pay-as-you-go: trial quota was 0)
- [x] [Ben] Cost Management → Budgets: £20/month, alerts at 50/80/100%
- [x] [Ben] `brew install azure-cli && az login`
- [x] [Ben] `azure_setup.sh resources` (Search in UK South: Sweden was full)
- [x] [Ben] Foundry resource `fdy-dmrb-bw01` + project `dmrb-assistant`, Sweden Central;
      `gpt-5.4-mini` (Data Zone Standard) and `text-embedding-3-small` deployed
- [ ] [Ben] Foundry: connect the Search service (needed for the Phase 3 built-in search tool)
- [x] [Ben] `azure_setup.sh roles`
- [x] [Ben] `.env` filled in
- [x] [CC] `python -m core.smoke_test`: chat, embeddings, Search, Doc Intelligence all pass
- [ ] [Ben] Claude Haiku 5.5 / Opus 5.5 (+ gpt-5.4) quota request submitted
- [x] [CC] Public GitHub repo, branch protection, PR template, Dependabot
- [x] [CC] GitHub Actions CI: ruff + pytest (no Azure calls)

### Data prep
- [x] [Ben] CD 109 and GG 101 downloaded; licence checked (OGL v3.0)
- [x] [Ben] Download CD 123 and CG 501
- [ ] [Ben] Add document page URLs to `data/sources.yaml`
- [ ] [Ben] Start `evals/golden.jsonl` (keep going on weekdays, 20-minute sessions).
      Row: `query, ground_truth, expected_clauses, expected_departure, type`
      (types: lookup, multi_hop, departure, out_of_scope). Target 30–50.
- [ ] [Ben] Write the 15 retrieval questions with known clause IDs (`evals/retrieval.jsonl`)

## Weekend 1: Phases 1–3 → cited answers, recall@5 measured

### Phase 1: Ingest
- [x] [CC] `ingest/sources.py`: load and validate `sources.yaml`
- [x] [CC] `ingest/conventions.py`: GG 101 rules (verbal forms, clause numbering, NOTEs,
      doc codes, annex prefixes, PDF footer)
- [x] [CC] Check `sources.yaml` version against the PDF footer during ingest
- [x] [CC] `ingest/parse.py`: Doc Intelligence `prebuilt-layout` → Markdown, cached in `data/parsed/`
      (4 standards, 313 pages, ~$3.13; footer checked against sources.yaml before paying)
- [x] [CC] `ingest/models.py`: `Chunk` Pydantic model (+ `refs`, `requirement_type`)
- [x] [CC] `ingest/layout.py` + `ingest/chunk.py`: reading order from line geometry; clause per
      chunk, NOTEs attached, tables and glossary as own chunks; ~800-token cap (654 chunks)
- [x] [CC] Requirement strength (must/shall/should/may/note) and table/clause cross-references
- [x] [Ben] Decide how NOTEs and tables attach (tables own chunks; glossary only)
- [ ] [Ben] Eyeball 20 chunks against the PDFs: `data/chunks/review_sample.md`
- **Done when:** any clause prints by ID with heading path and page; tests pass

### Phase 2: Index
- [ ] [CC] `index/schema.py`, `index/rebuild.py` (`python -m index.rebuild`), embed `heading_path + text`
- [ ] [CC] Semantic ranker config, `evals/recall.py` (recall@5)
- [ ] [Ben] Decide the embedding text; if recall@5 < ~80%, fix chunking before moving on
- **Done when:** recall@5 is measured and in the README

### Phase 3: RAG baseline
- [ ] [Ben] Write the system prompt (rules in guide p.5)
- [ ] [CC] `agent/rag.py`: hybrid top-8 → prompt → `Answer` (structured output)
- [ ] [CC] `agent/citations.py`: cited IDs must be in retrieved chunks, else retry once then escalate
- [ ] [CC] Foundry built-in variant: agent + `AzureAISearchTool`, `vector_semantic_hybrid`
- [ ] [Ben] Judge the first 10 answers yourself
- **Done when:** 10 cited answers; "speed limit on a motorway?" → "not covered"

## Weekdays
- [ ] [Ben] Golden set to 30–50 rows

## Weekend 2: Phases 4, 6, minimal 7

### Phase 4: Agent loop
- [ ] [Ben] Write tool descriptions
- [ ] [CC] Tools: `search_standards`, `get_clause`, `get_table`, `check_limit`, with arg models
- [ ] [CC] Responses-API function-calling loop: max 6 steps, time budget, JSONL traces, escalate on limit
- [ ] [Ben] Read traces, decide what to fix

### Phase 5 (minimal): single `needs_human_review` flag
- Escalate on: departure (possible), low confidence, citation check failed, conflicting versions,
  step limit, out of scope. Note in README that the review queue is future work.

### Phase 6: Evaluation
- [ ] [CC] `evals/metrics.py`: recall@5, citation precision, departure accuracy, refusal accuracy,
      cost/latency per question
- [ ] [CC] `azure-ai-evaluation` runner (Groundedness, Relevance, Tool Call Accuracy, Task Adherence)
- [ ] [Ben] Hand-check a judge sample; report agreement
- [ ] [CC] Experiment table: RAG vs agent, small vs large model (Claude optional)
- [ ] [CC] CI gate on departure accuracy

### Phase 7 (minimal): Ship
- [ ] [CC] Streamlit app: question box, clause badges → PDF page, review badge, trace expander
- [ ] [CC] README draft + architecture diagram
- [ ] [Ben] README design decisions + limitations in your own words; record demo GIF
- [ ] [Ben] CV line with the real number; LinkedIn post

## Later (if time)
Full Phase 5 SQLite review queue, Container Apps deploy, MCP server for the tools, widen to more standards.

## Habits
- End of each session: CC appends to `DECISIONS.md`; Ben rewrites it in his own words.
- Small commits with clear messages. Tear down (`scripts/azure_teardown.sh`) when idle for weeks.
