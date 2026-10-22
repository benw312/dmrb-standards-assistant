# System prompt

`system.md` in this folder is the system prompt for the RAG pipeline (`python -m agent.rag`).
**Ben writes it**; the pipeline refuses to run until it exists.

## What goes in `system.md` (yours)

The domain rules, in your own words. The build guide (p.5) suggests:

- Answer only from the clauses provided. If they don't cover the question, say so and suggest
  the likely standard instead of guessing.
- Cite every claim as `[CD 109 cl. 5.3.1]`.
- Quote the requirement's exact wording when it is a "shall".
- Say which version of the standard you're quoting.
- End with: "Decision support only; confirm against the current standard."

Add anything an engineer would expect: how to treat "should" vs "shall", relaxations and
departures, what to do when two clauses conflict, the tone you want.

## What the code adds (don't repeat it)

`agent/rag.py` appends `OUTPUT_CONTRACT` after your text. It's the format the citation checker
depends on, so it lives in code:

- the exact citation forms, and that any mention of a clause or table must be cited
- what each field of the structured answer means
- that a "not covered" answer cites nothing
- no arithmetic: a needed calculation is named and sent for review

Each retrieved clause is shown to the model with its ID, citation form, standard and version,
strength (shall/should/may), page, heading and text.

## Trying it

```bash
uv run python -m agent.rag "What is the maximum gradient on a motorway?"
uv run python -m agent.rag --json "What's the speed limit on a motorway?"
```
