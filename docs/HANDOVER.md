# Handover: where the project stands

Read this first in a new Claude Code session, then `PLAN.md` (checklist) and `DECISIONS.md`
(why things are the way they are). Last updated 2026-10-26: Phases 2–4 built, two charts machine-read.

## Status

| Phase | State |
|---|---|
| 0. Setup | Done. Azure resources live, smoke test 4/4 (`uv run python -m core.smoke_test`) |
| 1. Ingest | Done. 4 standards parsed (313 pages, ~$3.13) and chunked into 656 chunks; Ben's 20-chunk review fixed (PR #15) |
| 2. Index | Done. 657 documents; semantic recall@5 100%, MRR 0.83 on 20 questions (README) |
| 3. RAG | Done. `agent/rag.py`, 9/10 on the hand check (`evals/rag_check.md`); prompt is a Claude draft |
| 4. Agent | Built. `agent/loop.py` (11 tools, 6 steps, traces) and `agent/graph.py` (LangGraph, pause-for-review); 9/10 first run (`evals/agent_check.md`). `check_chart` reads CG 501 Fig 7.5.1 / 7.5.4 (machine-read drafts, Ben to check) |
| 5. Review | Escalation flag only; LangGraph pause is the mechanism for a queue |
| 6. Eval | Scorer built (`evals/golden.py`); waiting on Ben's `evals/golden.yaml` |
| 7. Ship | REST API (`app/api.py`) and Streamlit demo (`app/demo.py`) built; demo GIF is Ben's |

Open PRs (stacked, merge in order): #20 retrieval → #21 Phase 3 prompt → #22 agent loop → #23 LangGraph → #24 API.

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
uv run python -m ingest.digitise               # chart images -> data/charts/<id>.yaml + check overlays
uv run python -m evals.recall                  # recall@5 per mode on evals/retrieval.yaml
uv run python -m evals.recall --modes semantic --k 8
uv run python -m agent.rag "<question>"        # Phase 3 RAG baseline
uv run python -m agent.loop "<question>"       # Phase 4 agent (trace in traces/)
uv run python -m agent.graph "<question>"      # same agent on LangGraph; --mermaid draws it
uv run uvicorn app.api:app                     # REST API, docs at http://127.0.0.1:8000/docs
uv run python -m evals.golden                  # score rag/agent/graph on evals/golden.yaml
uv run streamlit run app/demo.py               # demo UI
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
- **Ground truth is Ben's:** `evals/golden.yaml` and `evals/retrieval.yaml` are written by
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

### Ben: open checks

**Claude drafts standing in for Ben's inputs** (added 2026-10-25 so work wasn't blocked; each
is marked `CLAUDE DRAFT` or `CLAUDE EDIT` in the file itself)
- [ ] `evals/retrieval.yaml`: citations written in full (`CD 123 cl. 5.8`, not `5.8`); figures
  3.2b, 8.7N, 6.8 and Equation 4.13 dropped (drawings aren't indexed; the clause that cites
  them stands in); `CD 109 Table 5.1` added to R02 (it holds the gradient values); R08 was a
  duplicate of R06 and is now a CG 501 climate-change uplift question. Check R08's clauses.
- [ ] `agent/prompts/system.md`: Claude's draft of your system prompt (GG 101 verbal forms,
  departures vs justifications, version first, no "what is covered" in refusals). Rewrite it
  in your own words; you'll be asked about it.
- [ ] `evals/rag_check.md`: Claude ran and judged the 10-answer check (9/10). Re-judge as the
  engineer, especially q4 (cl. 6.21 relevance) and q7.
- [ ] Tool descriptions in `agent/tools/registry.py` (the text the model reads to choose a
  tool), and `evals/agent_check.md` (agent on the same 10 questions). Read 2-3 traces in
  `traces/` to see how it searches.
- [ ] `.claude/settings.json` still denies Claude edits to `evals/golden.jsonl`; the golden set
  is now `evals/golden.yaml` (YAML, like the retrieval questions). Claude didn't change its own
  permissions: swap the two deny lines to `golden.yaml` (and add `retrieval.yaml` if you want
  it locked too, now its tidy-up is done).
- [ ] Scoring rule to confirm (`evals/scoring.py`): when your golden answer is `departure:
  false`, an answer that doesn't mention a departure (null) counts as correct. Agree?
- [ ] `agent/graph.py` (LangGraph version) and `docs/ARCHITECTURE.md`: read both so you can
  walk an interviewer through them. `uv run python -m agent.graph --mermaid` draws the graph.

Ordered roughly by what unblocks the most. Tick them off here (or in PLAN.md) as you go.

**Blocking Phase 2 sign-off**
- [x] Write 20 retrieval questions in `evals/retrieval.yaml`. Copy a block and edit it; the
  file's comments show the layout. `expected` = what you'd cite in your own answer
  (`CD 109 cl. 4.5`, `CD 109 Table 3.5`, `CD 123 cl. E/1.1`); any one counts as a hit. If the
  answer needs a clause *and* the table it points to, list both. Note multi-hop questions in
  `notes`. Then run `uv run python -m evals.recall` and paste the table into the README.
- [ ] If semantic recall@5 < 80%: decide whether to prefix the embedding text with the
  document code and title (see DECISIONS.md, "Search index").

**From the chunk review (PR #15)**
- [ ] Spot-check the fixed chunks: `uv run python -m ingest.chunk --show CD123-7.7` (also
  `CD123-Table-5.18a`, `CD123-Table-5.18b`, `CG501-Table-5.2`).
- [ ] CD 109 cl. 5.2 was left unticked with no note. It matches the PDF word for word; say what
  looked wrong, if anything.
- [ ] CD 123 Table 5.18b row 1 prints "≤ 450, > 4% gradient" where every other row says "≥".
  Typo in the standard? (Affects how a future tool or answer should read it.)

**For the Phase 4 chart tools (`docs/CHARTS.md`)**
- [ ] Confirm the tier 1 formulas are acceptable to cite, especially CG 501 flow path
  `G = √(S² + C²)` and `L = W·√(1 + (S/C)²)` (they reproduce Fig 7.5.3N2a/b, but CG 501
  doesn't print them).
- [ ] Fig 9.23N2: which section a value exactly on a V²/R boundary (1.25, 3.53, 10, 20) falls
  in, and what applies above V²/R = 20.
- [ ] Decide tier 3: build CD 123 Fig 2.3.1 (junction type by flows, flagged approximate)? How
  is the Fig 9.23N2 verge-width inset read, or drop it?
- [ ] **Check the machine-read charts** (no digitising needed for these two): run
  `uv run python -m ingest.digitise`, open `data/charts/check/CG501-Figure-7.5.4.png` and
  `...-7.5.1.png`, and compare the red crosses and values with the figures. If right, set
  `status: checked` and `checked_by: Ben Wallbridge` in each `data/charts/<id>.yaml`.
  Until then every `check_chart` answer is flagged for review.
- [ ] Confirm the reading tolerances in `data/charts/specs.yaml` (CLAUDE DRAFT: 0.03% on
  Fig 7.5.4, 0.02% on Fig 7.5.1). Within that of the line, a result is "marginal".
- [ ] **CD 109 Fig 2.1** is machine-read too: run `uv run python -m ingest.digitise_bands`,
  open `data/charts/check/CD109-Figure-2.1.png` (fitted lines in red, NOTE 2 point in blue)
  and check the crossing table in `data/charts/design_speed/CD109-Figure-2.1.yaml`. Before
  the design speed tool, three reading rules:
  1. Lc 0, 5 and 10 aren't drawn left of Ac 6.6, where only single carriageways can be (dual
     Ac = 6.6 + B/10). Does a single carriageway never have Lc below 15, or is that region
     simply not covered? Refuse those inputs?
  2. Where a line is above the top of the chart (Lc 0 below Ac 8.3, Lc 5 below 6.6): 120A?
  3. Lc between the drawn lines (e.g. 12 from Table 2.3): interpolate, as the tool does now?
  Also noticed: the vertical divider is drawn at Ac 6.49, not 6.6, and the Lc 0 line fits
  about 4% steeper than the others (check it on the overlay).
- [ ] Superelevation at exactly Table 2.10's "minimum R with adverse camber": the tool says
  camber (cl. 4.1 says "less than", 4.1.1 "greater than", so equality is open). Agree?
- [ ] Harmonic mean visibility warns below 20 readings (2 km at 100 m spacing, cl. 2.6/2.6.1).
  Is that the right trigger? cl. 2.6.1 says both directions, so it may want 40 (20 each way).
- [ ] **Superelevation just below the 2.5% radius** (found in the equation check,
  `evals/equation_check.md`): Equation 4.2 gives *less* than 2.5% on a band of radii where a
  larger radius gets 2.5% under cl. 4.1: 100 kph R 1414–1439 m (2.46%), 70 kph R 693–719 m
  (2.41%), 50 kph R 354–359 m (2.46%). Floor it at 2.5%, or report Equation 4.2 as printed?
- [ ] cl. 2.8.3 gives typical VISI values for preliminary route analysis (700 / 500 / 300 m).
  Worth adding to the visibility tool as an option?
- [ ] Note: CD 109 cl. 9.24 and 6.9 cite "Figure 9.24N2", which doesn't exist (it's 9.23N2).
  Worth knowing if you report errata.

**Other**
- [ ] Add each standard's document page URL to `data/sources.yaml` (chunk links currently
  point at the site root).
- [x] Phase 3: `agent/prompts/system.md` and the 10-answer check (Claude drafts, see top).
- [ ] `evals/golden.yaml` (30–50 questions with answers) for Phase 6. The template's comments
  show the layout; the scorer is built, so `uv run python -m evals.golden` gives the
  RAG vs agent vs graph table as soon as there are questions. You can reuse the 20
  retrieval questions and the 10 check questions as a start.

### Claude Code: next

1. When `evals/golden.yaml` has questions: run `evals.golden`, put the table in the README,
   then the `azure-ai-evaluation` judges over `evals/results/*.jsonl`.
2. Demo: link clause badges to the PDF page once `sources.yaml` has document URLs.
3. After Ben's confirmations: CG 501 flow-path and Fig 9.23N2 tools; CD 109 Fig 2.1 and the
   design speed chain (bendiness -> Ac -> Fig 2.1 with Table 2.3's Lc -> design speed).
4. Foundry built-in search-tool variant once the Search connection exists in Foundry.
