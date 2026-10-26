# Transparency record and responsible-AI notes

This is a portfolio project, not a deployed public service. It is written up in the shape of the
UK Government's [Algorithmic Transparency Recording Standard](https://www.gov.uk/government/collections/algorithmic-transparency-recording-standard-hub)
(ATRS) because that's the record a public body would publish before using it, and writing it
shows what has and hasn't been addressed yet. It is not an official ATRS submission.

Draft by Claude, 2026-10-26, for Ben to review and put in his own words.

## Tier 1: summary

| | |
|---|---|
| **Name** | DMRB standards assistant |
| **Description** | Answers questions about UK highway design standards (the Design Manual for Roads and Bridges) with citations to the clauses it relied on. Calculations use coded DMRB equations and charts rather than the language model. It says whether a requirement is mandatory ("shall") or advice ("should"), and flags possible Departures from Standard for an engineer. |
| **Website / repo** | This repository |
| **Contact** | Ben Wallbridge (project owner) |

## Tier 2: detail

### 1. Ownership and responsibility

- **Owner:** Ben Wallbridge, highways engineer and the project's author.
- **Built with:** Azure AI Foundry, Azure AI Search and Azure Document Intelligence, written
  with Claude Code. Every change goes through a reviewed pull request (`DECISIONS.md` records why).
- **Accountability for answers:** stays with the engineer using it. Every answer carries
  "Decision support only; confirm against the current standard."

### 2. Description and rationale

- **Problem:** finding the governing clause, its strength and its exceptions across long
  standards is slow and easy to get wrong; a missed "shall" means an unrecorded departure.
- **Why AI:** questions are in natural language and often need several clauses joined up. A
  language model is used for reading and wording only. Retrieval, arithmetic, chart reading and
  the citation check are code.
- **Alternatives considered:** keyword search (recall@5 80% against 100% for semantic search
  on 20 engineer-written questions; README); a single search-and-answer call (the RAG baseline,
  kept as a comparison pipeline).

### 3. Decision-making process and human oversight

- **Decision support, not decisions.** The tool never approves a design or a departure.
- **Escalation to an engineer** whenever: a cited clause wasn't retrieved (after one retry),
  the answer suggests a Departure from Standard, confidence is low, a chart reading is within
  its tolerance of the line, chart data hasn't been checked by an engineer, or the agent hits its
  step or time limit. Escalated answers are shown as "Unverified" with their details hidden.
- **Pause for review:** the LangGraph version can stop before the answer is released and resume
  after an engineer has looked (`agent/graph.py`). A review queue is Phase 5.
- **Ground truth is human-written:** retrieval questions, golden answers, chart checks and
  tolerances are written or confirmed by the engineer, not generated (`CLAUDE.md` rules). Where
  Claude has drafted one to unblock work it is marked `CLAUDE DRAFT` and listed for review in
  `docs/HANDOVER.md`.

### 4. Data

| Data | Source | Notes |
|---|---|---|
| DMRB documents (GG 101, CD 109, CD 123, CG 501) | [standardsforhighways.co.uk](https://www.standardsforhighways.co.uk), Crown copyright, Open Government Licence v3.0 | PDFs and parsed text are not redistributed (git-ignored); links and versions in `data/sources.yaml` |
| Chart values | Read from the published figures (`ingest/digitise.py`), checked by an engineer | Committed with attribution and provenance |
| Questions asked | Users | Not stored beyond local trace files (`traces/`, git-ignored). No personal data is needed or requested |

- **Currency:** each answer states the document version. A weekly check
  (`ingest/currency.py`) fails if an indexed standard is superseded or withdrawn.
- **Coverage is partial:** four standards. A refusal says "no applicable clause found in the
  indexed standards", never "no requirement exists".

### 5. Technical specification

- **Models:** `gpt-5.4-mini` on Azure AI Foundry (Data Zone Standard, EU) for reasoning;
  `text-embedding-3-small` for search. No fine-tuning; no user data used for training.
- **Hosting (prototype):** personal Azure subscription; Search in UK South, the other services
  in Sweden Central. Keyless auth (Entra ID + RBAC), no keys in code.
- **Interfaces:** REST (`app/api.py`), Streamlit UI (`app/demo.py`), MCP server
  (`app/mcp_server.py`).
- **Evaluation:** recall@k / MRR on 20 hand-written questions; hand-judged 10-answer checks per
  pipeline; a golden-set scorer (citation precision and recall, requirement, departure and
  refusal accuracy) waiting on the engineer's golden set. Code is tested offline in CI
  (700+ tests).

### 6. Risks and mitigations

| Risk | Mitigation | Status |
|---|---|---|
| Invented or wrong clause cited | Citation gate in code: every cited ID must have been returned by a tool this run; retry once, then escalate | Built |
| Wrong arithmetic | The model never calculates: coded equations in exact decimals, independently re-checked on 6,041 cases (`evals/equation_check.md`) | Built |
| Chart misread | Machine reading refuses rather than guesses; values are drafts until an engineer checks an overlay; near-the-line results are "marginal" | Built; engineer check pending |
| Advice treated as a requirement, or the reverse | GG 101 verbal forms stored per clause and shown in the answer | Built |
| Out-of-date standard | Version in every answer; weekly currency check | Built |
| Over-reliance by users | "Decision support only" on every answer; uncertain answers shown as unverified | Built; user research not done |
| Prompt injection (in questions or document text) | Retrieved text fenced in blocks it can't close; all tools read-only; no actions outside the tool | Partly built; Azure AI Content Safety prompt shields are the next step |
| Data residency | No personal data processed; for a UK public-sector deployment, all services would move to UK South / UK West | Not done (prototype) |
| Accessibility | Streamlit demo is not assessed against WCAG 2.2; a public-facing service would need a GOV.UK-pattern front end and an accessibility audit | Not done |

## Against the UK Government AI Playbook principles

The [AI Playbook for the UK Government](https://www.gov.uk/government/publications/ai-playbook-for-the-uk-government)
(2025) sets 10 principles. Where this project stands on the ones it touches:

| Principle (paraphrased) | Here |
|---|---|
| Know what AI is and its limitations | The model reads and words; code retrieves, calculates and checks. Known failure modes are listed in `docs/ARCHITECTURE.md` with the control for each |
| Use AI lawfully, ethically and responsibly | Crown copyright respected (OGL attribution, no redistribution); no personal data |
| Use AI securely | Keyless auth, read-only tools, input validation at every boundary, fenced document text |
| Meaningful human control at the right stages | Escalation rules above; pause-for-review in the graph; engineer-owned ground truth |
| Manage the full lifecycle | Currency check, versioned sources, eval harness, traces for every run |
| Use the right tool for the job | Search and coded equations wherever they suffice; a model only where language is involved; a RAG baseline kept to show the agent earns its cost |
| Be open and collaborative | Public repo, decisions log, this record |
| Have the right assurance in place | Reviewed PRs, CI, hand-judged checks; a formal assurance review would be needed before any live use |
