"""Catalog drafts, and the two rules that decide whether one may be deployed.

⛔ Rule one: no credential in TMS. A catalog holds `connection-password`, and a
draft stored here would put production credentials in TMS's database, its API,
its audit log and its screens. Trino resolves `${ENV:VAR}` from the node's own
process environment (TRINO_VERIFIED T1-9-2), so a credential-shaped key must
carry a reference rather than a value. Refused when the draft is written and
again immediately before it is deployed - the second check is the one that
matters, because a row can be edited between the two.

⛔ Rule two: a draft goes to a development cluster first. Not a nicety. A
catalog file Trino cannot load stops the *whole server* from starting - an
unknown connector, an unknown property, a missing environment variable, all of
them (T1-9-1) - and TMS has no way to check any of that in advance (T1-9-3).
The connector list exists only inside the exception of a server that already
failed. So the validator is the development cluster: deploy, restart, watch
health. That is D-018, and it is the only method available rather than the
cautious one.

Python 3.9 compatible.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

#: Trino reads `etc/catalog/<name>.properties`, so the name is a filename.
NAME = re.compile(r"^[a-z][a-z0-9_]*$")

#: A key whose value must never be a literal.
SECRET_KEY = re.compile(
    r"(password|secret|credential|access[-_.]?key|private[-_.]?key"
    r"|\.key$|keystore|truststore)",
    re.IGNORECASE)

#: What a credential-shaped key is allowed to hold instead. Trino's own syntax.
ENV_REFERENCE = re.compile(r"^\$\{ENV:[A-Za-z_][A-Za-z0-9_]*\}$")

#: `connector.name` lives in its own column; it is not a free property.
RESERVED = ("connector.name",)

MAX_PROPERTIES = 100
MAX_VALUE_CHARS = 2000


class CatalogError(ValueError):
    """A draft that cannot be written, with a sentence saying why."""


def validate(name: str, connector: str,
             properties: Dict[str, str]) -> Dict[str, Any]:
    """Refuse a draft that cannot be stored. Raises CatalogError."""
    name = (name or "").strip()
    if not NAME.match(name):
        raise CatalogError(
            "A catalog name must start with a letter and hold only lowercase "
            "letters, digits and underscores - Trino reads it as the filename "
            "etc/catalog/<name>.properties.")

    connector = (connector or "").strip()
    if not connector:
        raise CatalogError("Which connector? `connector.name` cannot be empty.")
    # ⛔ Not checked against a list. TMS has no way to know which connectors
    # this cluster's build has (T1-9-3) and a hand-written list would be a
    # second opinion about a build it has never seen. A wrong name is caught
    # by the development cluster refusing to start.
    if not re.match(r"^[a-z][a-z0-9_]*$", connector):
        raise CatalogError(
            "A connector name holds lowercase letters, digits and underscores. "
            "Trino writes them with underscores - `delta_lake`, not "
            "`delta-lake`.")

    cleaned: Dict[str, str] = {}
    for key, value in (properties or {}).items():
        key = str(key).strip()
        value = "" if value is None else str(value)
        if not key:
            continue
        if key in RESERVED:
            raise CatalogError(
                "`connector.name` is set by the Connector field, not as a "
                "property.")
        if len(value) > MAX_VALUE_CHARS:
            raise CatalogError("The value of {!r} is too long.".format(key))
        if SECRET_KEY.search(key) and not ENV_REFERENCE.match(value.strip()):
            raise CatalogError(
                "{} looks like a credential, so it must be an environment "
                "reference rather than a value: ${{ENV:SOME_VARIABLE}}. TMS "
                "never stores the secret itself - Trino reads the variable "
                "from the node's own environment, so the variable has to "
                "exist on every node this is deployed to.".format(key))
        cleaned[key] = value

    if len(cleaned) > MAX_PROPERTIES:
        raise CatalogError(
            "That is more than {} properties.".format(MAX_PROPERTIES))

    return {"name": name, "connector": connector, "properties": cleaned}


def environment_references(properties: Dict[str, str]) -> List[str]:
    """The variables this catalog needs to exist on every target node.

    ⛔ A reference whose variable is absent stops the server booting, exactly
    like a bad connector name (T1-9-2). Listing them is how the screen can say
    what has to be in place before a deploy, rather than finding out during
    one.
    """
    found = set()
    for value in (properties or {}).values():
        for match in re.finditer(r"\$\{ENV:([A-Za-z_][A-Za-z0-9_]*)\}",
                                 str(value or "")):
            found.add(match.group(1))
    return sorted(found)


def render(connector: str, properties: Dict[str, str]) -> str:
    """The file as it will land on the node.

    Shown before every deploy. `connector.name` first because that is how
    every catalog file anyone has read is laid out.
    """
    lines = ["connector.name={}".format(connector)]
    for key in sorted(properties or {}):
        lines.append("{}={}".format(key, properties[key]))
    return "\n".join(lines) + "\n"


def refuse_deploy(draft: Dict[str, Any], cluster: str,
                  development_clusters: List[str]) -> Optional[str]:
    """Why this draft may not go to this cluster, or None.

    ⛔ The gate, in one place. The screen greys a button with it and the
    service raises with it - two copies of this rule would disagree the day
    one of them was edited.
    """
    if cluster in (development_clusters or []):
        return None
    if not development_clusters:
        return ("No development cluster is configured, so there is nowhere to "
                "prove this first. Set cluster_ops.config_scan."
                "development_clusters before deploying a catalog to a cluster "
                "that serves queries.")
    verified = draft.get("verified_on")
    if not verified:
        return ("This catalog has not been proved on a development cluster "
                "yet. A catalog Trino cannot load stops every node it reaches "
                "from starting, and TMS cannot check that in advance - so "
                "deploy it to {} first and let it restart."
                .format(" or ".join(development_clusters)))
    if verified not in (development_clusters or []):
        return ("This catalog was last proved on {}, which is not a "
                "development cluster.".format(verified))
    return None


def fingerprint(connector: str, properties: Dict[str, str]) -> str:
    """What "the same catalog" means, for deciding whether a proof still holds.

    ⛔ Editing a draft after it was proved makes it a different draft. Without
    this, somebody proves a working catalog on the development cluster, changes
    a property, and ships the change straight to production on the strength of
    a test that never saw it.
    """
    import hashlib

    return hashlib.sha256(render(connector, properties).encode()).hexdigest()[:16]


def deployable(draft: Dict[str, Any], clusters: List[str],
               development_clusters: List[str]) -> List[Dict[str, Any]]:
    """Per cluster: may this draft go there, and if not, why not."""
    return [{"cluster": cluster,
             "development": cluster in (development_clusters or []),
             "refusal": refuse_deploy(draft, cluster, development_clusters)}
            for cluster in clusters]
