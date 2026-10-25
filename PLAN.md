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
- [x] [Ben] Write the 20 retrieval questions with known clause IDs (`evals/retrieval.yaml`;
      citation format tidied by CC, R08 replaced: Ben to review)

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
- [x] [Ben] Eyeball 20 chunks against the PDFs: `data/chunks/review_sample.md`
      (fixes in PR #15: three-level headings, captions, merged cells; 656 chunks)
- [ ] [Ben] Follow-ups from the review: see "Ben: open checks" in `docs/HANDOVER.md`
- **Done when:** any clause prints by ID with heading path and page; tests pass

### Phase 2: Index
- [x] [CC] `index/schema.py`, `index/rebuild.py` (`python -m index.rebuild`), embed `heading_path + text`
      (654 documents in `dmrb-clauses`)
- [x] [CC] Semantic ranker config, `evals/recall.py` (recall@5, MRR; keyword/vector/hybrid/semantic)
- [x] [Ben] Decide the embedding text; if recall@5 < ~80%, fix chunking before moving on
      (semantic recall@5 100%, MRR 0.83: kept `heading_path + text`)
- **Done when:** recall@5 is measured and in the README (done 2026-10-25)

### Phase 3: RAG baseline
- [ ] [Ben] Write the system prompt (rules in guide p.5)
- [x] [CC] `agent/rag.py`: hybrid top-8 → prompt → `Answer` (structured output); runs once
      `agent/prompts/system.md` exists
- [x] [CC] `agent/citations.py`: cited IDs must be in retrieved chunks, else retry once then escalate
- [ ] [CC] Foundry built-in variant: agent + `AzureAISearchTool`, `vector_semantic_hybrid`
- [ ] [Ben] Judge the first 10 answers yourself
- **Done when:** 10 cited answers; "speed limit on a motorway?" → "not covered"

## Weekdays
- [ ] [Ben] Golden set to 30–50 rows

## Weekend 2: Phases 4, 6, minimal 7

### Phase 4: Agent loop
- [ ] [Ben] Write tool descriptions
- [ ] [CC] Tools: `search_standards`, `get_clause`, `get_table`, `check_limit`, with arg models
      (`get_clause`, `get_table`, `check_limit` done; `search_standards` wraps `index/search.py`)
- Calculation tools from charts and equations (catalogue and formulas: `docs/CHARTS.md`).
  Build tier by tier; drop any that prove too hard.
  - [ ] [Ben] Confirm tier 1 formulas and band edges (Fig 9.23N2 sides, CG 501 flow-path formulas);
        full list of Ben's chart checks in `docs/HANDOVER.md`
  - [x] [CC] Tier 1, CD 109 equations: bendiness, Ac (Eq 2.2a/b), VISI (Eq 2.8.1, 2.8.2),
        superelevation (Eq 4.2), transition length (Eq 4.13) (`agent/tools/equations.py`)
  - [ ] [CC] Tier 1, after Ben's confirmation: flow path length/gradient (CG 501 7.5.3),
        overtaking section from V²/R (Fig 9.23N2)
  - [ ] [CC] Chart data format (`data/charts/*.yaml`), interpolation, out-of-range and marginal flags
  - [ ] [Ben] Digitise CG 501 Fig 7.5.4 and 7.5.1, and CD 109 Fig 2.1 (WebPlotDigitizer)
  - [ ] [CC] Tier 2 chart tools: Fig 7.5.4, Fig 7.5.1, design speed from Fig 2.1 + Table 2.3
  - [ ] [CC] Flagship chain: bendiness/VISI/verge/road type -> design speed -> Table 2.10 values
  - [ ] [Ben] Decide tier 3: CD 123 Fig 2.3.1 junction type (build?), Fig 9.23N2 verge inset
  - [x] [CC] CG 501 Fig 2.5 flowchart: indexed as figure chunk `CG501-Figure-2.5` (no tool)
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
