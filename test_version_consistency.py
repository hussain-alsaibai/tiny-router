"""Every surface that states tiny-router's version must agree.

`package.json` sat at 0.2.0 while `pyproject.toml` and `__version__` were at
0.7.5. Nothing reads the file - not the build, not the workflow, not any test -
so it drifted for five minor releases with no failure anywhere.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    assert match, "pyproject.toml has no version field"
    return match.group(1)


def _module_version() -> str:
    text = (ROOT / "tiny_router.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.M)
    assert match, "tiny_router.py has no __version__"
    return match.group(1)


def _package_json() -> dict:
    return json.loads((ROOT / "package.json").read_text(encoding="utf-8"))


class TestVersionConsistency(unittest.TestCase):
    def test_pyproject_matches_module_version(self) -> None:
        self.assertEqual(_pyproject_version(), _module_version())

    def test_version_is_semver(self) -> None:
        version = _pyproject_version()
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")

    def test_package_json_matches_pyproject(self) -> None:
        self.assertEqual(
            _package_json()["version"],
            _pyproject_version(),
            "package.json version does not match pyproject.toml",
        )

    def test_package_json_name_matches(self) -> None:
        self.assertEqual(_package_json()["name"], "tiny-router")

    def test_changelog_documents_the_shipped_version(self) -> None:
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        version = _pyproject_version()
        self.assertRegex(
            changelog,
            r"(?m)^## \[?%s\]?" % re.escape(version),
            "CHANGELOG.md has no entry for %s" % version,
        )


if __name__ == "__main__":
    unittest.main()
