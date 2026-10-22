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
