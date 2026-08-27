"""Every class the console uses must exist in the stylesheet.

Written after shipping a screen whose KPI block referenced class names that
were never in `tms.css`. It rendered - as unstyled text, in production, looking
like a broken page rather than a missing rule. Nothing catches it otherwise:
TypeScript checks the code, not the strings inside `className`.

⛔ This is the guard on the rule that has been broken three times while porting
screens: read the real class name out of the stylesheet, do not invent one that
sounds right.

The check is deliberately one-directional. Unused CSS is untidy; a class that
exists only in a component is a visibly broken screen.
"""

import os
import pathlib
import re
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND = pathlib.Path(_ROOT, "frontend", "src")
STYLESHEET = FRONTEND / "tms.css"

#: Applied by a library or by the browser rather than written in a component.
EXTERNAL = {"is-long"}

#: `className={`chart__series--${slot}`}` yields a prefix whose tail is a
#: number. The numbered variants are declared; the bare prefix is not a class.
_TEMPLATE_HOLE = re.compile(r"\$\{[^}]*\}")


def declared_classes():
    css = STYLESHEET.read_text(encoding="utf-8")
    # Strip declaration blocks so property values cannot look like selectors.
    selectors = re.sub(r"\{[^{}]*\}", " ", css)
    return set(re.findall(r"\.([a-zA-Z][a-zA-Z0-9_-]*)", selectors)) | EXTERNAL


def used_classes():
    """`className="..."` and `className={`...`}` across every component.

    A template hole is replaced by a space, so only the literal parts are
    checked. `chart__series--` on its own is dropped by the trailing-dash rule
    below - the dynamic tail is covered by the tests that render real payloads.
    """
    used = {}
    for path in sorted(FRONTEND.rglob("*.tsx")):
        text = path.read_text(encoding="utf-8")
        literals = re.findall(r'className="([^"]*)"', text)
        literals += re.findall(r"className=\{`([^`]*)`\}", text)
        # Lookup tables of state -> class, which several screens use instead of
        # a chain of ternaries in the markup.
        literals += re.findall(r'(?:klass|className):\s*"((?:status|banner|test-chip)[^"]*)"',
                               text)
        for attr in literals:
            for name in _TEMPLATE_HOLE.sub(" ", attr).split():
                name = name.strip()
                if not name or name.endswith("-") or not re.match(r"^[a-zA-Z]", name):
                    continue
                used.setdefault(name, str(path.relative_to(FRONTEND)))
    return used


class StylesheetTest(unittest.TestCase):
    def test_every_class_the_console_uses_is_styled(self):
        declared = declared_classes()
        missing = {name: where for name, where in used_classes().items()
                   if name not in declared}
        self.assertEqual(
            {}, missing,
            "these classes appear in components but not in tms.css, so they "
            "render unstyled: {}".format(missing))

    def test_the_check_reads_something(self):
        """A guard that scans nothing passes for the wrong reason."""
        self.assertGreater(len(used_classes()), 100)

    def test_the_check_would_notice_an_invented_class(self):
        """Guard the guard - it is worthless if it cannot fail."""
        self.assertNotIn("totally-invented-class", declared_classes())


if __name__ == "__main__":
    unittest.main()


class ClusterSelectionTest(unittest.TestCase):
    """Which cluster a screen shows has exactly one source.

    ⛔ Live Queries shipped with `params.get("cluster") ?? "prod-a"` - a literal
    from the test harness. Every deployment whose clusters are named anything
    else got a 404 the moment somebody opened the screen without ?cluster= in
    the URL, and the browser tests could not see it because the harness cluster
    really is called prod-a.

    The fix is not "do not hardcode that one name": it is that reading the
    cluster out of the query string belongs to `useCluster`, which falls back
    to the first cluster the *server* lists.
    """

    def test_only_the_cluster_hook_reads_the_cluster_parameter(self):
        offenders = {}
        for path in sorted(FRONTEND.rglob("*.tsx")) + sorted(FRONTEND.rglob("*.ts")):
            if path.name == "ClusterTabs.tsx":
                continue
            text = path.read_text(encoding="utf-8")
            if re.search(r'\bparams\.get\(\s*["\']cluster["\']\s*\)', text):
                offenders[str(path.relative_to(FRONTEND))] = 'params.get("cluster")'
        self.assertEqual(
            {}, offenders,
            "these read the cluster themselves instead of calling useCluster(), "
            "so they need their own fallback - and every fallback that is not "
            "the server's cluster list is a name somebody made up: {}".format(
                offenders))

    def test_no_screen_hardcodes_a_cluster_name(self):
        """The harness names, specifically. They are what gets typed by
        accident, and they are the ones that make a demo look correct."""
        offenders = {}
        for path in sorted(FRONTEND.rglob("*.tsx")):
            text = path.read_text(encoding="utf-8")
            for line in text.splitlines():
                if line.lstrip().startswith(("//", "*", "/*")):
                    continue  # the comment explaining the bug names it
                if re.search(r'["\']prod-[ab]["\']', line):
                    offenders[str(path.relative_to(FRONTEND))] = line.strip()
        self.assertEqual({}, offenders,
                         "cluster names come from /clusters: {}".format(offenders))


class FutureTimeTest(unittest.TestCase):
    """`relativeTime` clamps at zero, so every future moment reads "just now".

    A schedule due tomorrow said it was about to fire. There is a separate
    helper for the future direction; this is the guard that no screen reaches
    for the past one to describe it.
    """

    def test_no_screen_flips_the_past_helper_into_the_future(self):
        offenders = {}
        for path in sorted(FRONTEND.rglob("*.tsx")):
            text = path.read_text(encoding="utf-8")
            if re.search(r"relativeTime\([^)]*\)\s*\n?\s*\.replace", text):
                offenders[str(path.relative_to(FRONTEND))] = "relativeTime(...).replace"
        self.assertEqual(
            {}, offenders,
            "these rewrite the past-tense helper's words instead of using "
            "untilTime(): {}".format(offenders))
