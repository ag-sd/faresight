"""Shared matching semantics for classification rules.

Rule descriptions are regular expressions, matched case-insensitively anywhere
in the transaction description (`re.search`). Plain text therefore behaves as a
case-insensitive "contains". Both match sites — import-time pre-classification
and the retroactive apply endpoint — must go through this module so semantics
never drift.
"""
import re
from typing import Callable, Optional

from app.models import Rule


def compile_rule(pattern: str) -> "re.Pattern":
    return re.compile(pattern, re.IGNORECASE)


def build_matcher(rules: list[Rule]) -> Callable[[str], Optional[str]]:
    """Return a description -> category function (None when no rule matches).

    Rules are tried in the caller-provided order (order by created_at, id for
    deterministic oldest-wins); the first match wins. Patterns compile once
    here, not per row. Uncompilable patterns (legacy rows predating create-time
    validation) are skipped so a bad rule can never break an import.
    """
    compiled: list[tuple["re.Pattern", str]] = []
    for rule in rules:
        try:
            compiled.append((compile_rule(rule.description), rule.category))
        except re.error:
            continue

    def match(description: str) -> Optional[str]:
        for pattern, category in compiled:
            if pattern.search(description):
                return category
        return None

    return match
