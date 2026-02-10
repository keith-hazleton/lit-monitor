# Literature Monitor

An automated literature monitoring tool for researchers. Scans PubMed and bioRxiv for new papers matching your research interests, uses Claude AI to rank and summarize them, and delivers a weekly email digest with one-click Zotero integration.

## Features

- **Automated Search**: Weekly scans of PubMed and bioRxiv using customizable search queries
- **AI-Powered Ranking**: Claude analyzes papers and scores relevance to your active research projects
- **Smart Summaries**: Each paper gets a plain-language summary and relevance rationale
- **Email Digest**: HTML email with papers organized by priority (high/moderate/low)
- **One-Click Zotero**: Signed links to add papers directly to your Zotero library
- **Capacities Integration**: Save papers to your Capacities workspace via the Cloudflare Worker
- **Web UI**: Flask-based interface for editing search queries and settings
- **GitHub Actions**: Fully automated weekly runs with no server required

## Quick Start

### 1. Fork and Clone

```bash
# Fork this repository on GitHub, then:
git clone https://github.com/YOUR-USERNAME/lit-monitor.git
cd lit-monitor
```

### 2. Install Dependencies

```bash
# Requires Python 3.11+
pip install -r requirements.txt
```

### 3. Configure Environment Variables

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Claude API key for ranking ([get one](https://console.anthropic.com/)) |
| `NCBI_API_KEY` | Recommended | NCBI API key for higher rate limits ([get one](https://www.ncbi.nlm.nih.gov/account/settings/)) |
| `NCBI_EMAIL` | Yes | Your email for NCBI API |
| `EMAIL_TO` | For email | Recipient email address |
| `EMAIL_FROM` | For email | Sender email address |
| `SMTP_HOST` | For email | SMTP server (e.g., `smtp.gmail.com`) |
| `SMTP_PORT` | For email | SMTP port (usually `587`) |
| `SMTP_USER` | For email | SMTP username |
| `SMTP_PASSWORD` | For email | SMTP password or app password |
| `ZOTERO_WORKER_URL` | For Zotero | Your deployed Cloudflare Worker URL |
| `SIGNING_SECRET` | For Zotero | HMAC secret (generate with `openssl rand -hex 32`) |
| `CAPACITIES_API_TOKEN` | For Capacities | Capacities API token |
| `CAPACITIES_SPACE_ID` | For Capacities | Capacities space UUID |

### 4. Customize Your Research Profile

Edit `config/config.yaml` to define:

```yaml
# PubMed/bioRxiv search queries
search_queries:
  - "pediatric liver disease"
  - "biliary atresia"
  - "your research topic"

# Authors to highlight in digests
watched_authors:
  - "Smith JK"
  - "Doe AB"

# Your active research projects (used for AI relevance scoring)
active_projects:
  - name: "Project Name"
    keywords:
      - keyword1
      - keyword2
      - keyword3

# Journal weight multipliers for ranking
journal_weights:
  high_trust:
    weight: 1.5
    journals:
      - "Nature"
      - "Science"
  low_trust:
    weight: 0.5
    journals:
      - "Predatory Journal Name"
```

### 5. Run Locally

```bash
# Search for papers (last 7 days)
python main.py --pubmed-only --days 7 --skip-ranking

# Rank papers with Claude
python main.py --rank-only --rank-limit 50

# Generate digest
python main.py --digest --days 7

# Generate and send email
python main.py --digest --days 7 --send-email

# View stats
python main.py --stats
```

## Cloudflare Worker Setup (for Zotero Integration)

The one-click Zotero links require a Cloudflare Worker to securely add papers to your library.

### 1. Install Wrangler

```bash
cd worker
npm install
```

### 2. Deploy the Worker

```bash
npx wrangler deploy
```

### 3. Add Secrets

```bash
npx wrangler secret put ZOTERO_API_KEY
npx wrangler secret put ZOTERO_USER_ID
npx wrangler secret put SIGNING_SECRET
# Optional: for Capacities integration
npx wrangler secret put CAPACITIES_API_TOKEN
npx wrangler secret put CAPACITIES_SPACE_ID
```

Get your Zotero credentials at [zotero.org/settings/keys](https://www.zotero.org/settings/keys).

### 4. Update Your .env

Add your deployed worker URL:
```
ZOTERO_WORKER_URL=https://lit-monitor-zotero.YOUR-SUBDOMAIN.workers.dev
```

**Important**: The `SIGNING_SECRET` must be identical in your `.env`, GitHub Secrets, and Cloudflare Worker.

## GitHub Actions Setup (Automated Weekly Runs)

### 1. Add Repository Secrets

Go to your repository: **Settings → Secrets and variables → Actions → New repository secret**

Add all the environment variables from your `.env` file as secrets.

### 2. Enable the Workflow

The workflow runs automatically every Monday at 8am UTC. You can also trigger it manually:

**Actions → Weekly Literature Scan → Run workflow**

### 3. Access Results

- **Digest HTML**: Download from the workflow's Artifacts
- **Database**: Persisted as an artifact between runs (90-day retention)

## Web UI for Configuration

The Flask web UI provides an easy way to edit your search parameters without manually editing YAML.

### Running the Web UI

```bash
python -m web.app
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000) in your browser.

### What You Can Edit

- **Search Queries**: Add, remove, or modify PubMed/bioRxiv search terms
- **Watched Authors**: Authors to highlight in digests
- **Active Projects**: Research projects with keywords for relevance scoring
- **Journal Weights**: Adjust ranking multipliers for trusted/untrusted journals
- **Settings**: Max results per query, days to look back, minimum relevance score

### Workflow

1. Start the Flask UI locally
2. Make your changes in the browser
3. Click "Save" for each section
4. Commit and push changes:
   ```bash
   git add config/config.yaml
   git commit -m "Update search configuration"
   git push
   ```
5. Your next GitHub Actions run will use the updated config

## Project Structure

```
lit-monitor/
├── config/
│   └── config.yaml          # Your research configuration
├── src/
│   ├── config_loader.py     # YAML config parser
│   ├── database.py          # SQLite database layer
│   ├── email_digest.py      # HTML digest generator
│   ├── ranker.py            # Claude AI ranking
│   └── sources/
│       ├── pubmed.py        # PubMed E-utilities client
│       └── biorxiv.py       # bioRxiv API client
├── web/
│   ├── app.py               # Flask web UI
│   ├── templates/           # HTML templates
│   └── static/              # CSS styles
├── worker/
│   └── src/index.js         # Cloudflare Worker for Zotero
├── data/
│   └── papers.db            # SQLite database (git-ignored)
├── output/
│   └── digest_*.html        # Generated digests
├── main.py                  # CLI entry point
└── .github/workflows/
    └── weekly_scan.yaml     # GitHub Actions automation
```

## Command Reference

| Command | Description |
|---------|-------------|
| `python main.py --pubmed-only --days 7` | Search PubMed only |
| `python main.py --biorxiv-only --days 7` | Search bioRxiv only |
| `python main.py --skip-ranking` | Search without AI ranking |
| `python main.py --rank-only --rank-limit N` | Rank up to N unranked papers |
| `python main.py --digest --days 7` | Generate HTML digest |
| `python main.py --digest --send-email` | Generate and email digest |
| `python main.py --stats` | Show database statistics |
| `python -m web.app` | Start Flask config editor |

## Troubleshooting

### "Invalid Signature" on Zotero links
The `SIGNING_SECRET` must match in all three places:
1. Your local `.env`
2. GitHub Secrets
3. Cloudflare Worker (`npx wrangler secret put SIGNING_SECRET`)

### Rate limiting from PubMed
Add an `NCBI_API_KEY` to increase rate limits from 3 to 10 requests/second.

### Email not sending
For Gmail, use an [App Password](https://support.google.com/accounts/answer/185833) instead of your regular password.

### Flask UI shows blank page
Use `http://127.0.0.1:5000` instead of `http://localhost:5000` (IPv4 vs IPv6 issue).

## Contributing

This is a personal project shared publicly in case others find it useful. I make no guarantees about functionality or ongoing maintenance. Feedback is welcome, but I can't promise timely responses. See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

## License

MIT License - feel free to fork and adapt for your research needs.
