"""
Paper lookup by DOI or PMID for adding seed papers.

Supports:
- PubMed ID (PMID) lookup via existing PubMedClient
- DOI lookup via CrossRef API with PubMed fallback
"""

import re
from typing import Optional

import requests

from .sources.pubmed import Paper, PubMedClient


# DOI regex: starts with "10." followed by registrant code / suffix
DOI_PATTERN = re.compile(r'^10\.\d{4,9}/[^\s]+$')

# PMID: purely numeric
PMID_PATTERN = re.compile(r'^\d+$')


def is_doi(identifier: str) -> bool:
    """Check if identifier looks like a DOI."""
    # Strip common DOI URL prefixes
    cleaned = identifier.strip()
    for prefix in ('https://doi.org/', 'http://doi.org/', 'doi:'):
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix):]
    return bool(DOI_PATTERN.match(cleaned))


def clean_doi(identifier: str) -> str:
    """Extract the bare DOI from a DOI URL or prefixed string."""
    cleaned = identifier.strip()
    for prefix in ('https://doi.org/', 'http://doi.org/', 'doi:'):
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix):]
    return cleaned


def is_pmid(identifier: str) -> bool:
    """Check if identifier looks like a PubMed ID."""
    return bool(PMID_PATTERN.match(identifier.strip()))


def fetch_paper_by_pmid(pmid: str) -> Optional[Paper]:
    """
    Fetch a paper by PubMed ID.

    Args:
        pmid: PubMed ID (numeric string).

    Returns:
        Paper object or None if not found.
    """
    client = PubMedClient()
    papers = client.fetch_papers([pmid.strip()])
    return papers[0] if papers else None


def fetch_paper_by_doi(doi: str) -> Optional[Paper]:
    """
    Fetch a paper by DOI, trying CrossRef first then PubMed.

    Args:
        doi: DOI string (e.g., "10.1234/example").

    Returns:
        Paper object or None if not found.
    """
    doi = clean_doi(doi)

    # Try CrossRef first
    paper = _fetch_from_crossref(doi)
    if paper:
        return paper

    # Fallback: search PubMed by DOI
    return _fetch_from_pubmed_by_doi(doi)


def _fetch_from_crossref(doi: str) -> Optional[Paper]:
    """Fetch paper metadata from CrossRef API."""
    try:
        response = requests.get(
            f"https://api.crossref.org/works/{doi}",
            headers={"User-Agent": "LitMonitor/1.0 (mailto:litmonitor@example.com)"},
            timeout=15,
        )

        if response.status_code != 200:
            return None

        data = response.json().get("message", {})

        # Extract title
        title_list = data.get("title", [])
        title = title_list[0] if title_list else ""
        if not title:
            return None

        # Extract authors
        authors = []
        for author in data.get("author", []):
            family = author.get("family", "")
            given = author.get("given", "")
            if family:
                # Convert to "LastName Initials" format
                initials = "".join(w[0] for w in given.split() if w) if given else ""
                authors.append(f"{family} {initials}".strip())

        # Extract journal
        container = data.get("container-title", [])
        journal = container[0] if container else ""

        # Extract date
        pub_date = ""
        date_parts = data.get("published-print", {}).get("date-parts", [[]])
        if not date_parts[0]:
            date_parts = data.get("published-online", {}).get("date-parts", [[]])
        if date_parts and date_parts[0]:
            parts = date_parts[0]
            if len(parts) >= 3:
                pub_date = f"{parts[0]}-{parts[1]:02d}-{parts[2]:02d}"
            elif len(parts) >= 2:
                pub_date = f"{parts[0]}-{parts[1]:02d}-01"
            elif len(parts) >= 1:
                pub_date = f"{parts[0]}-01-01"

        # Extract abstract (CrossRef sometimes has it)
        abstract = data.get("abstract", "")
        # CrossRef abstracts sometimes have JATS XML tags
        if abstract:
            abstract = re.sub(r'<[^>]+>', '', abstract).strip()

        # Build URL
        url = f"https://doi.org/{doi}"

        # Check for open access
        is_oa = False
        license_list = data.get("license", [])
        for lic in license_list:
            if "open" in lic.get("URL", "").lower() or "creativecommons" in lic.get("URL", "").lower():
                is_oa = True
                break

        return Paper(
            id=f"doi:{doi}",
            source="crossref",
            title=title,
            authors=authors,
            journal=journal,
            pub_date=pub_date,
            abstract=abstract,
            url=url,
            doi=doi,
            is_open_access=is_oa,
        )

    except Exception:
        return None


def _fetch_from_pubmed_by_doi(doi: str) -> Optional[Paper]:
    """Search PubMed for a paper by DOI."""
    try:
        client = PubMedClient()
        pmids = client.search(f"{doi}[DOI]", max_results=1, days_back=36500)
        if pmids:
            papers = client.fetch_papers(pmids)
            return papers[0] if papers else None
    except Exception:
        pass
    return None


def lookup_paper(identifier: str) -> tuple[Optional[Paper], str]:
    """
    Look up a paper by DOI or PMID, auto-detecting the type.

    Args:
        identifier: DOI or PMID string.

    Returns:
        Tuple of (Paper or None, source_type string).
        source_type is 'pmid_lookup' or 'doi_lookup'.
    """
    identifier = identifier.strip()

    if is_pmid(identifier):
        paper = fetch_paper_by_pmid(identifier)
        return paper, "pmid_lookup"

    if is_doi(identifier):
        paper = fetch_paper_by_doi(identifier)
        return paper, "doi_lookup"

    # Try treating it as a DOI anyway (might have unusual format)
    paper = fetch_paper_by_doi(identifier)
    if paper:
        return paper, "doi_lookup"

    return None, "unknown"
