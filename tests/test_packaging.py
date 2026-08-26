"""Guards on what actually ends up in the installed package.

The console is a built bundle on disk, not Python modules, so setuptools does
not ship it unless pyproject says so explicitly. Getting this wrong is
invisible in development - the test suite and every local run import from src/
on sys.path, where the files are always present - and only shows up on a real
install, as

    RuntimeError: Directory 'tms/ui/assets/static' does not exist

at tms-api startup. That is exactly how it was found (2026-08-07, first
production deploy, when the same rule applied to the Jinja templates). These
tests fail in development instead.

Python 3.9 compatible: no tomllib (3.11+), so the declaration is parsed as text.
"""

import glob
import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYPROJECT = os.path.join(REPO, "pyproject.toml")
UI_DIR = os.path.join(REPO, "src", "tms", "ui")


def declared_patterns(package="tms.ui"):
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
    """Every non-Python file under src/tms/ui, relative to that directory."""
    found = []
    for root, dirs, files in os.walk(UI_DIR):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if name.endswith((".py", ".pyc")):
                continue
            rel = os.path.relpath(os.path.join(root, name), UI_DIR)
            found.append(rel.replace(os.sep, "/"))
    return sorted(found)


class WebAssetPackagingTest(unittest.TestCase):
    def test_package_data_is_declared_at_all(self):
        self.assertTrue(
            declared_patterns(),
            "pyproject.toml declares no package-data for tms.ui. The built "
            "console will be missing from the wheel and tms-api will serve "
            "nothing at /.",
        )

    def test_there_are_assets_to_ship(self):
        """If this ever finds nothing, the test below would pass vacuously."""
        files = shipped_data_files()
        self.assertTrue(files, "no console assets found under src/tms/ui - "
                               "run `npm --prefix frontend run build`")
        self.assertIn("assets/index.html", files)
        self.assertTrue([f for f in files if f.startswith("assets/static/")])

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
            for path in glob.glob(os.path.join(UI_DIR, pattern), recursive=True):
                if os.path.isfile(path):
                    rel = os.path.relpath(path, UI_DIR).replace(os.sep, "/")
                    covered.add(rel)
        uncovered = [path for path in shipped_data_files() if path not in covered]
        self.assertEqual(
            [],
            uncovered,
            "these files live under src/tms/ui but match no package-data glob "
            "in pyproject.toml, so they will NOT be installed: {}. Declared "
            "patterns: {}. Note that 'assets/*' does not match files in a "
            "subdirectory - 'assets/static/*' is a separate entry for exactly "
            "that reason.".format(
                uncovered, patterns
            ),
        )


if __name__ == "__main__":
    unittest.main()
