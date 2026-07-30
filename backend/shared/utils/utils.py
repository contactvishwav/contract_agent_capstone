from neo4j.time import Date, DateTime
import re
from datetime import datetime
from typing import Optional


def convert_neo4j_date(value):
    if isinstance(value, dict):
        return {k: convert_neo4j_date(v) for k, v in value.items()}
    elif isinstance(value, (list, tuple)):
        return [convert_neo4j_date(item) for item in value]
    elif isinstance(value, (Date, DateTime)):
        return f"{value.year}-{value.month}-{value.day}"
    return value


def serialize_neo4j_datetime(value):
    """
    Convert a neo4j.time.DateTime/Date/Time value (e.g. from a Cypher
    datetime()/date() property) to a proper ISO 8601 string via
    .iso_format(), before it reaches an API response - without this,
    FastAPI's default JSON encoder doesn't know how to serialize these
    types and falls back to dumping their __dict__ internals (nonsensical
    fields like "_Date__day": -2) instead of a readable value or a clean
    error.

    Unlike convert_neo4j_date (date-only, YYYY-M-D, no zero-padding -
    unsuitable for full datetimes, which need the time-of-day preserved),
    this always uses .iso_format() and is safe to call unconditionally on
    any query-result field: values that aren't a Neo4j temporal type
    (already a plain string, None, etc.) are returned unchanged.
    """
    if hasattr(value, "iso_format"):
        return value.iso_format()
    return value


def parse_date_to_iso(date_str: str) -> Optional[str]:
    """
    Parse various date formats into ISO 8601 (YYYY-MM-DD) for Neo4j compatibility.
    Handles: 
    - October 16, 2023
    - 16 October 2023
    - 2023-10-16
    - 10/16/2023
    """
    if not date_str or not isinstance(date_str, str):
        return None
    
    date_str = date_str.strip()
    
    # Try ISO format first
    try:
        if re.match(r'^\d{4}-\d{2}-\d{2}', date_str):
            return date_str[:10]
    except:
        pass

    # Month names mapping
    months = {
        'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
        'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12,
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
        'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12
    }

    # Format: October 16, 2023 or Oct 16, 2023
    match = re.search(r'([a-zA-Z]+)\s+(\d{1,2})[,\s]+\s*(\d{4})', date_str)
    if match:
        month_name, day, year = match.groups()
        month_idx = months.get(month_name.lower())
        if month_idx:
            return f"{year}-{int(month_idx):02d}-{int(day):02d}"

    # Format: 16 October 2023 or 16 Oct 2023
    match = re.search(r'(\d{1,2})\s+([a-zA-Z]+)\s+(\d{4})', date_str)
    if match:
        day, month_name, year = match.groups()
        month_idx = months.get(month_name.lower())
        if month_idx:
            return f"{year}-{int(month_idx):02d}-{int(day):02d}"

    # Format: 10/16/2023
    match = re.search(r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})', date_str)
    if match:
        m, d, y = match.groups()
        # Assume MM/DD/YYYY for 10/26/2023
        if int(m) <= 12:
            return f"{y}-{int(m):02d}-{int(d):02d}"
        # Fallback to DD/MM/YYYY if first part is > 12
        return f"{y}-{int(d):02d}-{int(m):02d}"

    # Final fallback: generic datetime parse if possible
    try:
        # This will handle many standard formats
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        return dt.strftime('%Y-%m-%d')
    except:
        pass

    return None
