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
