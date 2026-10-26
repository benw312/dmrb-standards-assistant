# Handover: where the project stands

Read this first in a new Claude Code session, then `PLAN.md` (checklist) and `DECISIONS.md`
(why things are the way they are). Last updated 2026-10-22, end of Phase 1.

## Status

| Phase | State |
|---|---|
| 0. Setup | Done. Azure resources live, smoke test 4/4 (`uv run python -m core.smoke_test`) |
| 1. Ingest | Done. 4 standards parsed (313 pages, ~$3.13) and chunked into 656 chunks; Ben's 20-chunk review fixed (PR #15) |
| 2. Index | Done. 657 documents; semantic recall@5 100%, MRR 0.83 on 20 questions (README) |
| 3–7 | Not started. `agent/answer.py`, `agent/citations.py`, `agent/tools/check_limit.py` already built and tested for Phases 3–4. Phase 4 chart tools planned in `docs/CHARTS.md` |

Open PRs: #16 (chart catalogue). Last updated 2026-10-22.

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
- [ ] Digitise CG 501 Fig 7.5.4 and Fig 7.5.1, and CD 109 Fig 2.1, with WebPlotDigitizer
  (how-to in `docs/CHARTS.md`). Not needed until the tier 2 tools are built.
- [ ] Superelevation at exactly Table 2.10's "minimum R with adverse camber": the tool says
  camber (cl. 4.1 says "less than", 4.1.1 "greater than", so equality is open). Agree?
- [ ] Harmonic mean visibility warns below 20 readings (2 km at 100 m spacing, cl. 2.6/2.6.1).
  Is that the right trigger?
- [ ] Note: CD 109 cl. 9.24 and 6.9 cite "Figure 9.24N2", which doesn't exist (it's 9.23N2).
  Worth knowing if you report errata.

**Other**
- [ ] Add each standard's document page URL to `data/sources.yaml` (chunk links currently
  point at the site root).
- [x] Phase 3: `agent/prompts/system.md` and the 10-answer check (Claude drafts, see top).
- [ ] Later: `evals/golden.jsonl` (30–50 questions with answers) for Phase 6.

### Claude Code: next

1. When the retrieval questions land: run `evals.recall`, fix chunking or the embedding text
   if semantic recall@5 < 80%, put the number in the README.
2. Phase 3: `agent/rag.py` is built; once Ben's prompt exists, run the 10-question check
   and the Foundry built-in search-tool variant (needs the Search connection in Foundry).
3. Phase 4: CD 109 equation tools and `get_clause`/`get_table` are built. Next: the CG 501
   flow-path and Fig 9.23N2 tools after Ben's confirmations, then the chart data format and
   tier 2 once charts are digitised.
4. Phase 4 agent loop: `search_standards` tool over `index/search.py`, then the Responses-API
   function-calling loop (max 6 steps, time budget, JSONL traces) using the tools built so far.
