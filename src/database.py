"""
SQLite database for tracking papers and avoiding duplicates.
"""

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from .sources.pubmed import Paper


@dataclass
class ConfigSuggestion:
    """A suggestion for config improvement based on feedback patterns."""
    id: int
    suggestion_type: str  # search_query / project_keyword / watched_author / new_project
    suggestion_text: str
    suggestion_data: Optional[dict]
    rationale: str
    status: str  # pending / accepted / dismissed
    created_at: str
    reviewed_at: Optional[str]


def get_default_db_path() -> Path:
    """Get the default database path."""
    return Path(__file__).parent.parent / "data" / "papers.db"


@dataclass
class SearchRun:
    """Record of a search run."""
    id: int
    run_date: str
    papers_found: int
    new_papers: int
    high_priority_count: int


class PaperDatabase:
    """SQLite database for storing and querying papers."""

    def __init__(self, db_path: str | Path | None = None):
        """
        Initialize the database connection.

        Args:
            db_path: Path to SQLite database file. Defaults to data/papers.db.
        """
        if db_path is None:
            db_path = get_default_db_path()
        self.db_path = Path(db_path)

        # Ensure data directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._init_schema()

    @contextmanager
    def _get_conn(self):
        """Get a database connection with row factory."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self):
        """Initialize the database schema."""
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS papers (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    title TEXT,
                    authors TEXT,
                    journal TEXT,
                    pub_date TEXT,
                    abstract TEXT,
                    url TEXT,
                    full_text_url TEXT,
                    is_open_access BOOLEAN,
                    doi TEXT,

                    -- Claude-generated fields
                    summary TEXT,
                    relevance_score REAL,
                    ranking_rationale TEXT,
                    matched_projects TEXT,

                    -- Tracking
                    first_seen_date TEXT,
                    added_to_zotero BOOLEAN DEFAULT FALSE,
                    user_read BOOLEAN DEFAULT FALSE,

                    -- Metadata
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS search_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_date TEXT NOT NULL,
                    papers_found INTEGER DEFAULT 0,
                    new_papers INTEGER DEFAULT 0,
                    high_priority_count INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS config_suggestions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    suggestion_type TEXT NOT NULL,
                    suggestion_text TEXT NOT NULL,
                    suggestion_data TEXT,
                    rationale TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    reviewed_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_papers_source ON papers(source);
                CREATE INDEX IF NOT EXISTS idx_papers_pub_date ON papers(pub_date);
                CREATE INDEX IF NOT EXISTS idx_papers_relevance ON papers(relevance_score);
                CREATE INDEX IF NOT EXISTS idx_papers_first_seen ON papers(first_seen_date);
                CREATE INDEX IF NOT EXISTS idx_config_suggestions_status ON config_suggestions(status);
            """)

            # Add feedback columns to papers table (migration for existing databases)
            migrations = [
                ("user_feedback", "ALTER TABLE papers ADD COLUMN user_feedback TEXT DEFAULT NULL"),
                ("feedback_date", "ALTER TABLE papers ADD COLUMN feedback_date TEXT DEFAULT NULL"),
                ("is_seed", "ALTER TABLE papers ADD COLUMN is_seed BOOLEAN DEFAULT FALSE"),
                ("seed_source", "ALTER TABLE papers ADD COLUMN seed_source TEXT DEFAULT NULL"),
                ("last_digest_date", "ALTER TABLE papers ADD COLUMN last_digest_date TEXT DEFAULT NULL"),
            ]
            for col_name, sql in migrations:
                try:
                    conn.execute(sql)
                except sqlite3.OperationalError:
                    pass  # Column already exists

            # Add feedback indexes
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_feedback ON papers(user_feedback)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_seed ON papers(is_seed)")
            except sqlite3.OperationalError:
                pass

    def paper_exists(self, paper_id: str) -> bool:
        """Check if a paper already exists in the database."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "SELECT 1 FROM papers WHERE id = ?", (paper_id,)
            )
            return cursor.fetchone() is not None

    def get_existing_ids(self, paper_ids: list[str]) -> set[str]:
        """Get the subset of paper IDs that already exist in the database."""
        if not paper_ids:
            return set()

        with self._get_conn() as conn:
            placeholders = ",".join("?" * len(paper_ids))
            cursor = conn.execute(
                f"SELECT id FROM papers WHERE id IN ({placeholders})",
                paper_ids
            )
            return {row["id"] for row in cursor.fetchall()}

    def doi_exists(self, doi: str) -> bool:
        """Check if a paper with this DOI already exists (cross-source dedup)."""
        if not doi:
            return False
        with self._get_conn() as conn:
            cursor = conn.execute(
                "SELECT 1 FROM papers WHERE doi = ? AND doi IS NOT NULL", (doi,)
            )
            return cursor.fetchone() is not None

    def insert_paper(self, paper: Paper) -> bool:
        """
        Insert a paper into the database.

        Deduplicates by both paper ID and DOI (to catch cross-source
        duplicates like the same paper on bioRxiv and PubMed).

        Args:
            paper: Paper object to insert.

        Returns:
            True if inserted, False if already exists.
        """
        if self.paper_exists(paper.id):
            return False

        # Cross-source dedup: skip if a paper with the same DOI already exists
        if paper.doi and self.doi_exists(paper.doi):
            return False

        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO papers (
                    id, source, title, authors, journal, pub_date,
                    abstract, url, full_text_url, is_open_access, doi,
                    summary, relevance_score, ranking_rationale, matched_projects,
                    first_seen_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                paper.id,
                paper.source,
                paper.title,
                json.dumps(paper.authors),
                paper.journal,
                paper.pub_date,
                paper.abstract,
                paper.url,
                paper.full_text_url,
                paper.is_open_access,
                paper.doi,
                paper.summary,
                paper.relevance_score,
                paper.ranking_rationale,
                json.dumps(paper.matched_projects) if paper.matched_projects else None,
                datetime.now().isoformat(),
            ))
        return True

    def insert_papers(self, papers: list[Paper]) -> tuple[int, int]:
        """
        Insert multiple papers, skipping duplicates.

        Args:
            papers: List of Paper objects.

        Returns:
            Tuple of (total_count, new_count).
        """
        new_count = 0
        for paper in papers:
            if self.insert_paper(paper):
                new_count += 1
        return len(papers), new_count

    def update_paper_ranking(
        self,
        paper_id: str,
        summary: str,
        relevance_score: float,
        ranking_rationale: str,
        matched_projects: list[str],
    ):
        """Update a paper with Claude-generated ranking information."""
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE papers SET
                    summary = ?,
                    relevance_score = ?,
                    ranking_rationale = ?,
                    matched_projects = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                summary,
                relevance_score,
                ranking_rationale,
                json.dumps(matched_projects),
                paper_id,
            ))

    def get_paper(self, paper_id: str) -> Optional[Paper]:
        """Get a paper by ID."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "SELECT * FROM papers WHERE id = ?", (paper_id,)
            )
            row = cursor.fetchone()
            if row:
                return self._row_to_paper(row)
        return None

    def get_unranked_papers(self) -> list[Paper]:
        """Get all papers that haven't been ranked yet."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "SELECT * FROM papers WHERE relevance_score IS NULL"
            )
            return [self._row_to_paper(row) for row in cursor.fetchall()]

    def get_papers_since(
        self,
        since_date: str,
        min_score: Optional[float] = None,
        limit: Optional[int] = None,
    ) -> list[Paper]:
        """
        Get papers first seen since a given date.

        Args:
            since_date: ISO format date string.
            min_score: Minimum relevance score filter.
            limit: Maximum number of papers to return.

        Returns:
            List of papers ordered by relevance score (descending).
        """
        query = "SELECT * FROM papers WHERE first_seen_date >= ?"
        params: list = [since_date]

        if min_score is not None:
            query += " AND relevance_score >= ?"
            params.append(min_score)

        query += " ORDER BY relevance_score DESC NULLS LAST"

        if limit:
            query += " LIMIT ?"
            params.append(limit)

        with self._get_conn() as conn:
            cursor = conn.execute(query, params)
            return [self._row_to_paper(row) for row in cursor.fetchall()]

    def get_recent_papers(
        self,
        days: int = 7,
        min_score: Optional[float] = None,
    ) -> list[Paper]:
        """Get papers from the last N days."""
        from datetime import timedelta
        since = (datetime.now() - timedelta(days=days)).isoformat()
        return self.get_papers_since(since, min_score=min_score)

    def mark_as_read(self, paper_id: str):
        """Mark a paper as read by the user."""
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE papers SET user_read = TRUE WHERE id = ?",
                (paper_id,)
            )

    def mark_added_to_zotero(self, paper_id: str):
        """Mark a paper as added to Zotero."""
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE papers SET added_to_zotero = TRUE WHERE id = ?",
                (paper_id,)
            )

    def mark_papers_digested(self, paper_ids: list[str]):
        """Mark papers as included in a digest so they aren't re-sent."""
        if not paper_ids:
            return
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            for paper_id in paper_ids:
                conn.execute(
                    "UPDATE papers SET last_digest_date = ? WHERE id = ?",
                    (now, paper_id)
                )

    def get_papers_for_digest(
        self,
        days: int = 7,
        min_score: Optional[float] = None,
    ) -> list[Paper]:
        """
        Get papers for a digest, excluding those already sent in a prior digest.

        Args:
            days: Look back window (papers first seen within this many days).
            min_score: Minimum relevance score filter.

        Returns:
            List of papers not yet included in any digest, ordered by score.
        """
        from datetime import timedelta
        since = (datetime.now() - timedelta(days=days)).isoformat()

        query = "SELECT * FROM papers WHERE first_seen_date >= ? AND last_digest_date IS NULL AND (is_seed IS NULL OR is_seed = FALSE)"
        params: list = [since]

        if min_score is not None:
            query += " AND relevance_score >= ?"
            params.append(min_score)

        query += " ORDER BY relevance_score DESC NULLS LAST"

        with self._get_conn() as conn:
            cursor = conn.execute(query, params)
            return [self._row_to_paper(row) for row in cursor.fetchall()]

    def record_search_run(
        self,
        papers_found: int,
        new_papers: int,
        high_priority_count: int = 0,
    ) -> int:
        """
        Record a search run.

        Returns:
            The ID of the new search run record.
        """
        with self._get_conn() as conn:
            cursor = conn.execute("""
                INSERT INTO search_runs (run_date, papers_found, new_papers, high_priority_count)
                VALUES (?, ?, ?, ?)
            """, (
                datetime.now().isoformat(),
                papers_found,
                new_papers,
                high_priority_count,
            ))
            return cursor.lastrowid

    def get_search_runs(self, limit: int = 10) -> list[SearchRun]:
        """Get recent search runs."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "SELECT * FROM search_runs ORDER BY run_date DESC LIMIT ?",
                (limit,)
            )
            return [
                SearchRun(
                    id=row["id"],
                    run_date=row["run_date"],
                    papers_found=row["papers_found"],
                    new_papers=row["new_papers"],
                    high_priority_count=row["high_priority_count"],
                )
                for row in cursor.fetchall()
            ]

    # --- Feedback methods ---

    def set_feedback(self, paper_id: str, feedback: str):
        """
        Set user feedback on a paper.

        Args:
            paper_id: Paper ID.
            feedback: 'star', 'dismiss', or None to clear.
        """
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE papers SET
                    user_feedback = ?,
                    feedback_date = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (feedback, datetime.now().isoformat() if feedback else None, paper_id))

    def get_starred_papers(self, limit: int = 50) -> list[Paper]:
        """Get papers the user has starred, most recent first."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "SELECT * FROM papers WHERE user_feedback = 'star' ORDER BY feedback_date DESC LIMIT ?",
                (limit,)
            )
            return [self._row_to_paper(row) for row in cursor.fetchall()]

    def get_dismissed_papers(self, limit: int = 50) -> list[Paper]:
        """Get papers the user has dismissed, most recent first."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "SELECT * FROM papers WHERE user_feedback = 'dismiss' ORDER BY feedback_date DESC LIMIT ?",
                (limit,)
            )
            return [self._row_to_paper(row) for row in cursor.fetchall()]

    def get_feedback_stats(self) -> dict:
        """Get counts of starred, dismissed, and neutral papers."""
        with self._get_conn() as conn:
            cursor = conn.execute("""
                SELECT
                    COUNT(CASE WHEN user_feedback = 'star' THEN 1 END) as starred,
                    COUNT(CASE WHEN user_feedback = 'dismiss' THEN 1 END) as dismissed,
                    COUNT(CASE WHEN user_feedback IS NULL THEN 1 END) as neutral,
                    COUNT(CASE WHEN is_seed = TRUE THEN 1 END) as seeds
                FROM papers
            """)
            row = cursor.fetchone()
            return {
                "starred": row["starred"],
                "dismissed": row["dismissed"],
                "neutral": row["neutral"],
                "seeds": row["seeds"],
            }

    # --- Seed paper methods ---

    def insert_seed_paper(self, paper: Paper, source: str = "doi_lookup") -> bool:
        """
        Insert a paper as a seed (auto-starred).

        Args:
            paper: Paper object to insert.
            source: How the seed was added ('doi_lookup', 'pmid_lookup', 'zotero_sync').

        Returns:
            True if inserted, False if already exists (updates feedback if exists).
        """
        if self.paper_exists(paper.id):
            # If paper exists, just mark it as seed + starred
            with self._get_conn() as conn:
                conn.execute("""
                    UPDATE papers SET
                        is_seed = TRUE,
                        seed_source = ?,
                        user_feedback = 'star',
                        feedback_date = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (source, datetime.now().isoformat(), paper.id))
            return False

        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO papers (
                    id, source, title, authors, journal, pub_date,
                    abstract, url, full_text_url, is_open_access, doi,
                    summary, relevance_score, ranking_rationale, matched_projects,
                    first_seen_date, is_seed, seed_source, user_feedback, feedback_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                paper.id,
                paper.source,
                paper.title,
                json.dumps(paper.authors),
                paper.journal,
                paper.pub_date,
                paper.abstract,
                paper.url,
                paper.full_text_url,
                paper.is_open_access,
                paper.doi,
                paper.summary,
                paper.relevance_score,
                paper.ranking_rationale,
                json.dumps(paper.matched_projects) if paper.matched_projects else None,
                datetime.now().isoformat(),
                True,
                source,
                "star",
                datetime.now().isoformat(),
            ))
        return True

    def get_seed_papers(self) -> list[Paper]:
        """Get all seed papers."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "SELECT * FROM papers WHERE is_seed = TRUE ORDER BY created_at DESC"
            )
            return [self._row_to_paper(row) for row in cursor.fetchall()]

    # --- Config suggestion methods ---

    def add_config_suggestion(
        self,
        suggestion_type: str,
        suggestion_text: str,
        suggestion_data: Optional[dict] = None,
        rationale: str = "",
    ) -> int:
        """
        Add a config suggestion.

        Args:
            suggestion_type: Type of suggestion (search_query, project_keyword, watched_author, new_project).
            suggestion_text: Human-readable suggestion text.
            suggestion_data: JSON-serializable data for auto-applying the suggestion.
            rationale: Why this suggestion was made.

        Returns:
            ID of the new suggestion.
        """
        with self._get_conn() as conn:
            cursor = conn.execute("""
                INSERT INTO config_suggestions (suggestion_type, suggestion_text, suggestion_data, rationale)
                VALUES (?, ?, ?, ?)
            """, (
                suggestion_type,
                suggestion_text,
                json.dumps(suggestion_data) if suggestion_data else None,
                rationale,
            ))
            return cursor.lastrowid

    def get_pending_suggestions(self) -> list[ConfigSuggestion]:
        """Get all pending config suggestions."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "SELECT * FROM config_suggestions WHERE status = 'pending' ORDER BY created_at DESC"
            )
            return [self._row_to_suggestion(row) for row in cursor.fetchall()]

    def get_all_suggestions(self, limit: int = 50) -> list[ConfigSuggestion]:
        """Get all config suggestions."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "SELECT * FROM config_suggestions ORDER BY created_at DESC LIMIT ?",
                (limit,)
            )
            return [self._row_to_suggestion(row) for row in cursor.fetchall()]

    def resolve_suggestion(self, suggestion_id: int, status: str):
        """
        Resolve a config suggestion.

        Args:
            suggestion_id: ID of the suggestion.
            status: New status ('accepted' or 'dismissed').
        """
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE config_suggestions SET
                    status = ?,
                    reviewed_at = ?
                WHERE id = ?
            """, (status, datetime.now().isoformat(), suggestion_id))

    def _row_to_suggestion(self, row: sqlite3.Row) -> ConfigSuggestion:
        """Convert a database row to a ConfigSuggestion object."""
        suggestion_data = row["suggestion_data"]
        if suggestion_data:
            suggestion_data = json.loads(suggestion_data)
        return ConfigSuggestion(
            id=row["id"],
            suggestion_type=row["suggestion_type"],
            suggestion_text=row["suggestion_text"],
            suggestion_data=suggestion_data,
            rationale=row["rationale"] or "",
            status=row["status"],
            created_at=row["created_at"],
            reviewed_at=row["reviewed_at"],
        )

    def get_stats(self) -> dict:
        """Get database statistics."""
        with self._get_conn() as conn:
            stats = {}

            # Total papers
            cursor = conn.execute("SELECT COUNT(*) as count FROM papers")
            stats["total_papers"] = cursor.fetchone()["count"]

            # By source
            cursor = conn.execute(
                "SELECT source, COUNT(*) as count FROM papers GROUP BY source"
            )
            stats["by_source"] = {row["source"]: row["count"] for row in cursor.fetchall()}

            # Ranked vs unranked
            cursor = conn.execute(
                "SELECT COUNT(*) as count FROM papers WHERE relevance_score IS NOT NULL"
            )
            stats["ranked_papers"] = cursor.fetchone()["count"]

            # High priority (score >= 0.7)
            cursor = conn.execute(
                "SELECT COUNT(*) as count FROM papers WHERE relevance_score >= 0.7"
            )
            stats["high_priority"] = cursor.fetchone()["count"]

            # Search runs
            cursor = conn.execute("SELECT COUNT(*) as count FROM search_runs")
            stats["total_runs"] = cursor.fetchone()["count"]

            # Feedback stats
            cursor = conn.execute("""
                SELECT
                    COUNT(CASE WHEN user_feedback = 'star' THEN 1 END) as starred,
                    COUNT(CASE WHEN user_feedback = 'dismiss' THEN 1 END) as dismissed,
                    COUNT(CASE WHEN is_seed = TRUE THEN 1 END) as seeds
                FROM papers
            """)
            row = cursor.fetchone()
            stats["starred"] = row["starred"]
            stats["dismissed"] = row["dismissed"]
            stats["seeds"] = row["seeds"]

            return stats

    def _row_to_paper(self, row: sqlite3.Row) -> Paper:
        """Convert a database row to a Paper object."""
        authors = row["authors"]
        if authors:
            authors = json.loads(authors)
        else:
            authors = []

        matched_projects = row["matched_projects"]
        if matched_projects:
            matched_projects = json.loads(matched_projects)
        else:
            matched_projects = []

        paper = Paper(
            id=row["id"],
            source=row["source"],
            title=row["title"] or "",
            authors=authors,
            journal=row["journal"] or "",
            pub_date=row["pub_date"] or "",
            abstract=row["abstract"] or "",
            url=row["url"] or "",
            full_text_url=row["full_text_url"],
            is_open_access=bool(row["is_open_access"]),
            doi=row["doi"],
            summary=row["summary"],
            relevance_score=row["relevance_score"],
            ranking_rationale=row["ranking_rationale"],
            matched_projects=matched_projects,
        )

        # Attach feedback metadata (not part of Paper dataclass, but useful for display)
        paper._user_feedback = row["user_feedback"] if "user_feedback" in row.keys() else None
        paper._feedback_date = row["feedback_date"] if "feedback_date" in row.keys() else None
        paper._is_seed = bool(row["is_seed"]) if "is_seed" in row.keys() else False
        paper._seed_source = row["seed_source"] if "seed_source" in row.keys() else None

        return paper


if __name__ == "__main__":
    # Quick test
    db = PaperDatabase()
    print(f"Database at: {db.db_path}")
    stats = db.get_stats()
    print(f"Stats: {stats}")
