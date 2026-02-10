#!/usr/bin/env python3
"""
Literature Monitor - Main entry point.

Searches PubMed and bioRxiv for papers relevant to configured research topics,
ranks them using Claude, and generates a digest.

Usage:
    python main.py                  # Run full pipeline
    python main.py --search-only    # Just search and display results
    python main.py --dry-run        # Search without saving to database
    python main.py --stats          # Show database statistics
    python main.py --rank-only      # Rank unranked papers in database
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

from src.config_loader import load_config
from src.database import PaperDatabase
from src.sources import PubMedClient, BioRxivClient


def format_paper_output(paper, config, is_new: bool = True, show_ranking: bool = False) -> str:
    """Format a paper for terminal output."""
    lines = []
    lines.append(f"\n{'='*80}")

    # Status prefix
    if show_ranking and paper.relevance_score is not None:
        score_bar = "█" * int(paper.relevance_score * 10) + "░" * (10 - int(paper.relevance_score * 10))
        status = f"[{score_bar} {paper.relevance_score:.2f}] "
    elif is_new:
        status = "[NEW] "
    else:
        status = "[SEEN] "

    lines.append(f"{status}TITLE: {paper.title}")
    lines.append(f"{'='*80}")

    # Authors (truncate if many)
    if len(paper.authors) > 5:
        author_str = ", ".join(paper.authors[:5]) + f" ... (+{len(paper.authors)-5} more)"
    else:
        author_str = ", ".join(paper.authors) if paper.authors else "No authors listed"
    lines.append(f"Authors: {author_str}")

    # Check for watched authors
    watched = []
    for author in paper.authors:
        for watched_author in config.watched_authors:
            if watched_author.lower() in author.lower():
                watched.append(author)
    if watched:
        lines.append(f"  ** WATCHED AUTHOR(S): {', '.join(watched)} **")

    lines.append(f"Journal: {paper.journal}")
    journal_weight = config.get_journal_weight(paper.journal)
    if journal_weight != 1.0:
        tier = "HIGH" if journal_weight > 1.0 else "LOW"
        lines.append(f"  (Journal weight: {journal_weight}x - {tier} trust)")

    lines.append(f"Date: {paper.pub_date}")
    lines.append(f"Source: {paper.source}")
    lines.append(f"URL: {paper.url}")

    if paper.is_open_access:
        lines.append(f"Full text: {paper.full_text_url} [OPEN ACCESS]")

    if paper.doi:
        lines.append(f"DOI: {paper.doi}")

    # Show ranking info if available
    if show_ranking and paper.relevance_score is not None:
        if paper.matched_projects:
            lines.append(f"Matched projects: {', '.join(paper.matched_projects)}")
        lines.append(f"\nClaude's Assessment:")
        lines.append(f"  Relevance: {paper.relevance_score:.2f}")
        lines.append(f"  Rationale: {paper.ranking_rationale}")
        lines.append(f"\nSummary: {paper.summary}")
    else:
        # Check project matches using config
        text = f"{paper.title} {paper.abstract}"
        matched_projects = config.match_projects(text)
        if matched_projects:
            lines.append(f"Matched projects: {', '.join(matched_projects)}")

        # Abstract (truncated)
        lines.append(f"\nAbstract:")
        if paper.abstract:
            abstract = paper.abstract[:500]
            if len(paper.abstract) > 500:
                abstract += "..."
            lines.append(abstract)
        else:
            lines.append("  [No abstract available]")

    return "\n".join(lines)


def run_search(config, args, db: PaperDatabase | None = None):
    """Run the search phase and display results."""
    print("\n" + "="*80)
    print("LITERATURE MONITOR - Search Results")
    print(f"Date: {datetime.now():%Y-%m-%d %H:%M}")
    print("="*80)

    # Initialize clients
    pubmed = PubMedClient()
    biorxiv = BioRxivClient(include_medrxiv=True)

    all_papers = []
    seen_ids = set()  # Track duplicates within this search

    # Search each query
    for query in config.search_queries:
        print(f"\n>> Searching: \"{query}\"")

        # PubMed
        print("  Querying PubMed...")
        try:
            pubmed_papers = pubmed.search_and_fetch(
                query,
                max_results=config.max_results_per_query,
                days_back=config.days_lookback,
            )
            new_count = 0
            for paper in pubmed_papers:
                if paper.id not in seen_ids:
                    seen_ids.add(paper.id)
                    all_papers.append(paper)
                    new_count += 1
            print(f"    Found {len(pubmed_papers)} papers ({new_count} unique)")
        except Exception as e:
            print(f"    Error searching PubMed: {e}")

        # bioRxiv/medRxiv
        if not args.pubmed_only:
            print("  Querying bioRxiv/medRxiv...")
            try:
                biorxiv_papers = biorxiv.search_and_fetch(
                    query,
                    max_results=config.max_results_per_query,
                    days_back=config.days_lookback,
                )
                new_count = 0
                for paper in biorxiv_papers:
                    if paper.id not in seen_ids:
                        seen_ids.add(paper.id)
                        all_papers.append(paper)
                        new_count += 1
                print(f"    Found {len(biorxiv_papers)} papers ({new_count} unique)")
            except Exception as e:
                print(f"    Error searching bioRxiv: {e}")

    # Check which papers are already in database
    existing_ids = set()
    if db and not args.dry_run:
        existing_ids = db.get_existing_ids([p.id for p in all_papers])

    new_papers = [p for p in all_papers if p.id not in existing_ids]

    # Summary
    print(f"\n{'='*80}")
    print(f"SEARCH SUMMARY")
    print(f"{'='*80}")
    print(f"Total unique papers found: {len(all_papers)}")
    print(f"  - PubMed: {sum(1 for p in all_papers if p.source == 'pubmed')}")
    print(f"  - bioRxiv: {sum(1 for p in all_papers if p.source == 'biorxiv')}")
    print(f"  - medRxiv: {sum(1 for p in all_papers if p.source == 'medrxiv')}")

    if db and not args.dry_run:
        print(f"New papers (not in database): {len(new_papers)}")
        print(f"Already seen: {len(existing_ids)}")

    # Count papers with watched authors
    papers_with_watched = 0
    for paper in all_papers:
        for author in paper.authors:
            if any(wa.lower() in author.lower() for wa in config.watched_authors):
                papers_with_watched += 1
                break
    print(f"Papers with watched authors: {papers_with_watched}")

    # Count papers matching projects
    papers_with_projects = 0
    for paper in all_papers:
        text = f"{paper.title} {paper.abstract}"
        if config.match_projects(text):
            papers_with_projects += 1
    print(f"Papers matching active projects: {papers_with_projects}")

    # Display papers (prioritize new ones)
    display_papers = new_papers if new_papers else all_papers
    if args.verbose or len(display_papers) <= 10:
        print(f"\n{'='*80}")
        print("PAPERS" + (" (showing new only)" if new_papers and not args.verbose else ""))
        print(f"{'='*80}")
        for paper in display_papers:
            is_new = paper.id not in existing_ids
            print(format_paper_output(paper, config, is_new=is_new))
    else:
        print(f"\n(Showing first 10 of {len(display_papers)} papers. Use --verbose to see all)")
        for paper in display_papers[:10]:
            is_new = paper.id not in existing_ids
            print(format_paper_output(paper, config, is_new=is_new))

    return all_papers, new_papers


def run_ranking(config, db: PaperDatabase, papers_to_rank: list = None, limit: int = None):
    """Run Claude ranking on papers."""
    from src.ranker import rank_and_update_db

    # Get papers to rank
    if papers_to_rank is None:
        papers_to_rank = db.get_unranked_papers()

    if not papers_to_rank:
        print("No unranked papers to process.")
        return []

    # Apply limit if specified
    if limit and len(papers_to_rank) > limit:
        print(f"Limiting to {limit} papers (of {len(papers_to_rank)} unranked)")
        papers_to_rank = papers_to_rank[:limit]

    print(f"\n{'='*80}")
    print("RANKING PAPERS WITH CLAUDE")
    print(f"{'='*80}")

    results = rank_and_update_db(papers_to_rank, config, db, verbose=True)

    # Display top results
    print(f"\n{'='*80}")
    print("TOP RANKED PAPERS")
    print(f"{'='*80}")

    high_priority = [(p, r) for p, r in results if r.relevance_score >= 0.7]
    moderate = [(p, r) for p, r in results if 0.4 <= r.relevance_score < 0.7]
    low = [(p, r) for p, r in results if r.relevance_score < 0.4]

    print(f"\nHigh Priority ({len(high_priority)} papers, score >= 0.7):")
    for paper, result in high_priority[:5]:
        print(f"  [{result.relevance_score:.2f}] {paper.title[:65]}...")
        if result.matched_projects:
            print(f"         Projects: {', '.join(result.matched_projects)}")

    print(f"\nModerate ({len(moderate)} papers, score 0.4-0.7):")
    for paper, result in moderate[:3]:
        print(f"  [{result.relevance_score:.2f}] {paper.title[:65]}...")

    print(f"\nLow Priority ({len(low)} papers, score < 0.4):")
    if low:
        print(f"  (showing {min(3, len(low))} of {len(low)})")
        for paper, result in low[:3]:
            print(f"  [{result.relevance_score:.2f}] {paper.title[:65]}...")

    return results


def show_stats(db: PaperDatabase):
    """Display database statistics."""
    stats = db.get_stats()
    print("\n" + "="*80)
    print("DATABASE STATISTICS")
    print("="*80)
    print(f"Total papers: {stats['total_papers']}")
    print(f"By source:")
    for source, count in stats.get('by_source', {}).items():
        print(f"  - {source}: {count}")
    print(f"Ranked papers: {stats['ranked_papers']}")
    print(f"High priority (score >= 0.7): {stats['high_priority']}")
    print(f"Total search runs: {stats['total_runs']}")

    # Recent runs
    runs = db.get_search_runs(limit=5)
    if runs:
        print(f"\nRecent search runs:")
        for run in runs:
            print(f"  {run.run_date[:16]}: found {run.papers_found}, new {run.new_papers}")


def show_ranked_papers(config, db: PaperDatabase, min_score: float = 0.0, limit: int = 20):
    """Display ranked papers from the database."""
    papers = db.get_recent_papers(days=30, min_score=min_score if min_score > 0 else None)

    if not papers:
        print("No ranked papers found.")
        return

    # Filter to only ranked papers
    ranked = [p for p in papers if p.relevance_score is not None]
    ranked.sort(key=lambda p: p.relevance_score or 0, reverse=True)

    print(f"\n{'='*80}")
    print(f"TOP RANKED PAPERS (last 30 days, score >= {min_score})")
    print(f"{'='*80}")

    for paper in ranked[:limit]:
        print(format_paper_output(paper, config, is_new=False, show_ranking=True))


def main():
    parser = argparse.ArgumentParser(
        description="Literature Monitor - Track relevant papers from PubMed and bioRxiv"
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to config.yaml (default: config/config.yaml)",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="Path to database file (default: data/papers.db)",
    )
    parser.add_argument(
        "--search-only",
        action="store_true",
        help="Only run search, don't process with Claude or send email",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without saving to database",
    )
    parser.add_argument(
        "--pubmed-only",
        action="store_true",
        help="Only search PubMed (skip bioRxiv/medRxiv)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show all papers (not just first 10)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Override days_lookback from config",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show database statistics and exit",
    )
    parser.add_argument(
        "--rank-only",
        action="store_true",
        help="Only rank unranked papers in database (skip search)",
    )
    parser.add_argument(
        "--skip-ranking",
        action="store_true",
        help="Skip the Claude ranking step",
    )
    parser.add_argument(
        "--rank-limit",
        type=int,
        default=None,
        help="Maximum number of papers to rank (for cost control)",
    )
    parser.add_argument(
        "--show-ranked",
        action="store_true",
        help="Show top ranked papers from database and exit",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.0,
        help="Minimum relevance score filter (default: 0.0)",
    )
    parser.add_argument(
        "--digest",
        action="store_true",
        help="Generate HTML digest of ranked papers and exit",
    )
    parser.add_argument(
        "--send-email",
        action="store_true",
        help="Send digest via email (requires SMTP config)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="output",
        help="Directory for digest output (default: output/)",
    )
    parser.add_argument(
        "--add-seed",
        type=str,
        metavar="DOI_OR_PMID",
        help="Add a seed paper by DOI or PMID",
    )
    parser.add_argument(
        "--suggest-config",
        action="store_true",
        help="Generate config suggestions based on feedback",
    )
    parser.add_argument(
        "--sync-zotero",
        action="store_true",
        help="Sync Zotero library as seed papers",
    )
    parser.add_argument(
        "--zotero-tag",
        type=str,
        default=None,
        help="Only sync Zotero items with this tag (use with --sync-zotero)",
    )
    parser.add_argument(
        "--sync-feedback",
        action="store_true",
        help="Sync feedback from Cloudflare Worker",
    )

    args = parser.parse_args()

    # Check for API key if ranking will be needed
    if not args.search_only and not args.skip_ranking and not args.stats and not args.show_ranked and not args.digest:
        if not os.getenv("ANTHROPIC_API_KEY"):
            print("Warning: ANTHROPIC_API_KEY not set. Ranking will be skipped.")
            print("Set the environment variable or use --skip-ranking")
            args.skip_ranking = True

    # Initialize database
    db = PaperDatabase(args.db)

    # Stats mode
    if args.stats:
        show_stats(db)
        return

    # Add seed paper mode
    if args.add_seed:
        from src.paper_lookup import lookup_paper
        print(f"Looking up: {args.add_seed}")
        paper, source = lookup_paper(args.add_seed)
        if paper:
            is_new = db.insert_seed_paper(paper, source=source)
            print(f"{'Added' if is_new else 'Updated'} seed paper: {paper.title}")
            print(f"  Source: {source}")
            print(f"  Authors: {', '.join(paper.authors[:3])}{'...' if len(paper.authors) > 3 else ''}")
            print(f"  Journal: {paper.journal}")
            if paper.doi:
                print(f"  DOI: {paper.doi}")
        else:
            print(f"Could not find paper for: {args.add_seed}")
            sys.exit(1)
        return

    # Sync feedback from Worker
    if args.sync_feedback:
        from src.feedback import sync_worker_feedback
        count = sync_worker_feedback(db)
        print(f"Synced {count} feedback entries from Worker")
        return

    # Suggest config
    if args.suggest_config:
        config = load_config(args.config)
        from src.config_suggester import generate_suggestions
        suggestions = generate_suggestions(config, db)
        if suggestions:
            print(f"\nGenerated {len(suggestions)} suggestions:")
            for s in suggestions:
                print(f"  [{s['suggestion_type']}] {s['suggestion_text']}")
                print(f"    Rationale: {s['rationale']}")
        else:
            print("No suggestions generated (need at least 5 starred papers).")
        return

    # Sync Zotero library
    if args.sync_zotero:
        from src.zotero_sync import sync_zotero_library
        zotero_key = os.getenv("ZOTERO_API_KEY")
        zotero_user = os.getenv("ZOTERO_USER_ID")
        if not zotero_key or not zotero_user:
            print("Error: ZOTERO_API_KEY and ZOTERO_USER_ID must be set in .env")
            sys.exit(1)
        count = sync_zotero_library(zotero_key, zotero_user, db, tag_filter=args.zotero_tag)
        print(f"Synced {count} papers from Zotero as seeds")
        return

    # Load config
    try:
        config = load_config(args.config)
        print(f"Loaded config with {len(config.search_queries)} queries")
    except FileNotFoundError as e:
        print(f"Error: {e}")
        print("Create a config file at config/config.yaml or specify --config")
        sys.exit(1)

    # Show ranked papers mode
    if args.show_ranked:
        show_ranked_papers(config, db, min_score=args.min_score)
        return

    # Digest mode
    if args.digest:
        from src.email_digest import generate_and_save_digest
        days = args.days or 7
        output_path = generate_and_save_digest(
            db, config,
            days=days,
            min_score=args.min_score,
            output_dir=args.output_dir,
            send_email=args.send_email,
        )
        if output_path:
            print(f"\nOpen in browser: file://{output_path.absolute()}")
        return

    # Sync feedback from Worker before any ranking
    worker_url = os.getenv("ZOTERO_WORKER_URL")
    feedback_key = os.getenv("FEEDBACK_API_KEY")
    if worker_url and feedback_key:
        try:
            from src.feedback import sync_worker_feedback
            count = sync_worker_feedback(db)
            if count > 0:
                print(f"Synced {count} feedback entries from Worker")
        except Exception as e:
            print(f"Warning: Could not sync Worker feedback: {e}")

    # Rank-only mode
    if args.rank_only:
        run_ranking(config, db, limit=args.rank_limit)
        return

    # Override days if specified
    if args.days:
        config.days_lookback = args.days

    # Run search
    all_papers, new_papers = run_search(config, args, db=db)

    # Save to database
    if not args.dry_run and not args.search_only:
        print(f"\n{'='*80}")
        print("SAVING TO DATABASE")
        print(f"{'='*80}")

        total, inserted = db.insert_papers(all_papers)
        print(f"Inserted {inserted} new papers (of {total} found)")

        # Record the search run
        high_priority = sum(
            1 for p in new_papers
            if any(
                wa.lower() in a.lower()
                for a in p.authors
                for wa in config.watched_authors
            ) or config.match_projects(f"{p.title} {p.abstract}")
        )
        run_id = db.record_search_run(
            papers_found=total,
            new_papers=inserted,
            high_priority_count=high_priority,
        )
        print(f"Recorded search run #{run_id}")

    if args.search_only:
        print("\n[--search-only mode, skipping ranking and email]")
        return

    if args.dry_run:
        print("\n[--dry-run mode, skipping database save]")
        return

    # Run ranking on new papers
    if not args.skip_ranking and new_papers:
        # Re-fetch from database to get proper Paper objects
        papers_to_rank = [db.get_paper(p.id) for p in new_papers]
        papers_to_rank = [p for p in papers_to_rank if p is not None]
        run_ranking(config, db, papers_to_rank=papers_to_rank, limit=args.rank_limit)
    elif args.skip_ranking:
        print("\n[--skip-ranking mode, skipping Claude ranking]")
    else:
        print("\nNo new papers to rank.")

    # Generate digest if email sending is requested
    if args.send_email:
        from src.email_digest import generate_and_save_digest
        days = args.days or 7
        output_path = generate_and_save_digest(
            db, config,
            days=days,
            min_score=args.min_score,
            output_dir=args.output_dir,
            send_email=True,
        )

    print(f"\nDatabase: {db.db_path}")
    print("Use --digest to generate HTML digest, --show-ranked for terminal view")


if __name__ == "__main__":
    main()
