"""What a route module is handed at registration.

A plain object rather than globals: `create_app` builds a fresh set per app,
and tests build several apps in one process.

Python 3.9 compatible.
"""

from typing import Any, Optional


class Deps:
    __slots__ = ("config", "service", "current_principal", "restarts", "fleet",
                 "board", "benchmark", "resource_groups", "config_scan",
                 "catalogs")

    def __init__(self, config, service, current_principal, restarts=None,
                 fleet=None, board=None, benchmark=None, config_scan=None,
                 catalogs=None, resource_groups=None) -> None:
        self.config = config
        self.service = service
        self.current_principal = current_principal
        self.restarts = restarts
        self.fleet = fleet
        self.board = board
        self.benchmark = benchmark
        self.config_scan = config_scan
        self.catalogs = catalogs
        self.resource_groups = resource_groups

    def require(self, name: str) -> Any:
        """The service, or a 503 naming what is switched off.

        ⛔ Not a 404. A missing feature and a disabled one look identical to a
        client otherwise, and "the endpoint does not exist" sends whoever is
        debugging to the wrong place.
        """
        found: Optional[Any] = getattr(self, name, None)
        if found is None:
            from tms.api.errors import UpstreamUnavailable

            raise UpstreamUnavailable(
                "The {} feature is not enabled on this deployment.".format(
                    name.replace("_", " ")))
        return found
