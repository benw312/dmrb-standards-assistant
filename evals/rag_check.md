# Phase 3 check: 10 answers from the RAG baseline

<!-- CLAUDE DRAFT (2026-10-25): Claude ran and judged these against the indexed chunks so the
     phase wasn't blocked. Ben to re-judge as the engineer: tick or correct the verdicts. -->

Run: `uv run python -m agent.rag --json "<question>"`, gpt-5.4-mini, semantic search top 8,
draft system prompt (`agent/prompts/system.md`, second revision). Answers abridged; the model's
text is in the scratchpad runs, not committed (it quotes the standard at length).

| # | Question | Verdict | Notes |
|---|---|---|---|
| 1 | Desirable max gradient, all-purpose dual | ✅ | 4% [CD 109 Table 5.1], "should" from cl. 5.1.1. Correct |
| 2 | Entrance gate setback off a direct access | ✅ | Quotes cl. 4.3 ("one vehicle ... clear of the main running lane"); says no fixed distance is given |
| 3 | Drainage for an urban road on embankment | ✅ | Kerbs and gullies [CG 501 Table 3.4]; Table 3.5.1 options. Found Table 3.4, which Ben's question didn't list |
| 4 | WS2+1 through lanes at a ghost island | ✅ | 3.5 m exclusive of hard strips [CD 123 cl. 6.7]. Adds cl. 6.21 (physical reserve): relevant but stretched |
| 5 | Max superelevation "in a city" | ✅ | 5% [CD 109 cl. 4.4]; treats city as urban and says so. Footer missing |
| 6 | AADT 30,000 on WS2+1 | ✅ | Above 25,000 [CD 109 cl. 6.1.1]; "should", so justification, not departure; flagged |
| 7 | 5% gradient on a motorway | ⚠️ | Values right (3% / 4% relaxation), departure flagged, but calls it "should-level": cl. 5.1 (the "shall") wasn't retrieved |
| 8 | Superelevation, R = 510 m at 100 kph | ✅ | Names Eq 4.2, says a calculation is needed, flags review. Doesn't invent a number |
| 9 | Motorway speed limit (out of scope) | ✅ | "Not covered", cites nothing. Needed the retry: first attempt cited clauses |
| 10 | 300 mm bollard in the SSD envelope | ✅ | Allowed: ≤ 550 mm exception [CD 109 cl. 3.4]. Footer missing |

**9 of 10 correct and cited; 1 partly wrong for a retrieval reason.** All citations passed the
code check on the final attempt; 2 of 10 needed the retry.

## What the first run (before prompt revision) got wrong

The first draft prompt scored 6/10. Fixed by prompt wording:
- Version rarely stated. Now required in the first sentence; 10/10 comply.
- AADT question treated a "should" as a possible departure. Prompt now says it isn't.
- Superelevation opened with "you need 2.5%" and then withdrew it. Prompt now says to lead
  with "a calculation is needed".
- Speed-limit answer listed what the clauses *do* cover and escalated twice. Now clean.

## Findings to act on

1. **Table without its "shall" (q7).** Search returned Table 5.1 but not cl. 5.1 that makes
   it binding. Fix in Phase 4: when a table is retrieved, also fetch the clauses whose `refs`
   point at it (or let the agent call `get_clause`). Good golden-set case.
2. **Disclaimer footer dropped in 3 of 10.** A fixed sentence belongs in code, not the prompt:
   `render()` should append it.
3. **Superelevation needs the tool.** Eq 4.2 gives 10000 / (2.828 × 510) ≈ 6.9%, within the 7%
   rural maximum. Only the Phase 4 `superelevation` tool should say that.
