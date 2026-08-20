"""SQL leaves TMS in one place, and never on the polling path (D-012, A1).

Until 2026-08-21 principle A1 was enforced by a permission: the OPA `queries`
rule withheld `execute`, so TMS *could not* submit SQL however badly it was
written. D-012 granted that permission because FR-FL-02 and FR-BM-01 were both
stuck behind it, and the costs A1 listed turned out to be functions of polling
frequency rather than of SQL itself.

That trade only holds while the frequency stays near zero. So the enforcement
moved from the permission to here: the collector - the only component that runs
on a timer - must not be able to reach the SQL client at all.

⛔ If this file is failing, do not add the import to an allowlist. Ask instead
whether the new caller runs on a timer. If it does, D-012's reasoning no longer
covers it and the decision has to be reopened.
"""

import ast
import os
import pathlib
import sys
import unittest

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

SQL_MODULE = "tms.clients.sql"


def _imports(path: pathlib.Path):
    """Every module name this file imports, however it spells it."""
    tree = ast.parse(path.read_text(), filename=str(path))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            # `from tms.clients import sql` names the module in the alias.
            found.update("{}.{}".format(node.module, a.name) for a in node.names)
    return found


class SqlIsolationTest(unittest.TestCase):
    def test_the_collector_cannot_reach_the_sql_client(self):
        """The collector is the only thing that runs on a timer."""
        offenders = []
        for path in sorted((SRC / "tms" / "collector").rglob("*.py")):
            if SQL_MODULE in _imports(path):
                offenders.append(str(path.relative_to(SRC)))
        self.assertEqual(
            [], offenders,
            "these collector modules import the SQL client, which would put "
            "SQL back on the polling path and undo what D-012 traded away: "
            "{}".format(offenders))

    def test_sql_is_submitted_from_exactly_one_module(self):
        """A second submission point is a second thing to audit, and the one
        nobody remembers to look at."""
        submitters = []
        for path in sorted((SRC / "tms").rglob("*.py")):
            if path.name == "sql.py" and path.parent.name == "clients":
                continue
            if "/v1/statement" in path.read_text():
                submitters.append(str(path.relative_to(SRC)))
        self.assertEqual([], submitters, submitters)

    def test_the_health_and_fleet_pollers_are_covered_by_the_sweep(self):
        """A guard that walks an empty directory passes for the wrong reason."""
        modules = list((SRC / "tms" / "collector").rglob("*.py"))
        names = {p.name for p in modules}
        self.assertGreater(len(modules), 5, "collector sweep found almost nothing")
        for expected in ("poller.py", "fleet_poller.py", "health_writer.py"):
            self.assertIn(expected, names)


if __name__ == "__main__":
    unittest.main()
