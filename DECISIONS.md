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

## 2026-10-22: Fixes from Ben's review of 20 chunks
- **Three-level heading paths from the PDF's fonts.** DI tags headings without a level, so a
  subheading replaced its parent (CD 123 cl. 7.7 showed "Lane widths", not "Entry lanes, exit
  lanes and storage capacity > Lane widths"). Every standard uses 14.3pt bold for section
  titles, 12pt bold for headings and 10pt bold for subheadings, so `ingest/fonts.py` reads
  bold text with pypdf (local, free), groups it into rows by position, and a line takes the
  level of the bold row printed at its own height. Guessing from heading order was rejected:
  a 12pt heading with no subheading (CD 123 "Minor road traffic islands") would have been
  nested wrongly. Without the PDF it falls back to one level.
- **With fonts, only bold 12pt/10pt text can be a heading.** Code review found impostors that
  replaced real headings: CG 501's running header "CG 501 Version 2.2.0", a "NOTE" label,
  equation captions ("Equation 4.2 Superelevation") and "Length of curve:". Equation and figure
  captions, NOTE labels and lines ending in ":" are never headings; a heading that repeats its
  section title is skipped. Audit against main: every heading name removed was one of these,
  every name added was a real parent heading that had been overwritten.
- **Captions win over heading tags, and wrapped captions are joined.** DI tagged both lines of
  CD 123 Table 5.18a's caption as headings, so the caption's second line became the section
  name and closed clause 5.18 early, dropping its NOTE. Figure captions are never headings
  either (CD 123 "Figure 5.6 Corner radii" had become one), and a heading-tagged line starting
  lowercase is a wrapped caption, not a heading. A second caption line is joined only if it is
  bold like the caption, so a roman "(dimensions in metres)" stays out.
- **Merged table cells repeat their text in every spanned cell** (Ben's suggestion for CG 501
  Table 5.2), so each row reads on its own. Header cells repeat across columns only: copied
  down, a tall header ("Design speed (kph)") landed in the flattened second header row as if it
  were data. A cell DI reported is never overwritten by a neighbour's span.
- **Checked, no change:** the glossary's last three CD 109 terms were in split part
  `CD109-Glossary#2`, not missing. Chunk pages are physical PDF pages (cover = 1), which is what
  `#page=N` links need; printed page numbers are one lower. A chunk records its first page only.

## 2026-10-22: Charts become calculation tools (Phase 4)
- **Survey:** about 110 figures across CD 109, CD 123 and CG 501. 7 are charts and 6
  CD 109 equations can be calculated; the other ~100 figures are layout drawings whose
  dimensions are in the clause text. Catalogue: `docs/CHARTS.md`.
- **Tools, not text:** the charts are images, so retrieval can only find the caption. A model
  asked about one would guess. A tool computes the answer in code and cites the clause (Ben's
  call: "the most robust way"), the same rule as `check_limit`.
- **Formula before digitising:** CG 501 Fig 7.5.3N2a/b are reproduced exactly by
  `G = √(S² + C²)` and `L = W·√(1 + (S/C)²)`, and Fig 9.23N2's bands are printed V²/R
  boundaries, so those need no digitising. Curves that are data (Fig 7.5.4, 7.5.1, 2.1) are read
  once by Ben and interpolated linearly. Out-of-range inputs are refused, never extrapolated;
  results near the line are marked marginal and escalated.
- **Flowchart:** CG 501 Fig 2.5 asks judgement questions, so no tool; its text will be indexed.
- Ben's approach: build everything, then drop whatever proves too hard (tier 3 first).


## 2026-10-22: CD 109 equation tools and clause lookups (Phase 4)
- **Six CD 109 equations are tools** (`agent/tools/equations.py`): bendiness, Ac (Eq 2.2a/b),
  VISI (Eq 2.8.1, 2.8.2), superelevation (Eq 4.2 with cl. 4.1-4.4), transition length (Eq 4.13
  with cl. 4.12-4.15). Each returns the value, the clause IDs it relied on, notes, and warnings;
  any warning sets `needs_review`.
- **Superelevation uses Table 2.10's radii, not the clauses' V²/R shorthand.** Cl. 4.2 says
  "V²/R > 7", the table's column says 7.07, and the rounded radii give 6.8-7.08 depending on
  speed. The clauses are written against the radii ("radii less than those shown in Table
  2.10"), so the tool only accepts Table 2.10's six design speeds. Check: Eq 4.2 at the table's
  3.5%, 5% and 7% radii reproduces those values within 0.25 at every speed.
- **At exactly the adverse-camber minimum radius, camber applies.** Cl. 4.1 covers radii "less
  than" it and cl. 4.1.1 radii "greater than" it, so the standard leaves equality open; a minimum
  radius *with* adverse camber permits adverse camber at that radius. Ben to confirm.
- **Exact where it decides compliance, consistent where it's advisory.** The cap, L vs √(24R)
  and the q limits are exact product comparisons. Range checks (Ac on Figure 2.1, VISI <= 720 m)
  use the displayed value, so "720.0 m, beyond 720 m" can't happen. Inputs are capped at 10
  significant digits and realistic ranges (R >= 1 m, q >= 0.01), so extreme input is a
  validation error, never a decimal-arithmetic exception (found by code review).
- **`get_clause` / `get_table` fetch by citation** with a `citation_id` filter and reassemble
  split parts. Only table and glossary parts drop their repeated caption/header; clause parts
  are joined whole (review found the generic version could drop a real line). A clause number
  printed twice in a standard (`#dup2`) is refused as ambiguous rather than merged.

## 2026-10-22: Figures indexed as text (CG 501 Figure 2.5)
- **A new chunk kind, `figure`**, citable as `[CG 501 Figure 2.5]` (`CG501-Figure-2.5`). Its
  text is the caption plus the figure's own paragraphs, which DI groups one per box, so a
  flowchart's questions and outcomes stay together and in a sensible order. Bullets DI read as
  lone dots become "- " lines. Clauses that mention the figure link to it in `refs`.
- **Only figures listed in `sources.yaml` (`text_figures`) are indexed.** Word count can't tell
  a flowchart (242 words) from a road-marking drawing (up to 135 words of labels), and label
  soup would crowd real clauses out of search. So it's a deliberate per-document choice: the
  "person classifies the figure" step in docs/CHARTS.md. A listed figure that can't be found
  is reported in the chunk summary.
- **Known limit:** arrows aren't captured, so "No to all -> Discharge to ground" reads as
  adjacent lines rather than an explicit edge. Good enough to retrieve and quote; an answer
  that needs the exact decision path should cite the figure and say to check it.

## 2026-10-22: Phase 3 RAG baseline (`agent/rag.py`)
- **Pipeline:** semantic hybrid search for the top 8 chunks, then one structured-output call
  (`responses.parse` with the `Answer` model), then the citation check in code. A failed check
  gets one retry with the reasons; a second failure is flagged for human review with every
  attempt's reasons, and the CLI prints it under an UNVERIFIED banner. A refusal (no parsed
  output) isn't retried.
- **The system prompt is split.** Ben owns `agent/prompts/system.md` (domain rules); the code
  owns `OUTPUT_CONTRACT` (citation format, field meanings), appended after it, because the
  checker depends on it and the two must not drift. The pipeline refuses to run without
  Ben's file.
- **Found by a live plumbing check** (placeholder prompt, not committed):
  - The model can return schema-valid JSON that breaks `Answer`'s rules (a blank answer).
    The SDK raises inside `parse`; it's now caught and given the retry instead of crashing.
  - gpt-5.4-mini writes `[CD 109 cl. 5.1, CD 109 Table 5.1]` with a comma. The citation
    grammar now treats ", <document>" and ", Table/Figure/Glossary" as a new segment; a comma
    between bare numbers is still a clause list.
  - Paraphrasing a clause repeats its own references ("the values in Table 5.1"). A prose
    mention now passes if the answer also cites that exact item in brackets, but only when
    the answer cites one document and names no other: code review showed that with two
    documents, "CD 123 cl. 5.1" next to [CD 109 cl. 5.1] slipped through. Anything not cited
    still fails.
  - Asked for superelevation, the model computed 14.0% where Eq 4.2 gives 4.9%. The
    contract now forbids arithmetic: name the equation and flag for review. Phase 4's
    `superelevation` tool is the real fix.
  - "Not covered" answers liked to cite the clauses they ruled out; the contract now says a
    not_covered answer names none.

## 2026-10-25: Retrieval measured; embedding text kept

- **Result:** on Ben's 20 questions, semantic recall@5 is 100% (MRR 0.83), hybrid 90%, vector
  90%, keyword 80%. Above the 80% bar, so `heading_path + text` stays as the embedding text and
  the document-code prefix idea is dropped for now.
- **What the modes show:** keyword misses paraphrases (R12, R14, R15); R07 (drainage on an urban
  embankment, answered by CG 501 Table 3.5.1) is found only by the semantic re-ranker. That
  supports semantic as the production mode.
- **Ground-truth edits by Claude, at Ben's request** so Phase 3 wasn't blocked: citation
  format only, plus one replaced duplicate (R08). Flagged in the file and in HANDOVER for Ben.
- **Caveat:** 20 questions is small, and they were written with the standards open. The
  golden set (Phase 6) adds questions written without the clause list open.
