"""Query sets, and what a benchmark statement is allowed to be.

A set is a named list of named statements. Storage lives in
`bench/setstore.py`; this module is what a set *is* and what may go in one.

⛔ Read-only statements only. A benchmark runs a statement N times unattended
against a cluster nobody is watching, so a pasted `DELETE` would run N times.
The allowlist is enforced twice - here on write, and again in the runner
before each execution - because a row can reach the table through psql.

⛔ No query set ships by default. A bundled set would have to name a catalog,
and which catalogs exist is a fact about the deployment; one that fails on
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

    __slots__ = ("name", "sql", "title", "position")

    def __init__(self, name: str, sql: str, title: str = "",
                 position: int = 0) -> None:
        self.name = name
        self.sql = sql
        self.title = title or name
        self.position = int(position)

    def as_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "sql": self.sql, "title": self.title,
                "position": self.position}


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


def refuse_statement(sql: str) -> Optional[str]:
    """None if this may be benchmarked, else why not, in a sentence.

    Called on every write, and again by the runner before each execution.
    """
    return _statement_is_read_only(sql)


def refuse_name(name: str, what: str = "name") -> Optional[str]:
    """None if usable as a set key or query name, else why not."""
    if not NAME_PATTERN.match(name or ""):
        return ("{!r} is not a usable {}: lowercase letters, digits, '-' and "
                "'_', up to 64 characters, starting with a letter or digit"
                .format(name, what))
    return None


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
            # `position=index` so the declared order survives a round trip
            # through a repository, which sorts by position then name.
            queries.append(BenchmarkQuery(name=name, sql=sql.strip().rstrip(";"),
                                          title=str(entry.get("title") or ""),
                                          position=index))

        sets[key] = QuerySet(key=key, title=str(declared.get("title") or ""),
                             description=str(declared.get("description") or ""),
                             queries=queries)
    return sets
