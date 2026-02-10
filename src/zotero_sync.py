"""
Zotero library sync for importing existing papers as seeds.

Syncs items from a Zotero library, converting them to Paper objects
and inserting them as seed papers (auto-starred).
"""

import json
from pathlib import Path
from typing import Optional

import requests

from .database import PaperDatabase
from .sources.pubmed import Paper


ZOTERO_API_BASE = "https://api.zotero.org"
VERSION_FILE = Path(__file__).parent.parent / "data" / ".zotero_sync_version"


def sync_zotero_library(
    api_key: str,
    user_id: str,
    db: PaperDatabase,
    tag_filter: Optional[str] = None,
) -> int:
    """
    Sync items from a Zotero library as seed papers.

    Args:
        api_key: Zotero API key.
        user_id: Zotero user ID.
        db: Database instance.
        tag_filter: Optional tag to filter items (e.g., 'lit-monitor').

    Returns:
        Number of new papers synced.
    """
    headers = {
        "Zotero-API-Key": api_key,
        "Zotero-API-Version": "3",
    }

    # Load last sync version for incremental sync
    last_version = _load_sync_version()

    # Build request params
    params = {
        "itemType": "journalArticle || preprint",
        "format": "json",
        "limit": 100,
        "sort": "dateModified",
        "direction": "desc",
    }

    if tag_filter:
        params["tag"] = tag_filter

    if last_version:
        params["since"] = str(last_version)

    new_count = 0
    start = 0
    new_version = last_version

    while True:
        params["start"] = start

        response = requests.get(
            f"{ZOTERO_API_BASE}/users/{user_id}/items",
            headers=headers,
            params=params,
            timeout=30,
        )

        if response.status_code == 304:
            # No changes since last sync
            break

        if response.status_code != 200:
            print(f"Zotero API error: {response.status_code} - {response.text[:200]}")
            break

        # Track the library version from response headers
        lib_version = response.headers.get("Last-Modified-Version")
        if lib_version:
            lib_version = int(lib_version)
            if new_version is None or lib_version > new_version:
                new_version = lib_version

        items = response.json()
        if not items:
            break

        for item in items:
            paper = _zotero_item_to_paper(item)
            if paper:
                is_new = db.insert_seed_paper(paper, source="zotero_sync")
                if is_new:
                    new_count += 1
                    print(f"  Synced: {paper.title[:60]}...")

        # Check for more pages
        total = int(response.headers.get("Total-Results", 0))
        start += len(items)
        if start >= total:
            break

    # Save sync version for next incremental sync
    if new_version and new_version != last_version:
        _save_sync_version(new_version)

    return new_count


def _zotero_item_to_paper(item: dict) -> Optional[Paper]:
    """Convert a Zotero API item to a Paper object."""
    data = item.get("data", {})

    title = data.get("title", "")
    if not title:
        return None

    # Extract authors
    authors = []
    for creator in data.get("creators", []):
        if creator.get("creatorType") == "author":
            last = creator.get("lastName", "")
            first = creator.get("firstName", "")
            if last:
                initials = "".join(w[0] for w in first.split() if w) if first else ""
                authors.append(f"{last} {initials}".strip())

    # Extract DOI
    doi = data.get("DOI", "")

    # Build paper ID (prefer DOI, fall back to Zotero key)
    paper_id = f"doi:{doi}" if doi else f"zotero:{item.get('key', '')}"

    # Journal
    journal = data.get("publicationTitle", "") or data.get("journalAbbreviation", "")

    # Date
    pub_date = data.get("date", "")

    # Abstract
    abstract = data.get("abstractNote", "")

    # URL
    url = data.get("url", "")
    if not url and doi:
        url = f"https://doi.org/{doi}"

    return Paper(
        id=paper_id,
        source="zotero",
        title=title,
        authors=authors,
        journal=journal,
        pub_date=pub_date,
        abstract=abstract,
        url=url,
        doi=doi or None,
    )


def _load_sync_version() -> Optional[int]:
    """Load the last sync version from disk."""
    try:
        if VERSION_FILE.exists():
            return int(VERSION_FILE.read_text().strip())
    except (ValueError, OSError):
        pass
    return None


def _save_sync_version(version: int):
    """Save the sync version to disk."""
    try:
        VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        VERSION_FILE.write_text(str(version))
    except OSError:
        pass
