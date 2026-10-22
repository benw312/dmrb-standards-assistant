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
