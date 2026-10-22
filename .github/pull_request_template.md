## What and why

<!-- One or two sentences. Link the PLAN.md phase / issue. -->

## Changes

-

## Test plan

- [ ] `uv run pytest` passes
- [ ] `uv run ruff check . && uv run ruff format --check .` clean
- [ ] Manual check (if any):

## Checklist

- [ ] Numeric comparisons go through `check_limit()`, not the model
- [ ] Citations are verified against retrieved chunks in code
- [ ] No secrets, keys or `.env` contents; no DMRB PDFs or parsed text committed
- [ ] `DECISIONS.md` updated if a design choice was made
