# Claude Code Instructions

## Sister Repository

This project has a sister repository: [lit-monitor-pi](https://github.com/keith-hazleton/lit-monitor-pi)

Both repositories contain the same core functionality - the only difference is the deployment target:
- **lit-monitor** (this repo): Runs via GitHub Actions
- **lit-monitor-pi**: Runs via cron on a Raspberry Pi

## Keeping Repositories in Sync

When making changes to core functionality, the same changes should be applied to both repositories. This includes:

### Files that should stay in sync:
- `src/` - All source code:
  - `database.py` - SQLite database layer (schema, feedback, seed papers, config suggestions, digest dedup)
  - `ranker.py` - Claude AI ranking with feedback-informed prompts
  - `email_digest.py` - HTML digest generator with star/dismiss feedback links
  - `feedback.py` - Feedback prompt builder and Worker feedback sync
  - `paper_lookup.py` - DOI/PMID lookup for seed paper import
  - `config_suggester.py` - AI-powered config improvement suggestions
  - `zotero_sync.py` - Zotero library sync for seed papers
  - `config_loader.py` - YAML config parser
  - `sources/pubmed.py` - PubMed E-utilities client
  - `sources/biorxiv.py` - bioRxiv API client
- `web/` - Flask web UI:
  - `app.py` - Routes for config, papers, seeds, suggestions (note: `__main__` block differs between repos)
  - `templates/index.html` - Config editor with nav bar
  - `templates/papers.html` - Paper list with star/dismiss feedback
  - `templates/seeds.html` - Seed paper import via DOI/PMID
  - `templates/suggestions.html` - Config suggestion review
  - `static/style.css` - Shared styles
- `worker/` - Cloudflare Worker for Zotero + feedback integration
- `config/config.yaml` structure (not the actual search terms)
- `requirements.txt`
- `.env.example`
- `main.py` - CLI entry point (note: `--web` flag behavior may differ)

### Files that are intentionally different:
- `README.md` - Different setup instructions for each platform
- `.github/workflows/` - Only exists in lit-monitor (GitHub Actions)
- `run_weekly.sh` - Only exists in lit-monitor-pi (cron wrapper)
- `logs/` directory - Only exists in lit-monitor-pi
- `web/app.py` `__main__` block - Pi version binds to `0.0.0.0` with network IP display; GitHub Actions version uses `127.0.0.1`

## Workflow

When asked to modify functionality:
1. Make the change in the current repository
2. Remind the user that the same change should be applied to the sister repository
3. Offer to make the change in the sister repository if it's available in the working directories
