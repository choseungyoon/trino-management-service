"""Declared query sets (FR-BM-01).

A set is written into config by an administrator, exactly like `fleet.jobs`,
and the request only ever names a key. The alternative - a text box that
submits SQL - is a different product: it is the web SQL editor CLAUDE.md lists
as a non-goal, wearing a benchmark's clothes.

**Read-only statements only.** Not because a write benchmark is illegitimate,
but because a benchmark runs a statement N times unattended. A `DELETE` that
reached a query set through a copy-paste would run N times against a cluster
whose defining property, at that moment, is that nobody is watching it. The
allowlist is checked at config load, so it is a startup error rather than a
discovery made mid-run.

⛔ **No query set ships with TMS.** A default set would have to name a catalog,
and which catalogs exist is a fact about the deployment - `tpch` is present on
a stock Trino and absent on plenty of real ones. A shipped set that fails on
first use teaches the operator that the feature is broken.

Python 3.9 compatible.
"""

import re
from typing import Any, Dict, List, Optional

#: What may start a benchmark statement. Everything else is refused at load.
#: `WITH` is here because most real analytic queries begin with it; it can only
#: lead to a SELECT, since Trino's `WITH` attaches to a query.
READ_ONLY_STARTS = ("SELECT", "WITH", "SHOW", "EXPLAIN", "DESCRIBE", "VALUES", "TABLE")

NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

MAX_QUERIES = 50
MAX_REPETITIONS = 20
MAX_STATEMENT_CHARS = 20000


class QuerySetError(Exception):
    """A declared set cannot be used as written."""


class BenchmarkQuery:
    """One named statement."""

    __slots__ = ("name", "sql", "title")

    def __init__(self, name: str, sql: str, title: str = "") -> None:
        self.name = name
        self.sql = sql
        self.title = title or name

    def as_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "sql": self.sql, "title": self.title}


class QuerySet:
    __slots__ = ("key", "title", "description", "queries")

    def __init__(self, key: str, title: str, description: str,
                 queries: List[BenchmarkQuery]) -> None:
        self.key = key
        self.title = title or key
        self.description = description
        self.queries = queries

    def as_dict(self) -> Dict[str, Any]:
        return {"key": self.key, "title": self.title,
                "description": self.description,
                "queries": [q.as_dict() for q in self.queries]}


def _statement_is_read_only(sql: str) -> Optional[str]:
    """None if it is allowed, else the reason it is not."""
    stripped = _without_comments(sql).strip()
    if not stripped:
        return "the statement is empty"
    if len(sql) > MAX_STATEMENT_CHARS:
        return "the statement is longer than {} characters".format(MAX_STATEMENT_CHARS)
    # Trino's client protocol takes one statement per request, so a second one
    # would not run - it would make the first fail with a parse error, N times.
    if ";" in stripped.rstrip(";"):
        return "more than one statement; a benchmark query must be a single statement"
    first = re.split(r"[\s(]+", stripped.lstrip("("), maxsplit=1)[0].upper()
    if first not in READ_ONLY_STARTS:
        return ("starts with {!r}; benchmark statements must be read-only "
                "({})".format(first, ", ".join(READ_ONLY_STARTS)))
    return None


def _without_comments(sql: str) -> str:
    """Strip `--` and `/* */` so a comment cannot hide the real first keyword.

    Without this, `-- harmless\\nDELETE FROM t` passes the allowlist.
    """
    no_block = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"--[^\n]*", " ", no_block)


def build_query_sets(raw: Dict[str, Any]) -> Dict[str, QuerySet]:
    """Config -> query sets, or `QuerySetError`.

    Called at config load so a malformed set stops startup, rather than being
    found by the person who took a cluster out of rotation to run it.
    """
    sets: Dict[str, QuerySet] = {}
    for key, declared in sorted((raw or {}).items()):
        key = str(key)
        if not NAME_PATTERN.match(key):
            raise QuerySetError(
                "{!r} is not a usable set name (lowercase letters, digits, "
                "'-' and '_', up to 64 characters)".format(key))
        if not isinstance(declared, dict):
            raise QuerySetError("{}: expected a mapping".format(key))

        raw_queries = declared.get("queries") or []
        if not raw_queries:
            raise QuerySetError("{}: declares no queries".format(key))
        if len(raw_queries) > MAX_QUERIES:
            raise QuerySetError(
                "{}: {} queries, more than the {} allowed".format(
                    key, len(raw_queries), MAX_QUERIES))

        queries: List[BenchmarkQuery] = []
        seen = set()
        for index, entry in enumerate(raw_queries):
            if not isinstance(entry, dict):
                raise QuerySetError(
                    "{}: query {} is not a mapping".format(key, index + 1))
            name = str(entry.get("name") or "")
            if not NAME_PATTERN.match(name):
                raise QuerySetError(
                    "{}: query {} has no usable name".format(key, index + 1))
            if name in seen:
                # Results are keyed by name; a duplicate would silently merge
                # two different queries into one column of the comparison.
                raise QuerySetError("{}: two queries are named {!r}".format(key, name))
            seen.add(name)

            sql = str(entry.get("sql") or "")
            refusal = _statement_is_read_only(sql)
            if refusal is not None:
                raise QuerySetError("{}.{}: {}".format(key, name, refusal))
            queries.append(BenchmarkQuery(name=name, sql=sql.strip().rstrip(";"),
                                          title=str(entry.get("title") or "")))

        sets[key] = QuerySet(key=key, title=str(declared.get("title") or ""),
                             description=str(declared.get("description") or ""),
                             queries=queries)
    return sets
