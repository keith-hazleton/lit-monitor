# Claude Code Instructions

## Sister Repository

This project has a sister repository: [lit-monitor-pi](https://github.com/keith-hazleton/lit-monitor-pi)

Both repositories contain the same core functionality - the only difference is the deployment target:
- **lit-monitor** (this repo): Runs via GitHub Actions
- **lit-monitor-pi**: Runs via cron on a Raspberry Pi

## Keeping Repositories in Sync

When making changes to core functionality, the same changes should be applied to both repositories. This includes:

### Files that should stay in sync:
- `src/` - All source code (database, ranker, sources, email_digest, config_loader)
- `web/` - Flask web UI
- `worker/` - Cloudflare Worker for Zotero integration
- `config/config.yaml` structure (not the actual search terms)
- `requirements.txt`
- `.env.example`

### Files that are intentionally different:
- `README.md` - Different setup instructions for each platform
- `.github/workflows/` - Only exists in lit-monitor (GitHub Actions)
- `run_weekly.sh` - Only exists in lit-monitor-pi (cron wrapper)
- `logs/` directory - Only exists in lit-monitor-pi

## Workflow

When asked to modify functionality:
1. Make the change in the current repository
2. Remind the user that the same change should be applied to the sister repository
3. Offer to make the change in the sister repository if it's available in the working directories
