"""
Config suggestion system based on user feedback patterns.

Analyzes starred/dismissed papers and suggests improvements to
search queries, project keywords, watched authors, and new projects.
"""

import json
import os
from typing import Optional

import anthropic

from .config_loader import Config
from .database import PaperDatabase


def generate_suggestions(config: Config, db: PaperDatabase) -> list[dict]:
    """
    Generate config suggestions by analyzing feedback patterns.

    Requires at least 5 starred papers to have enough signal.
    Sends one Claude API call with current config and feedback data.

    Args:
        config: Current application config.
        db: Database instance.

    Returns:
        List of suggestion dicts, also saved to the database.
    """
    feedback_stats = db.get_feedback_stats()

    if feedback_stats["starred"] < 5:
        return []

    starred = db.get_starred_papers(limit=30)
    dismissed = db.get_dismissed_papers(limit=30)

    # Build context for Claude
    prompt = _build_suggestion_prompt(config, starred, dismissed, feedback_stats)

    # Call Claude
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2048,
        system="You are an expert at optimizing literature search configurations for researchers. Analyze the provided feedback and suggest improvements. Respond ONLY with valid JSON.",
        messages=[{"role": "user", "content": prompt}],
    )

    content = response.content[0].text.strip()

    # Handle markdown code blocks
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
        content = content.strip()

    try:
        suggestions_data = json.loads(content)
    except json.JSONDecodeError:
        return []

    if not isinstance(suggestions_data, list):
        suggestions_data = suggestions_data.get("suggestions", [])

    # Save to database
    saved = []
    for s in suggestions_data:
        suggestion_type = s.get("type", "search_query")
        suggestion_text = s.get("text", "")
        suggestion_data = s.get("data")
        rationale = s.get("rationale", "")

        if suggestion_text:
            db.add_config_suggestion(
                suggestion_type=suggestion_type,
                suggestion_text=suggestion_text,
                suggestion_data=suggestion_data,
                rationale=rationale,
            )
            saved.append({
                "suggestion_type": suggestion_type,
                "suggestion_text": suggestion_text,
                "suggestion_data": suggestion_data,
                "rationale": rationale,
            })

    return saved


def _build_suggestion_prompt(
    config: Config,
    starred: list,
    dismissed: list,
    stats: dict,
) -> str:
    """Build the prompt for generating config suggestions."""
    # Current config summary
    queries = "\n".join(f"  - {q}" for q in config.search_queries)
    projects = "\n".join(
        f"  - {p.name}: {', '.join(p.keywords)}"
        for p in config.active_projects
    )
    authors = "\n".join(f"  - {a}" for a in config.watched_authors) if config.watched_authors else "  (none)"

    # Starred papers summary
    starred_text = ""
    for p in starred[:20]:
        proj = f" [Projects: {', '.join(p.matched_projects)}]" if p.matched_projects else ""
        starred_text += f"  - \"{p.title}\" ({p.journal}){proj}\n"
        if p.abstract:
            starred_text += f"    Abstract excerpt: {p.abstract[:200]}...\n"

    # Dismissed papers summary
    dismissed_text = ""
    for p in dismissed[:15]:
        proj = f" [Projects: {', '.join(p.matched_projects)}]" if p.matched_projects else ""
        score = f", score: {p.relevance_score:.2f}" if p.relevance_score is not None else ""
        dismissed_text += f"  - \"{p.title}\" ({p.journal}{score}){proj}\n"

    return f"""Analyze this literature monitoring configuration and the researcher's feedback to suggest improvements.

## Current Configuration

Search queries:
{queries}

Active projects:
{projects}

Watched authors:
{authors}

## Feedback Statistics
- Starred (valuable): {stats['starred']}
- Dismissed (not relevant): {stats['dismissed']}
- Neutral (no feedback): {stats['neutral']}

## Starred Papers (researcher found these valuable):
{starred_text}

## Dismissed Papers (researcher found these NOT relevant):
{dismissed_text}

## Instructions

Identify gaps and suggest improvements. For each suggestion, provide:
1. What topics or patterns appear in starred papers but aren't well-covered by current search queries?
2. Are there keywords from starred papers that should be added to projects?
3. Are there authors who appear frequently in starred papers who should be watched?
4. Are there overly broad terms catching irrelevant papers (from dismissed)?
5. Should any new projects be created based on emerging interest patterns?

Respond with a JSON array of suggestions. Each suggestion must have:
- "type": one of "search_query", "project_keyword", "watched_author", "new_project"
- "text": human-readable description of the suggestion
- "data": object for auto-applying (see formats below)
- "rationale": why this suggestion is made

Data formats:
- search_query: {{"query": "the PubMed query string"}}
- project_keyword: {{"project": "project name", "keyword": "new keyword"}}
- watched_author: {{"author": "LastName Initials"}}
- new_project: {{"name": "Project Name", "keywords": ["kw1", "kw2"]}}

Return 3-8 suggestions. Only suggest things that are clearly supported by the feedback patterns.
Return ONLY the JSON array, no other text."""
