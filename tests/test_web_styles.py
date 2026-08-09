"""Every class a template uses must exist in the stylesheet.

Written after shipping a screen whose KPI block referenced class names that
were never in `tms.css`. It rendered - as unstyled text, in production, looking
like a broken page rather than a missing rule. Nothing caught it because
templates and CSS have no compiler between them.

The check is deliberately one-directional. Unused CSS is untidy; a class that
exists only in a template is a visibly broken screen.
"""

import os
import pathlib
import re
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))

STATIC = pathlib.Path(_ROOT, "src", "tms", "web", "static")
TEMPLATES = pathlib.Path(_ROOT, "src", "tms", "web", "templates")

# Written by tms.js rather than by a template, so no template mentions them.
JS_APPLIED = {"is-long"}

_JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", re.S)


def declared_classes():
    css = (STATIC / "tms.css").read_text(encoding="utf-8")
    # Strip declaration blocks so property values cannot look like selectors.
    selectors = re.sub(r"\{[^{}]*\}", " ", css)
    return set(re.findall(r"\.([a-zA-Z][a-zA-Z0-9_-]*)", selectors)) | JS_APPLIED


def used_classes():
    """class="..." across every template, with Jinja expressions removed.

    A `class="console__line--{{ line.level }}"` yields the literal prefix only;
    the dynamic tail is checked by the tests that render real payloads.
    """
    used = {}
    for path in sorted(TEMPLATES.glob("*.html")):
        text = _JINJA.sub(" ", path.read_text(encoding="utf-8"))
        for attr in re.findall(r'class="([^"]*)"', text):
            for name in attr.split():
                name = name.strip()
                # A trailing dash means the Jinja tail was stripped from a
                # modifier; the prefix on its own is not a real class.
                if not name or name.endswith("-") or not re.match(r"^[a-zA-Z]", name):
                    continue
                used.setdefault(name, path.name)
    return used


class StylesheetTest(unittest.TestCase):
    def test_every_template_class_is_styled(self):
        declared = declared_classes()
        missing = {name: where for name, where in used_classes().items()
                   if name not in declared}
        self.assertEqual(
            {}, missing,
            "these classes appear in templates but not in tms.css, so they "
            "render unstyled: {}".format(missing))

    def test_the_check_would_notice_an_invented_class(self):
        """Guard the guard - it is worthless if it cannot fail."""
        self.assertNotIn("totally-invented-class", declared_classes())


if __name__ == "__main__":
    unittest.main()
