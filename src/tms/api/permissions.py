"""Role to capability mapping (FR-PT-04, ARCHITECTURE.md 6-1).

Capabilities rather than raw role checks scattered through handlers: a handler
asking "is this user an admin?" drifts the moment a fourth role appears, while
"may this user change a threshold?" keeps meaning the same thing.

`GET /api/v1/me` returns the caller's capabilities so the UI can hide what they
cannot do. Hiding is a courtesy - the server checks regardless. A screen that
renders a button and then answers 403 is a worse design than not rendering it.

Python 3.9 compatible.
"""

from typing import Dict, FrozenSet, Iterable, List, Optional

ROLE_VIEWER = "viewer"
ROLE_OPERATOR = "operator"
ROLE_ADMIN = "admin"

ALL_ROLES = (ROLE_VIEWER, ROLE_OPERATOR, ROLE_ADMIN)

# Capabilities
VIEW_PORTAL = "view_portal"
VIEW_QUERIES = "view_queries"
KILL_QUERY = "kill_query"
VIEW_HEALTH = "view_health"
MANAGE_HEALTH = "manage_health"
VIEW_AUDIT = "view_audit"
EXPORT_AUDIT = "export_audit"

_ROLE_CAPABILITIES: Dict[str, FrozenSet[str]] = {
    ROLE_VIEWER: frozenset([VIEW_PORTAL, VIEW_QUERIES, VIEW_HEALTH]),
    ROLE_OPERATOR: frozenset(
        [VIEW_PORTAL, VIEW_QUERIES, VIEW_HEALTH, KILL_QUERY, VIEW_AUDIT]
    ),
    ROLE_ADMIN: frozenset(
        [
            VIEW_PORTAL,
            VIEW_QUERIES,
            VIEW_HEALTH,
            KILL_QUERY,
            VIEW_AUDIT,
            MANAGE_HEALTH,
            EXPORT_AUDIT,
        ]
    ),
}


class Principal:
    """The authenticated caller.

    Identity resolution (LDAP/AD) is V9's job; this is what it produces.
    """

    __slots__ = ("username", "roles", "ip")

    def __init__(
        self, username: str, roles: Iterable[str], ip: Optional[str] = None
    ) -> None:
        self.username = username
        self.roles = [r for r in roles if r in _ROLE_CAPABILITIES]
        self.ip = ip

    @property
    def capabilities(self) -> List[str]:
        granted: set = set()
        for role in self.roles:
            granted |= _ROLE_CAPABILITIES[role]
        return sorted(granted)

    def can(self, capability: str) -> bool:
        return capability in self.capabilities

    def __repr__(self) -> str:
        return "Principal({}, roles={})".format(self.username, self.roles)


def capabilities_for(roles: Iterable[str]) -> List[str]:
    return Principal("_", roles).capabilities
