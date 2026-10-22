# Decisions log

One entry per design choice: what, alternatives, why. Newest at the bottom.

---

## 2026-10-22: Region: Sweden Central
- **Alternatives:** UK South.
- **Why:** Broader Azure OpenAI model availability and quota. Everything lives in one resource
  group, so moving later is a teardown and rebuild.

## 2026-10-22: Document Intelligence on S0, not F0
- **Alternatives:** F0 free tier.
- **Why:** F0 only analyses the first two pages of each document; DMRB standards are 50–100 pages.
  Layout parsing costs about $10 per 1,000 pages, and parsed Markdown is cached so each PDF is
  paid for once.

## 2026-10-22: Keyless auth everywhere
- **Alternatives:** API keys in `.env`.
- **Why:** `DefaultAzureCredential` with RBAC roles (`scripts/azure_setup.sh roles`). No secrets to
  leak from a public repo, and it matches how a production deployment would authenticate.

## 2026-10-22: Don't commit DMRB PDFs or parsed text
- **Alternatives:** Commit `data/parsed/` for reproducibility.
- **Why:** DMRB is Crown copyright. The repo commits code and `sources.yaml` metadata, and links to
  standardsforhighways.co.uk.
- **Update (same day):** checked the PDFs: DMRB is published under the Open Government Licence
  v3.0, which allows reuse with attribution. Short extracts are now committed as test fixtures,
  with attribution. PDFs stay out of git anyway (size, and `sources.yaml` pins the version);
  `*.pdf` is ignored repo-wide after one was committed by accident in #5.

## 2026-10-22: `sources.yaml` fails to load while any version is TODO
- **Why:** The version appears in every citation. A placeholder would pass silently into answers.

## 2026-10-22: check_limit design
- **Units:** convert within a dimension (mm/m/km, %, km/h) using an explicit table; mixing
  dimensions raises an error the agent can read and correct. *Alternative:* exact unit match only,
  but then the model would have to convert, which is arithmetic. **mph is excluded**: its factor
  makes km/h -> mph inexact, and DMRB design speeds are in km/h. Power-of-ten factors only.
- **Comparisons:** `<=`, `<`, `>=`, `>` and `between`. `between` is inclusive at both ends because
  DMRB ranges ("2.5% to 5%") include their end values. Margin is signed headroom to the nearest bound.
- **Exactness:** `Decimal` throughout, and floats from the model's JSON go through `str()` first,
  so a value exactly on a limit is judged exactly. No tolerance band: rounding is an engineering
  judgement, not something to hide in code. Inputs are capped at 15 significant digits and magnitudes of 1e±9,
  and arithmetic runs in a context that raises on any rounding, so a verdict can never be rounded.
  (Found in code review: a 29-digit value rounded to the limit under Python's default context.)

## 2026-10-22: GitHub workflow
- Public repo from day one; `main` is protected (PRs only, CI must pass, linear history,
  squash merge). Claude Code opens PRs, Ben reviews and merges.
- Actions pinned to commit SHAs (supply-chain hygiene); Dependabot keeps them and uv deps current.

## 2026-10-22: Citation verification rules
- **ID format:** chunk IDs are `CD109-5.3.1`; answers cite inline in one rigid form,
  `[CD 109 cl. 5.3.1]`, with lists allowed (`[CD 109 cl. 5.3.1, 5.3.2; CD 123 cl. 3.4]`). Both
  normalise to the same ID, defined in one place (`agent.citations.chunk_id`).
- **Strict by default:** a reference the checker can't read is one it can't verify, so it fails
  rather than being skipped. That covers bracket groups that don't parse (e.g. `[CD 109 Table 5.3]`)
  and clause/table/figure numbers in prose outside brackets. *Alternative:* a lenient parser for
  every phrasing, which grows forever and still misses some. Code review showed the lenient version
  let hallucinated clauses through (`[CD 109 cl. 5.3.1, 5.9.9]` only checked the first).
- **Exception:** double-quoted text is skipped, because quoted DMRB wording often says "in
  accordance with Table 5.3". *Follow-up:* verify quotes appear verbatim in a retrieved chunk.
- **What else fails the gate:** a cited clause that was never retrieved (hallucination); a clause
  cited in the text but missing from `citations`, or the reverse; an answer claiming a requirement
  with no citation; a `not_covered` refusal that cites clauses.
- **Tables:** cite the clause that introduces the table for now. Table IDs are decided in Phase 1.
- **Not caught here:** an uncited factual claim inside a refusal. That's a semantic check, left to
  the evaluation set (refusal accuracy) and the LLM-judge groundedness metric.
- **Failure reasons are plain sentences** so they can go straight into the retry prompt.

## 2026-10-22: Encode GG 101 drafting conventions as code (`ingest/conventions.py`)
GG 101 defines how every DMRB document is written, so the parser follows it instead of
guessing. Each rule cites its GG 101 source.
- **Verbal forms:** `must` (statutory, never varied), `shall` (requirement: departure or an
  explicitly permitted relaxation), `should` (recommendation: justification plus safety risk
  assessment, no departure), `may` (permissible: internal review), anything else is a note.
  A clause's type is its strongest verb. **Only `shall` can need a Departure**, which is the rule
  `is_departure` answers will be checked against. `must` was added to `Answer.requirement_type`,
  because the guide's original list missed it.
- **Numbering (Appendix B):** `X.X` is a requirement, `X.X.X` is advice belonging to requirement
  `X.X`, unnumbered NOTEs belong to the clause immediately before them. A verb that contradicts
  the level (a `X.X` clause whose strongest verb is `should`) is flagged for checking by eye, not
  rejected: it usually means the parser split or merged clauses wrongly.
- **Annex clauses:** National Application Annex clauses are prefixed `E/` or `NI/` (CD 109 has
  37). Citations accept them: `[CD 109 cl. E/1.1]` -> `CD109-E/1.1`. Only prefixes seen in real
  documents are allowed. *Phase 2 note:* `/` is not valid in an Azure AI Search document key,
  so the index key must be an encoded form of the chunk ID.
- **Doc codes (Appendix A1):** discipline letter (G/L/C/T) + life-cycle letter (G/A/D/C/M/S/Z) +
  three digits. Citations and `sources.yaml` both validate against this, from one definition.
- **Version from the PDF itself:** every page footer prints the code, version and publication
  date, so the ingest step can check `sources.yaml` against the PDF it's parsing.
- *Checked against CD 109* (pypdf text): 150 two-level requirements, 80 three-level advice clauses
  and 37 England-annex clauses, consistent with the scheme. The same extraction ran words
  together ("Wheretheresultsof..."), which supports using Document Intelligence for real parsing.
- **Verb matching is lowercase only**, and skips verbs that are quoted ('shall'), bracketed
  ("(should)") or open a condition ("should the OO agree"). DMRB clauses open with their subject,
  so a capitalised "May" is a month, not a permission. Found in code review.
- **Doc-code letters are checked independently**, so a combination that doesn't exist yet (e.g.
  "GD 109") is accepted. *Alternative:* a list of known codes, which would need updating as the
  DMRB grows. `sources.yaml` and retrieval limit what can actually be cited anyway.

## 2026-10-22: Azure setup as built (differs from the guide)
- **Subscription:** the free trial gave 0 quota for most models; upgraded to pay-as-you-go.
- **Chat model:** `gpt-5.4-mini` on **Data Zone Standard** (EU processing). The guide's
  `gpt-4.1-mini` is blocked for new subscriptions (models go "Deprecated" ~12 months after
  launch), and Global Standard had 0 quota for current models. Claude Haiku 5.5 / Opus 5.5 have
  0 quota in every region until a quota request is approved; they'll be added as a second
  provider for the Phase 6 comparison.
- **Regions:** Foundry, models and Document Intelligence in Sweden Central; **AI Search in UK
  South**, because Sweden Central was out of Search capacity. Cross-region latency is negligible.
- **Embeddings go through the resource-level endpoint** (`https://<resource>.services.ai.azure.com/openai/v1`,
  token scope `cognitiveservices.azure.com`). The project endpoint's `/openai/v1` serves chat
  but returned 404 for embeddings, even on the default project. Derived from the project endpoint,
  so no extra setting. Found by the smoke test (`python -m core.smoke_test`).

## 2026-10-22: Parsing and caching
- **Document Intelligence `prebuilt-layout`, Markdown output**, plus the full JSON result (page
  numbers, table cells, bounding regions) for page lookups and table extraction later.
- **Cached against the PDF's SHA-256** in `data/parsed/` (git-ignored): re-parsed only when the
  PDF changes, or with `--force`. Metadata is written last, so an interrupted run re-parses.
- **The PDF's footer is checked against `sources.yaml` before the billable call**, using pypdf
  locally. Paying to index the wrong revision is the costliest mistake this step can make.
- First run: GG 101, CD 109, CD 123, CG 501. 313 pages, about $3.13.

## 2026-10-22: Reading order from line geometry, not DI Markdown
- **Problem:** DI's Markdown and paragraph order get scrambled around figures. On some pages it
  merged figure labels, NOTEs, a clause and the page footer into one paragraph (CD 109 p.10:
  NOTE 2, clause 2.2 and NOTE 1 in one block, before the section heading).
- **Measured:** clause numbers out of order per document: Markdown 5-14; DI paragraphs 0-1 but
  4 clauses lost inside merged paragraphs; **line geometry 0, none lost**.
- **Rule:** rebuild order from DI *lines* sorted by page and vertical centre, then x within a
  row. Drop lines inside figures, tables and page furniture, plus untagged footers and bare page
  numbers. Rows are clustered by distance, not by rounding y to bands: a band edge fell between
  clause 2.2's number and its own text, which glued 118 clauses' first lines onto the previous
  clause. The verb/numbering check (below) caught it.
- **Clause starts:** a number in the **left-margin column** (measured per document; all four sit
  at x = 0.58in). The same numbers elsewhere are wrapped cross-references (x = 1.37in), version
  numbers or table values.

## 2026-10-22: Chunking rules (Phase 1 decisions)
- **One chunk per clause**; NOTEs stay with the clause before them (GG 101 Appendix B) but are
  excluded when classifying the clause's verb.
- **Self-check:** each clause's strongest verb must match its numbering level (X.X: must/shall,
  X.X.X: should/may). After the fixes above: **0 mismatches in 595 clauses** across four standards.
- **Tables are their own chunks** (`CD109-Table-3.5`), linked from clauses that mention them
  (`refs`), citable as `[CD 109 Table 3.5]`. Chosen by Ben over embedding tables in clauses: big
  tables would blow the clause token cap, and the Phase 4 `get_table` tool fetches them directly.
  The caption line printed just above a table beats DI's caption: DI attaches only ~1 in 3 and
  mislabelled CD 123 Table 5.18a as 5.18b. Tables continued across pages are merged.
- **Glossary:** Terms, Abbreviations and Symbols tables become one `CD109-Glossary` chunk per
  document (Ben's choice). Foreword, Introduction and Release notes are not indexed.
- **Numbering forms seen in real documents:** annex prefixes E/, NI/, S/; appendix clauses A2.1;
  table numbers 5.18a, A.1 and 6.25N2 (a NOTE's table).
- **Cap:** ~800 tokens (4 characters per token). Long clauses split at sentence ends into
  `CD109-1.1#2` parts that keep the same `citation_id`; clauses are never merged.
- **Code review fixes:** caption lines go to their table, not the clause above; "Table A.1 shows
  ..." is prose, not a caption; short unpunctuated lines before a clause number are untagged
  sub-headings; heading-tagged lines that finish an open sentence stay in the clause; mentions
  of other standards ("CD 127 clause 3.2") are not linked locally; split parts (`#2`) count as
  their clause in the citation gate. Clauses ending mid-sentence: 32 -> 4 (all checked: list
  items, a legitimate "greater than 2.5%" ending, and one figure-label leak).
- **Known gaps:** unnumbered appendix text (e.g. CD 109 Appendix A, coordinated link design) is
  not indexed; figure axis labels outside DI's figure box can leak into clause text; tables
  printed as images aren't extracted. ~240 lines per document fall outside any clause, mostly
  front matter.

## 2026-10-22: Search index (Phase 2)
- **One index field per Chunk attribute**, checked by a test so a new Chunk field can't be
  forgotten. `text` and `heading_path` are searchable with the `en.microsoft` analyser (better
  lemmatisation than Lucene's stemmer); `doc_code`, `citation_id`, `kind`, `clause_level`,
  `requirement_type`, `id` and `refs` are filterable for the Phase 4 tools.
- **Document key = URL-safe base64 of the chunk ID.** Keys only allow letters, digits, `_`, `-`
  and `=`, so `CD123-E/1.1#2` can't be used as it is. Base64 is reversible and can't collide; the
  readable ID stays in the `id` field.
- **Embedding text = `heading_path` + blank line + `text`.** The heading adds the topic a bare
  clause often lacks. Document code and title are not included yet (see open question below).
- **Rebuild recreates the index** rather than updating it, so removed chunks and schema changes
  never leave stale documents. Everything is embedded *before* the old index is deleted, so a
  throttled embedding run leaves the working index in place.
- **Four search modes behind one function** (`index/search.py`): keyword, vector, hybrid (RRF),
  semantic (hybrid then semantic re-rank). Hybrid and semantic ask for 50 vector candidates
  because the semantic ranker re-ranks up to 50. Semantic errors fail loudly (no silent fallback
  to un-reranked results once the free tier's 1,000 queries a month run out).
- **Recall@k counts distinct clauses**: each search fetches 20 chunks, split parts (`#2`)
  collapse into their clause, and the top k distinct clauses are scored. Otherwise a mode that
  returns several parts of one clause would see fewer than k clauses (found by code review).
  Reported with MRR@k (rank 1 vs rank 5) and coverage@k (share of *all* expected clauses
  found, which matters when an answer needs a clause and its table).
- **The eval refuses to run on bad ground truth or a stale index**: an expected clause that no
  chunk carries, or an index whose IDs differ from `data/chunks/`, stops the run. Either would
  otherwise show up as a retrieval failure.
- **A rebuild that fails after deleting the old index says so** (`RebuildError`: "missing or
  partial, re-run"). A build-then-swap alias would avoid the gap; not worth it for a dev index.
- **Open (Ben):** questions that name a standard ("Which roads does CD 109 apply to?") can miss,
  because clause text says "this document", not "CD 109". Option: prefix the embedding text with
  `CD 109 Highway link design >`. Decide once recall on the real questions is in.
- **Retrieval questions are YAML** (`evals/retrieval.yaml`), not JSONL: Ben writes them by
  hand, and a block per question is easier to copy and edit than one JSON object per line.
  `golden.jsonl` may follow later if it's written the same way.
