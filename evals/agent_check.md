# Phase 4 check: the agent on the same 10 questions

<!-- CLAUDE DRAFT (2026-10-25): Claude ran and judged these against the indexed chunks. Ben to
     re-judge as the engineer. -->

Run: `uv run python -m agent.loop --json "<question>"`, gpt-5.4-mini, draft system prompt and
draft tool descriptions. Same questions as [rag_check.md](rag_check.md), so the two compare
directly. Traces are in `traces/` (git-ignored).

| # | Question | RAG | Agent | Agent notes |
|---|---|---|---|---|
| 1 | Desirable max gradient, all-purpose dual | ✅ | ✅ | Also gives the 8% relaxation |
| 2 | Entrance gate setback | ✅ | ✅ | Searched twice |
| 3 | Drainage, urban road on embankment | ✅ | ✅ | Table expansion found CG 501 cl. 3.5.1 (the clause that points to Table 3.5.1) |
| 4 | WS2+1 through lanes at a ghost island | ✅ | ✅ | |
| 5 | Max superelevation "in a city" | ✅ | ✅ | |
| 6 | AADT 30,000 on WS2+1 | ✅ | ✅ | "should": justification, not departure |
| 7 | 5% gradient on a motorway | ⚠️ | ✅ | **Fixed:** cl. 5.1 (the "shall") now comes back with Table 5.1. Wording "should normally use 3%" is loose |
| 8 | Superelevation, R = 510 m at 100 kph | ✅ (no value) | ✅ | **6.93%** from the `superelevation` tool, not the model. Assumed rural; now says so |
| 9 | Motorway speed limit | ✅ | ✅ after fix | First run cited GG 101 in a refusal and escalated (safe). A rule now says refusals cite nothing found while checking; re-run clean |
| 10 | 300 mm bollard in the SSD envelope | ✅ | ✅ | |

**Agent: 9/10 on its first full run; the miss (q9) escalated rather than answering wrongly,
and q8 and q9 pass when re-run after two rule fixes. The other eight weren't re-run after the
fixes. RAG baseline: 9/10.** The agent's gain is the two questions RAG can't do well
alone: a table needing its governing clause (q7) and a number needing a calculation (q8).

## Cost of the gain

| | RAG | Agent |
|---|---|---|
| Model calls per question | 1–2 | 2–4 |
| Input tokens per question | ~2.5–3.5k | ~8k (up to ~20k) |
| Seconds per question | ~4 | ~6 (up to ~10) |

Roughly 3× the tokens. The Phase 6 golden set decides whether that's worth it per question
type (a router could send plain lookups to RAG).

## Findings to act on

1. **Unstated inputs.** For q8 the model picked "rural" for the tool. The tool description
   now says to call it for both when the question doesn't say; it states the assumption but
   still only ran rural. Golden-set case.
2. **Tool descriptions are Claude's draft** (`agent/tools/registry.py`); Ben to rewrite.
3. **Evidence from calculation tools** lets the model cite clauses it didn't read in full
   (DECISIONS.md, 2026-10-25). Watch for it in traces.
