"""
Email digest generator for literature monitoring.

Generates HTML email digests of ranked papers and optionally sends via SMTP.
Supports Capacities integration for saving digests to daily notes.
"""

import base64
import hashlib
import hmac
import json
import os
import smtplib
import time
import urllib.parse
import requests
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

from .config_loader import Config
from .database import PaperDatabase
from .sources.pubmed import Paper


CAPACITIES_API_BASE = 'https://api.capacities.io'


def generate_hmac_signature(data: str, timestamp: str, secret: str) -> str:
    """
    Generate HMAC-SHA256 signature matching the Cloudflare Worker's algorithm.

    Args:
        data: Base64-encoded paper data.
        timestamp: Unix timestamp in milliseconds.
        secret: Signing secret.

    Returns:
        Hex-encoded HMAC signature.
    """
    message = f"{data}.{timestamp}"
    signature = hmac.new(
        secret.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()
    return signature


def generate_zotero_link(
    paper: Paper,
    worker_url: Optional[str] = None,
    signing_secret: Optional[str] = None,
) -> str:
    """
    Generate a one-click Zotero add link with HMAC signature.

    If worker_url and signing_secret are set, creates a signed link to the
    Cloudflare Worker. Otherwise, falls back to DOI/paper URL.

    Args:
        paper: Paper to generate link for.
        worker_url: Cloudflare Worker URL.
        signing_secret: Secret for HMAC signing.

    Returns:
        URL string.
    """
    if worker_url and signing_secret:
        # Encode paper metadata
        metadata = {
            "title": paper.title,
            "authors": paper.authors[:20],  # Limit authors to keep URL reasonable
            "journal": paper.journal,
            "date": paper.pub_date,
            "doi": paper.doi,
            "url": paper.url,
            "abstract": paper.abstract[:500] if paper.abstract else "",
        }
        encoded = base64.urlsafe_b64encode(
            json.dumps(metadata).encode()
        ).decode().rstrip('=')  # Remove padding for cleaner URLs

        # Generate timestamp and signature
        timestamp = str(int(time.time() * 1000))
        signature = generate_hmac_signature(encoded, timestamp, signing_secret)

        return f"{worker_url}/add?data={encoded}&ts={timestamp}&sig={signature}"

    elif worker_url:
        # Worker URL but no signing secret - generate unsigned (will fail at worker)
        metadata = {
            "title": paper.title,
            "authors": paper.authors[:20],
            "journal": paper.journal,
            "date": paper.pub_date,
            "doi": paper.doi,
            "url": paper.url,
            "abstract": paper.abstract[:500] if paper.abstract else "",
        }
        encoded = base64.urlsafe_b64encode(
            json.dumps(metadata).encode()
        ).decode().rstrip('=')
        return f"{worker_url}/add?data={encoded}"

    else:
        # Fallback: link to DOI or paper URL
        if paper.doi:
            return f"https://doi.org/{paper.doi}"
        return paper.url


def generate_digest_html(
    papers: list[Paper],
    config: Config,
    title: str = "Literature Monitor Digest",
    worker_url: Optional[str] = None,
    signing_secret: Optional[str] = None,
) -> str:
    """
    Generate an HTML email digest of ranked papers.

    Args:
        papers: List of ranked papers (should be sorted by relevance).
        config: Application config.
        title: Email subject/title.
        worker_url: Optional Zotero worker URL.
        signing_secret: Secret for signing Zotero links.

    Returns:
        HTML string for the email.
    """
    # Categorize papers (note: must check `is not None` since 0.0 is a valid score)
    high_priority = [p for p in papers if p.relevance_score is not None and p.relevance_score >= 0.7]
    moderate = [p for p in papers if p.relevance_score is not None and 0.4 <= p.relevance_score < 0.7]
    low_priority = [p for p in papers if p.relevance_score is not None and p.relevance_score < 0.4]

    # Stats
    total = len(papers)
    open_access = sum(1 for p in papers if p.is_open_access)

    date_str = datetime.now().strftime("%B %d, %Y")

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
        }}
        .container {{
            background-color: white;
            border-radius: 8px;
            padding: 30px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        h1 {{
            color: #2c3e50;
            border-bottom: 3px solid #3498db;
            padding-bottom: 10px;
            margin-top: 0;
        }}
        h2 {{
            color: #2c3e50;
            margin-top: 30px;
            padding-bottom: 5px;
            border-bottom: 1px solid #eee;
        }}
        .stats {{
            background-color: #f8f9fa;
            padding: 15px 20px;
            border-radius: 6px;
            margin-bottom: 25px;
            display: flex;
            gap: 30px;
            flex-wrap: wrap;
        }}
        .stat {{
            text-align: center;
        }}
        .stat-number {{
            font-size: 28px;
            font-weight: bold;
            color: #3498db;
        }}
        .stat-label {{
            font-size: 12px;
            color: #666;
            text-transform: uppercase;
        }}
        .paper {{
            border-left: 4px solid #ddd;
            padding: 15px 20px;
            margin: 20px 0;
            background-color: #fafafa;
            border-radius: 0 6px 6px 0;
        }}
        .paper.high-priority {{
            border-left-color: #e74c3c;
            background-color: #fdf2f2;
        }}
        .paper.moderate {{
            border-left-color: #f39c12;
            background-color: #fffbf0;
        }}
        .paper.low-priority {{
            border-left-color: #95a5a6;
        }}
        .paper-title {{
            font-size: 16px;
            font-weight: 600;
            color: #2c3e50;
            margin-bottom: 8px;
        }}
        .paper-title a {{
            color: #2c3e50;
            text-decoration: none;
        }}
        .paper-title a:hover {{
            color: #3498db;
            text-decoration: underline;
        }}
        .paper-meta {{
            font-size: 13px;
            color: #666;
            margin-bottom: 10px;
        }}
        .paper-meta .journal {{
            font-style: italic;
        }}
        .paper-meta .date {{
            color: #888;
        }}
        .score-badge {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: bold;
            margin-right: 8px;
        }}
        .score-high {{
            background-color: #e74c3c;
            color: white;
        }}
        .score-moderate {{
            background-color: #f39c12;
            color: white;
        }}
        .score-low {{
            background-color: #95a5a6;
            color: white;
        }}
        .paper-summary {{
            margin: 12px 0;
            padding: 10px;
            background-color: white;
            border-radius: 4px;
            font-size: 14px;
        }}
        .paper-rationale {{
            font-size: 13px;
            color: #555;
            font-style: italic;
            margin: 8px 0;
        }}
        .paper-projects {{
            margin-top: 8px;
        }}
        .project-tag {{
            display: inline-block;
            background-color: #3498db;
            color: white;
            padding: 2px 10px;
            border-radius: 12px;
            font-size: 11px;
            margin-right: 5px;
            margin-bottom: 5px;
        }}
        .paper-links {{
            margin-top: 12px;
            font-size: 13px;
        }}
        .paper-links a {{
            color: #3498db;
            text-decoration: none;
            margin-right: 15px;
        }}
        .paper-links a:hover {{
            text-decoration: underline;
        }}
        .open-access {{
            color: #27ae60;
            font-weight: 500;
        }}
        .watched-author {{
            background-color: #9b59b6;
            color: white;
            padding: 1px 6px;
            border-radius: 3px;
            font-size: 11px;
            margin-left: 5px;
        }}
        .section-empty {{
            color: #888;
            font-style: italic;
            padding: 20px;
            text-align: center;
        }}
        .footer {{
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #eee;
            font-size: 12px;
            color: #888;
            text-align: center;
        }}
        @media (max-width: 600px) {{
            body {{
                padding: 10px;
            }}
            .container {{
                padding: 15px;
            }}
            .stats {{
                gap: 15px;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>{title}</h1>
        <p style="color: #666; margin-top: -10px;">{date_str}</p>

        <div class="stats">
            <div class="stat">
                <div class="stat-number">{total}</div>
                <div class="stat-label">Papers</div>
            </div>
            <div class="stat">
                <div class="stat-number" style="color: #e74c3c;">{len(high_priority)}</div>
                <div class="stat-label">High Priority</div>
            </div>
            <div class="stat">
                <div class="stat-number" style="color: #f39c12;">{len(moderate)}</div>
                <div class="stat-label">Moderate</div>
            </div>
            <div class="stat">
                <div class="stat-number" style="color: #27ae60;">{open_access}</div>
                <div class="stat-label">Open Access</div>
            </div>
        </div>
"""

    # High Priority Section
    html += """
        <h2>High Priority Papers</h2>
"""
    if high_priority:
        for paper in high_priority:
            html += _render_paper(paper, "high-priority", config, worker_url, signing_secret)
    else:
        html += '<p class="section-empty">No high priority papers this week.</p>'

    # Moderate Section
    html += """
        <h2>Moderate Relevance</h2>
"""
    if moderate:
        for paper in moderate[:10]:  # Limit to 10
            html += _render_paper(paper, "moderate", config, worker_url, signing_secret)
        if len(moderate) > 10:
            html += f'<p class="section-empty">...and {len(moderate) - 10} more moderate papers.</p>'
    else:
        html += '<p class="section-empty">No moderate relevance papers this week.</p>'

    # Low Priority (collapsible with <details>)
    if low_priority:
        html += f"""
        <h2>Low Priority ({len(low_priority)} papers)</h2>
        <details style="margin-top: 10px;">
            <summary style="cursor: pointer; color: #3498db; font-size: 14px; padding: 10px 0;">
                Click to show {len(low_priority)} lower-relevance papers
            </summary>
            <div style="margin-top: 10px;">
"""
        for paper in low_priority[:20]:
            html += _render_paper(paper, "low-priority", config, worker_url, signing_secret, compact=True)
        if len(low_priority) > 20:
            html += f'<p class="section-empty">...and {len(low_priority) - 20} more papers.</p>'
        html += """
            </div>
        </details>
"""

    # Footer
    html += f"""
        <div class="footer">
            Generated by Literature Monitor<br>
            {datetime.now().strftime("%Y-%m-%d %H:%M")}
        </div>
    </div>
</body>
</html>
"""

    return html


def _render_paper(
    paper: Paper,
    priority_class: str,
    config: Config,
    worker_url: Optional[str] = None,
    signing_secret: Optional[str] = None,
    compact: bool = False,
) -> str:
    """Render a single paper as HTML."""

    # Score badge
    score = paper.relevance_score or 0
    if score >= 0.7:
        score_class = "score-high"
    elif score >= 0.4:
        score_class = "score-moderate"
    else:
        score_class = "score-low"

    # Check for watched authors
    watched = []
    for author in paper.authors:
        for wa in config.watched_authors:
            if wa.lower() in author.lower():
                watched.append(author)
                break

    # Authors string
    if len(paper.authors) > 3:
        authors_str = ", ".join(paper.authors[:3]) + f" et al."
    else:
        authors_str = ", ".join(paper.authors) if paper.authors else "Unknown authors"

    # Add watched author badges
    for w in watched:
        authors_str = authors_str.replace(w, f'{w}<span class="watched-author">Watched</span>')

    html = f"""
        <div class="paper {priority_class}">
            <div class="paper-title">
                <span class="score-badge {score_class}">{score:.0%}</span>
                <a href="{paper.url}" target="_blank">{paper.title}</a>
            </div>
            <div class="paper-meta">
                {authors_str}<br>
                <span class="journal">{paper.journal}</span> &middot;
                <span class="date">{paper.pub_date}</span>
                {' &middot; <span class="open-access">Open Access</span>' if paper.is_open_access else ''}
            </div>
"""

    if not compact:
        if paper.summary:
            html += f"""
            <div class="paper-summary">
                <strong>Summary:</strong> {paper.summary}
            </div>
"""

        if paper.ranking_rationale:
            html += f"""
            <div class="paper-rationale">
                {paper.ranking_rationale}
            </div>
"""

        if paper.matched_projects:
            html += '<div class="paper-projects">'
            for project in paper.matched_projects:
                html += f'<span class="project-tag">{project}</span>'
            html += '</div>'

    # Links
    zotero_link = generate_zotero_link(paper, worker_url, signing_secret)
    html += f"""
            <div class="paper-links">
                <a href="{paper.url}" target="_blank">View Paper</a>
                {f'<a href="{paper.full_text_url}" target="_blank">Full Text (PDF)</a>' if paper.full_text_url else ''}
                {f'<a href="https://doi.org/{paper.doi}" target="_blank">DOI</a>' if paper.doi else ''}
                <a href="{zotero_link}" target="_blank">Add to Zotero</a>
            </div>
        </div>
"""

    return html


def save_digest(
    html: str,
    output_dir: str | Path = "output",
    filename: Optional[str] = None,
) -> Path:
    """
    Save the digest HTML to a file.

    Args:
        html: HTML content.
        output_dir: Directory to save to.
        filename: Optional filename (defaults to digest_YYYY-MM-DD.html).

    Returns:
        Path to the saved file.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if filename is None:
        filename = f"digest_{datetime.now().strftime('%Y-%m-%d')}.html"

    output_path = output_dir / filename
    output_path.write_text(html, encoding="utf-8")

    return output_path


def send_digest_email(
    html: str,
    subject: str,
    to_email: str,
    from_email: Optional[str] = None,
    smtp_host: Optional[str] = None,
    smtp_port: int = 587,
    smtp_user: Optional[str] = None,
    smtp_password: Optional[str] = None,
) -> bool:
    """
    Send the digest via SMTP.

    Args:
        html: HTML content.
        subject: Email subject.
        to_email: Recipient email.
        from_email: Sender email (defaults to EMAIL_FROM env var).
        smtp_host: SMTP server (defaults to SMTP_HOST env var).
        smtp_port: SMTP port (defaults to 587).
        smtp_user: SMTP username (defaults to SMTP_USER env var).
        smtp_password: SMTP password (defaults to SMTP_PASSWORD env var).

    Returns:
        True if sent successfully, False otherwise.
    """
    # Load from environment if not provided
    from_email = from_email or os.getenv("EMAIL_FROM")
    smtp_host = smtp_host or os.getenv("SMTP_HOST")
    smtp_user = smtp_user or os.getenv("SMTP_USER")
    smtp_password = smtp_password or os.getenv("SMTP_PASSWORD")

    if not all([from_email, smtp_host, smtp_user, smtp_password]):
        print("SMTP not configured. Set EMAIL_FROM, SMTP_HOST, SMTP_USER, SMTP_PASSWORD.")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_email
        msg["To"] = to_email

        # Plain text fallback
        text_content = f"View this email in HTML format.\n\nSubject: {subject}"
        msg.attach(MIMEText(text_content, "plain"))
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(from_email, to_email, msg.as_string())

        return True

    except Exception as e:
        print(f"Failed to send email: {e}")
        return False


def generate_digest_markdown(
    papers: list[Paper],
    config: Config,
    title: str = "Literature Monitor Digest",
) -> str:
    """
    Generate a Markdown version of the digest for Capacities.

    Args:
        papers: List of ranked papers (should be sorted by relevance).
        config: Application config.
        title: Digest title.

    Returns:
        Markdown string.
    """
    # Categorize papers
    high_priority = [p for p in papers if p.relevance_score is not None and p.relevance_score >= 0.7]
    moderate = [p for p in papers if p.relevance_score is not None and 0.4 <= p.relevance_score < 0.7]
    low_priority = [p for p in papers if p.relevance_score is not None and p.relevance_score < 0.4]

    date_str = datetime.now().strftime("%B %d, %Y")

    md = f"# {title}\n\n"
    md += f"*{date_str}*\n\n"

    # Stats
    total = len(papers)
    md += f"**{total} papers** | "
    md += f"**{len(high_priority)} high priority** | "
    md += f"**{len(moderate)} moderate** | "
    md += f"**{len(low_priority)} low priority**\n\n"

    md += "---\n\n"

    # High Priority
    if high_priority:
        md += "## High Priority\n\n"
        for paper in high_priority:
            md += _render_paper_markdown(paper, config)
        md += "\n"

    # Moderate
    if moderate:
        md += "## Moderate Relevance\n\n"
        for paper in moderate[:10]:
            md += _render_paper_markdown(paper, config)
        if len(moderate) > 10:
            md += f"*...and {len(moderate) - 10} more moderate papers.*\n\n"

    # Low Priority (abbreviated)
    if low_priority:
        md += f"## Low Priority ({len(low_priority)} papers)\n\n"
        for paper in low_priority[:5]:
            md += _render_paper_markdown(paper, config, compact=True)
        if len(low_priority) > 5:
            md += f"*...and {len(low_priority) - 5} more low priority papers.*\n\n"

    return md


def _render_paper_markdown(
    paper: Paper,
    config: Config,
    compact: bool = False,
) -> str:
    """Render a single paper as Markdown (no URLs to avoid Capacities auto-linking)."""
    score = paper.relevance_score or 0
    score_str = f"{score:.0%}"

    # Authors
    if len(paper.authors) > 3:
        authors_str = ", ".join(paper.authors[:3]) + " et al."
    else:
        authors_str = ", ".join(paper.authors) if paper.authors else "Unknown authors"

    # Check for watched authors
    watched = []
    for author in paper.authors:
        for wa in config.watched_authors:
            if wa.lower() in author.lower():
                watched.append(author)
                break

    md = f"### {paper.title}\n\n"
    md += f"**Score: {score_str}** | {authors_str}\n\n"
    md += f"*{paper.journal}* — {paper.pub_date}"

    if watched:
        md += f" | **Watched:** {', '.join(watched)}"

    if paper.is_open_access:
        md += " | Open Access"

    # Add DOI as plain text reference (not a link)
    if paper.doi:
        md += f" | DOI: {paper.doi}"

    md += "\n\n"

    if not compact:
        if paper.summary:
            md += f"> {paper.summary}\n\n"

        if paper.ranking_rationale:
            md += f"*{paper.ranking_rationale}*\n\n"

        if paper.matched_projects:
            md += f"**Projects:** {', '.join(paper.matched_projects)}\n\n"

    md += "---\n\n"

    return md


def save_to_capacities_daily_note(
    markdown_content: str,
    api_token: Optional[str] = None,
    space_id: Optional[str] = None,
) -> bool:
    """
    Save content to Capacities daily note.

    Args:
        markdown_content: Markdown text to append to daily note.
        api_token: Capacities API token (defaults to CAPACITIES_API_TOKEN env var).
        space_id: Capacities space ID (defaults to CAPACITIES_SPACE_ID env var).

    Returns:
        True if saved successfully, False otherwise.
    """
    api_token = api_token or os.getenv("CAPACITIES_API_TOKEN")
    space_id = space_id or os.getenv("CAPACITIES_SPACE_ID")

    if not api_token or not space_id:
        print("Capacities not configured. Set CAPACITIES_API_TOKEN and CAPACITIES_SPACE_ID.")
        return False

    try:
        response = requests.post(
            f"{CAPACITIES_API_BASE}/save-to-daily-note",
            headers={
                "Authorization": f"Bearer {api_token}",
                "Content-Type": "application/json",
            },
            json={
                "spaceId": space_id,
                "mdText": markdown_content,
                "noTimeStamp": True,  # We include our own header
            },
            timeout=30,
        )

        print(f"Capacities API response: {response.status_code} - {response.text}")
        if response.status_code == 200:
            return True
        else:
            return False

    except Exception as e:
        print(f"Failed to save to Capacities: {e}")
        return False


def generate_and_save_digest(
    db: PaperDatabase,
    config: Config,
    days: int = 7,
    min_score: float = 0.0,
    output_dir: str = "output",
    send_email: bool = False,
    to_email: Optional[str] = None,
    save_to_capacities: bool = False,
) -> Path:
    """
    Generate a digest from recent ranked papers and save/send it.

    Args:
        db: Database instance.
        config: Application config.
        days: Number of days to include.
        min_score: Minimum relevance score.
        output_dir: Directory for HTML output.
        send_email: Whether to also send via email.
        to_email: Override recipient (defaults to config/env).
        save_to_capacities: Whether to save to Capacities daily note.

    Returns:
        Path to the saved HTML file.
    """
    # Get recent ranked papers
    papers = db.get_recent_papers(days=days, min_score=min_score if min_score > 0 else None)

    # Filter to only ranked papers and sort by score
    ranked = [p for p in papers if p.relevance_score is not None]
    ranked.sort(key=lambda p: p.relevance_score or 0, reverse=True)

    if not ranked:
        print(f"No ranked papers found in the last {days} days.")
        return None

    # Get worker URL and signing secret from environment
    worker_url = os.getenv("ZOTERO_WORKER_URL")
    signing_secret = os.getenv("SIGNING_SECRET")

    if worker_url and not signing_secret:
        print("Warning: ZOTERO_WORKER_URL set but SIGNING_SECRET missing. Zotero links will not work.")

    # Generate HTML
    title = f"Literature Monitor - Week of {datetime.now().strftime('%B %d, %Y')}"
    html = generate_digest_html(
        ranked, config,
        title=title,
        worker_url=worker_url,
        signing_secret=signing_secret,
    )

    # Save to file
    output_path = save_digest(html, output_dir=output_dir)
    print(f"Digest saved to: {output_path}")

    # Optionally send email
    if send_email:
        to = to_email or config.email_to or os.getenv("EMAIL_TO")
        if to:
            if send_digest_email(html, subject=title, to_email=to):
                print(f"Digest emailed to: {to}")
            else:
                print("Failed to send email (check SMTP settings)")
        else:
            print("No recipient email configured (set EMAIL_TO)")

    # Optionally save to Capacities daily note
    if save_to_capacities:
        capacities_token = os.getenv("CAPACITIES_API_TOKEN")
        capacities_space = os.getenv("CAPACITIES_SPACE_ID")

        if capacities_token and capacities_space:
            markdown = generate_digest_markdown(ranked, config, title=title)
            if save_to_capacities_daily_note(markdown):
                print("Digest saved to Capacities daily note")
            else:
                print("Failed to save to Capacities (check API settings)")
        # Silently skip if not configured (not everyone uses Capacities)

    return output_path


if __name__ == "__main__":
    from .config_loader import load_config
    from .database import PaperDatabase

    config = load_config()
    db = PaperDatabase()

    output_path = generate_and_save_digest(db, config, days=30)
    if output_path:
        print(f"\nOpen in browser: file://{output_path.absolute()}")
