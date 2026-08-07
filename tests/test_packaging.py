"""Guards on what actually ends up in the installed package.

The UI is served from files on disk (Jinja templates, CSS, JS). Those are not
Python modules, so setuptools does not ship them unless pyproject says so
explicitly. Getting this wrong is invisible in development - the test suite and
every local run import from src/ on sys.path, where the files are always
present - and only shows up on a real install, as

    RuntimeError: Directory 'tms/web/static' does not exist

at tms-api startup. That is exactly how it was found (2026-08-07, first
production deploy). These tests fail in development instead.

Python 3.9 compatible: no tomllib (3.11+), so the declaration is parsed as text.
"""

import glob
import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYPROJECT = os.path.join(REPO, "pyproject.toml")
WEB_DIR = os.path.join(REPO, "src", "tms", "web")


def declared_patterns(package="tms.web"):
    """Globs declared under [tool.setuptools.package-data] for `package`."""
    with open(PYPROJECT, encoding="utf-8") as handle:
        text = handle.read()
    section = re.search(
        r"^\[tool\.setuptools\.package-data\]\s*$(.*?)(?=^\[|\Z)",
        text,
        re.M | re.S,
    )
    if not section:
        return []
    entry = re.search(
        r'^\s*"?{}"?\s*=\s*\[(.*?)\]'.format(re.escape(package)),
        section.group(1),
        re.M | re.S,
    )
    if not entry:
        return []
    return re.findall(r'"([^"]+)"', entry.group(1))


def shipped_data_files():
    """Every non-Python file under src/tms/web, relative to that directory."""
    found = []
    for root, dirs, files in os.walk(WEB_DIR):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if name.endswith((".py", ".pyc")):
                continue
            rel = os.path.relpath(os.path.join(root, name), WEB_DIR)
            found.append(rel.replace(os.sep, "/"))
    return sorted(found)


class WebAssetPackagingTest(unittest.TestCase):
    def test_package_data_is_declared_at_all(self):
        self.assertTrue(
            declared_patterns(),
            "pyproject.toml declares no package-data for tms.web. The templates "
            "and static files will be missing from the wheel and tms-api will "
            "fail at startup.",
        )

    def test_there_are_assets_to_ship(self):
        """If this ever finds nothing, the test below would pass vacuously."""
        files = shipped_data_files()
        self.assertTrue(files, "no UI assets found under src/tms/web")
        self.assertIn("static/tms.css", files)
        self.assertIn("templates/base.html", files)

    def test_every_asset_matches_a_declared_pattern(self):
        """Uses glob, not fnmatch.

        They disagree exactly where it matters: fnmatch's '*' happily crosses a
        '/', so 'templates/*' would appear to cover 'templates/partials/x.html'.
        setuptools globs the package directory, where '*' stops at a separator -
        so that file would silently not ship. Match setuptools' semantics.
        """
        patterns = declared_patterns()
        covered = set()
        for pattern in patterns:
            for path in glob.glob(os.path.join(WEB_DIR, pattern), recursive=True):
                if os.path.isfile(path):
                    rel = os.path.relpath(path, WEB_DIR).replace(os.sep, "/")
                    covered.add(rel)
        uncovered = [path for path in shipped_data_files() if path not in covered]
        self.assertEqual(
            [],
            uncovered,
            "these files live under src/tms/web but match no package-data glob "
            "in pyproject.toml, so they will NOT be installed: {}. Declared "
            "patterns: {}. Note that 'templates/*' does not match files in a "
            "subdirectory - add 'templates/**/*' if you introduce one.".format(
                uncovered, patterns
            ),
        )

    def test_templates_referenced_by_routes_exist(self):
        """A typo'd template name only fails when that page is requested."""
        routes_py = os.path.join(REPO, "src", "tms", "web", "routes.py")
        with open(routes_py, encoding="utf-8") as handle:
            source = handle.read()
        referenced = set(re.findall(r'"([A-Za-z0-9_/]+\.html)"', source))
        self.assertTrue(referenced, "no template names found in routes.py")
        missing = [
            name
            for name in sorted(referenced)
            if not os.path.exists(os.path.join(WEB_DIR, "templates", name))
        ]
        self.assertEqual([], missing, "routes.py renders templates that do not exist")


if __name__ == "__main__":
    unittest.main()
