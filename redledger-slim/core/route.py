"""
Baolu Route calculation and canonical route merging.
"""
from typing import Any

def clean_route(route: str | None) -> str:
    if not route:
        return ""
    return "".join(ch for ch in str(route) if ch in "1234")

def merge_canonical_route(canonical: str, reported: str) -> str:
    canonical = clean_route(canonical)
    reported = clean_route(reported)
    if not canonical:
        return reported
    if not reported:
        return canonical
    if reported.startswith(canonical):
        return reported
    if canonical.startswith(reported) or canonical.endswith(reported):
        return canonical
    return reported if len(reported) > len(canonical) else canonical

def append_result_to_route(current_route: str, result: str) -> str:
    current = clean_route(current_route)
    res = clean_route(result)
    if res in {"1", "2", "3", "4"}:
        return f"{current}{res}"
    return current
