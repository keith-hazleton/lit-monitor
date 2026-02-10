"""
Paper ranker using Claude API for summarization and relevance scoring.
"""

import json
import os
from dataclasses import dataclass
from typing import Optional

import anthropic

from .config_loader import Config
from .sources.pubmed import Paper


@dataclass
class RankingResult:
    """Result of ranking a paper."""
    summary: str
    relevance_score: float  # 0.0 to 1.0
    ranking_rationale: str
    matched_projects: list[str]


class PaperRanker:
    """Ranks papers using Claude API."""

    def __init__(
        self,
        config: Config,
        api_key: Optional[str] = None,
        model: str = "claude-sonnet-4-20250514",
        db=None,
    ):
        """
        Initialize the ranker.

        Args:
            config: Application config with projects and research context.
            api_key: Anthropic API key (defaults to ANTHROPIC_API_KEY env var).
            model: Claude model to use.
            db: Optional PaperDatabase for feedback-informed ranking.
        """
        self.config = config
        self.model = model
        self.client = anthropic.Anthropic(api_key=api_key or os.getenv("ANTHROPIC_API_KEY"))

        # Build feedback section once at init
        self._feedback_section = None
        if db:
            try:
                from .feedback import build_feedback_prompt_section
                self._feedback_section = build_feedback_prompt_section(db)
            except Exception:
                pass

    def _build_system_prompt(self) -> str:
        """Build the system prompt with research context."""
        projects_text = "\n".join(
            f"- {p.name}: keywords include {', '.join(p.keywords)}"
            for p in self.config.active_projects
        )

        prompt = f"""You are a research assistant helping a pediatric hepatology researcher stay current with the literature. Your task is to evaluate papers for relevance and provide concise summaries.

The researcher's active projects are:
{projects_text}

Key research interests:
- Pediatric liver diseases (biliary atresia, PFIC, Alagille syndrome)
- Liver transplantation in children
- Gut-liver axis and microbiome
- Immune mechanisms in liver disease

When evaluating papers:
1. Consider direct relevance to active projects
2. Consider methodological advances that could apply to their research
3. Consider findings that challenge or support current understanding
4. Papers from watched authors or high-impact journals may warrant slightly higher scores"""

        if self._feedback_section:
            prompt += "\n" + self._feedback_section

        prompt += "\n\nRespond in JSON format only."

        return prompt

    def _build_user_prompt(self, paper: Paper) -> str:
        """Build the user prompt for a single paper."""
        authors_str = ", ".join(paper.authors[:5])
        if len(paper.authors) > 5:
            authors_str += f" et al. ({len(paper.authors)} authors)"

        journal_weight = self.config.get_journal_weight(paper.journal)
        journal_note = ""
        if journal_weight > 1.0:
            journal_note = " (high-impact journal)"
        elif journal_weight < 1.0:
            journal_note = " (lower-tier journal)"

        watched = [
            a for a in paper.authors
            if any(wa.lower() in a.lower() for wa in self.config.watched_authors)
        ]
        author_note = ""
        if watched:
            author_note = f"\n**Note: Paper includes watched author(s): {', '.join(watched)}**"

        return f"""Evaluate this paper:

**Title:** {paper.title}

**Authors:** {authors_str}{author_note}

**Journal:** {paper.journal}{journal_note}

**Publication Date:** {paper.pub_date}

**Abstract:**
{paper.abstract or '[No abstract available]'}

Respond with a JSON object containing:
- "summary": A 2-3 sentence summary focusing on key findings and methods (not just restating the title)
- "relevance_score": A number from 0.0 to 1.0 indicating relevance to the researcher's interests (0.7+ = high priority, 0.4-0.7 = moderate, <0.4 = low)
- "ranking_rationale": A brief explanation of the score (1-2 sentences)
- "matched_projects": An array of project names this paper is relevant to (can be empty)

Available project names: {[p.name for p in self.config.active_projects]}"""

    def rank_paper(self, paper: Paper) -> RankingResult:
        """
        Rank a single paper using Claude.

        Args:
            paper: Paper to rank.

        Returns:
            RankingResult with summary, score, rationale, and matched projects.
        """
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=self._build_system_prompt(),
            messages=[
                {"role": "user", "content": self._build_user_prompt(paper)}
            ],
        )

        # Parse the JSON response
        content = response.content[0].text.strip()

        # Handle potential markdown code blocks
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            print(f"  Warning: Failed to parse ranking response for {paper.id}: {e}")
            print(f"  Response was: {content[:200]}...")
            # Return default values
            return RankingResult(
                summary="[Failed to generate summary]",
                relevance_score=0.5,
                ranking_rationale="[Failed to parse ranking response]",
                matched_projects=[],
            )

        # Apply journal weight modifier to score
        raw_score = float(data.get("relevance_score", 0.5))
        journal_weight = self.config.get_journal_weight(paper.journal)
        # Adjust score but keep it in 0-1 range
        adjusted_score = min(1.0, raw_score * journal_weight)

        return RankingResult(
            summary=data.get("summary", ""),
            relevance_score=adjusted_score,
            ranking_rationale=data.get("ranking_rationale", ""),
            matched_projects=data.get("matched_projects", []),
        )

    def rank_papers(
        self,
        papers: list[Paper],
        callback: Optional[callable] = None,
    ) -> list[tuple[Paper, RankingResult]]:
        """
        Rank multiple papers.

        Args:
            papers: List of papers to rank.
            callback: Optional callback(paper, result, index, total) for progress updates.

        Returns:
            List of (paper, result) tuples sorted by relevance score descending.
        """
        results = []

        for i, paper in enumerate(papers):
            try:
                result = self.rank_paper(paper)
                results.append((paper, result))

                if callback:
                    callback(paper, result, i, len(papers))

            except Exception as e:
                print(f"  Error ranking paper {paper.id}: {e}")
                # Add with default low score
                results.append((paper, RankingResult(
                    summary="[Error during ranking]",
                    relevance_score=0.0,
                    ranking_rationale=f"Error: {str(e)}",
                    matched_projects=[],
                )))

        # Sort by relevance score descending
        results.sort(key=lambda x: x[1].relevance_score, reverse=True)

        return results

    def rank_papers_batch(
        self,
        papers: list[Paper],
        batch_size: int = 5,
    ) -> list[tuple[Paper, RankingResult]]:
        """
        Rank multiple papers in batches (more efficient for many papers).

        Args:
            papers: List of papers to rank.
            batch_size: Number of papers per API call.

        Returns:
            List of (paper, result) tuples sorted by relevance score descending.
        """
        # For now, just call rank_papers - batch processing could be added later
        # using a single prompt with multiple papers
        return self.rank_papers(papers)


def rank_and_update_db(
    papers: list[Paper],
    config: Config,
    db,  # PaperDatabase
    verbose: bool = True,
) -> list[tuple[Paper, RankingResult]]:
    """
    Rank papers and update the database with results.

    Args:
        papers: Papers to rank.
        config: Application config.
        db: Database instance.
        verbose: Whether to print progress.

    Returns:
        Ranked papers with results.
    """
    if not papers:
        print("No papers to rank.")
        return []

    ranker = PaperRanker(config, db=db)

    def progress_callback(paper, result, index, total):
        if verbose:
            score_bar = "█" * int(result.relevance_score * 10) + "░" * (10 - int(result.relevance_score * 10))
            print(f"  [{index+1}/{total}] {score_bar} {result.relevance_score:.2f} - {paper.title[:50]}...")

    if verbose:
        print(f"Ranking {len(papers)} papers with Claude...")

    results = ranker.rank_papers(papers, callback=progress_callback)

    # Update database
    if verbose:
        print(f"\nUpdating database...")

    for paper, result in results:
        db.update_paper_ranking(
            paper_id=paper.id,
            summary=result.summary,
            relevance_score=result.relevance_score,
            ranking_rationale=result.ranking_rationale,
            matched_projects=result.matched_projects,
        )

    if verbose:
        high_priority = sum(1 for _, r in results if r.relevance_score >= 0.7)
        print(f"Ranking complete: {high_priority} high-priority papers (score >= 0.7)")

    return results


if __name__ == "__main__":
    # Quick test
    from .config_loader import load_config
    from .database import PaperDatabase

    config = load_config()
    db = PaperDatabase()

    # Get unranked papers
    unranked = db.get_unranked_papers()
    print(f"Found {len(unranked)} unranked papers")

    if unranked:
        # Rank just the first 3 as a test
        test_papers = unranked[:3]
        results = rank_and_update_db(test_papers, config, db)

        print("\nResults:")
        for paper, result in results:
            print(f"\n{paper.title}")
            print(f"  Score: {result.relevance_score:.2f}")
            print(f"  Summary: {result.summary}")
            print(f"  Rationale: {result.ranking_rationale}")
            print(f"  Projects: {result.matched_projects}")
