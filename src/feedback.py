"""
Feedback loop for literature monitoring.

Builds feedback examples for the ranking prompt and syncs
feedback from the Cloudflare Worker.
"""

import os
from typing import Optional

import requests

from .database import PaperDatabase
from .sources.pubmed import Paper


def build_feedback_prompt_section(db: PaperDatabase) -> Optional[str]:
    """
    Build a prompt section with feedback examples for calibrating Claude's ranking.

    Selects up to 5 starred and 5 dismissed papers as examples, preferring:
    - Recent papers first
    - Diversity across projects
    - Papers where score disagreed with feedback (most informative)

    Args:
        db: Database instance.

    Returns:
        Prompt section string, or None if no feedback exists.
    """
    starred = db.get_starred_papers(limit=20)
    dismissed = db.get_dismissed_papers(limit=20)

    if not starred and not dismissed:
        return None

    starred_examples = _select_examples(starred, max_count=5)
    dismissed_examples = _select_examples(dismissed, max_count=5)

    if not starred_examples and not dismissed_examples:
        return None

    lines = [
        "",
        "The researcher has provided feedback on past papers. Use these to calibrate:",
    ]

    if starred_examples:
        lines.append("")
        lines.append("Papers the researcher STARRED (found highly valuable):")
        for paper in starred_examples:
            lines.append(_format_example(paper))

    if dismissed_examples:
        lines.append("")
        lines.append("Papers the researcher DISMISSED (not relevant):")
        for paper in dismissed_examples:
            lines.append(_format_example(paper))

    lines.append("")
    lines.append("Adjust your scoring to better match these demonstrated preferences.")

    return "\n".join(lines)


def _select_examples(papers: list[Paper], max_count: int = 5) -> list[Paper]:
    """
    Select diverse, informative examples from a list of papers.

    Prioritizes papers where the score disagreed with feedback (e.g., high-scored
    but dismissed, or low-scored but starred) as these are most calibrating.
    Also ensures diversity across projects.
    """
    if not papers:
        return []

    # Score by informativeness: papers where score disagrees with feedback
    def informativeness(paper):
        score = paper.relevance_score or 0.5
        is_starred = paper._user_feedback == 'star'
        # For starred papers, lower scores are more informative
        # For dismissed papers, higher scores are more informative
        if is_starred:
            return 1.0 - score  # Low score + star = very informative
        else:
            return score  # High score + dismiss = very informative

    # Sort by informativeness (most informative first)
    sorted_papers = sorted(papers, key=informativeness, reverse=True)

    # Select diverse examples across projects
    selected = []
    seen_projects = set()

    for paper in sorted_papers:
        if len(selected) >= max_count:
            break

        # Prefer papers from projects not yet represented
        paper_projects = frozenset(paper.matched_projects) if paper.matched_projects else frozenset()
        is_new_project = not paper_projects or not paper_projects.issubset(seen_projects)

        if is_new_project or len(selected) < max_count:
            selected.append(paper)
            seen_projects.update(paper_projects)

    return selected


def _format_example(paper: Paper) -> str:
    """Format a paper as a concise example line for the prompt."""
    score_str = f"score was {paper.relevance_score:.2f}" if paper.relevance_score is not None else "unscored"
    projects_str = ""
    if paper.matched_projects:
        projects_str = f" [Projects: {', '.join(paper.matched_projects)}]"

    # Truncate title to ~60 chars
    title = paper.title
    if len(title) > 60:
        title = title[:57] + "..."

    return f'- "{title}" ({paper.journal}, {score_str}){projects_str}'


def sync_worker_feedback(db: PaperDatabase) -> int:
    """
    Sync feedback from the Cloudflare Worker KV store.

    Pulls pending feedback entries from the Worker, applies them to the
    local database, and acknowledges them so they aren't re-processed.

    Args:
        db: Database instance.

    Returns:
        Number of feedback entries synced.
    """
    worker_url = os.getenv("ZOTERO_WORKER_URL")
    feedback_key = os.getenv("FEEDBACK_API_KEY")

    if not worker_url or not feedback_key:
        return 0

    # Fetch pending feedback from Worker
    try:
        response = requests.get(
            f"{worker_url}/feedback/pending",
            params={"key": feedback_key},
            timeout=15,
        )

        if response.status_code != 200:
            return 0

        entries = response.json().get("entries", [])
        if not entries:
            return 0

    except Exception:
        return 0

    # Apply each feedback entry
    processed_keys = []
    for entry in entries:
        paper_id = entry.get("paper_id")
        action = entry.get("action")
        kv_key = entry.get("key")

        if paper_id and action in ("star", "dismiss"):
            db.set_feedback(paper_id, action)
            if kv_key:
                processed_keys.append(kv_key)

    # Acknowledge processed entries
    if processed_keys:
        try:
            requests.post(
                f"{worker_url}/feedback/ack",
                json={"keys": processed_keys, "key": feedback_key},
                timeout=15,
            )
        except Exception:
            pass  # Best effort acknowledgement

    return len(processed_keys)
