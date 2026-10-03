"""Real Git regression checks for root and nested skill installation."""

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(os.environ.get(
    "SKILL_INSTALLER_TEST_SCRIPT",
    str(Path(__file__).with_name("install-skill-from-github.py")),
))
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("installer_under_test", SCRIPT)
installer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = installer
spec.loader.exec_module(installer)


def git(repo, *args):
    return subprocess.check_output(
        ["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
         "-C", str(repo), *args], text=True, stderr=subprocess.PIPE,
    ).strip()


class GitInstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.origin = self.root / "origin"
        self.origin.mkdir()
        git(self.origin, "init", "-b", "main")
        git(self.origin, "config", "user.name", "Installer Test")
        git(self.origin, "config", "user.email", "test@example.invalid")
        self.files = {
            "SKILL.md": "# Root skill\n",
            ".gitignore": "__pycache__/\n",
            "scripts/run.py": "print('root payload')\n",
            "docs/usage.md": "Root usage\n",
            "templates/profile.json": "{}\n",
            "skills/alpha/SKILL.md": "# Alpha\n",
            "skills/alpha/scripts/run.py": "print('alpha')\n",
            "skills/beta/SKILL.md": "# Beta\n",
            "skills/beta/docs/usage.md": "Beta usage\n",
            "unselected/payload.txt": "Do not install with a nested skill\n",
        }
        for name, content in self.files.items():
            path = self.origin / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        git(self.origin, "add", ".")
        git(self.origin, "commit", "-m", "Add fixture skills")
        self.commit = git(self.origin, "rev-parse", "HEAD")

    def checkout(self, paths, ref="main"):
        directory = tempfile.mkdtemp(dir=self.root)
        source = installer.Source("fixture", "skills", ref, paths,
                                  repo_url=self.origin.as_uri())
        return Path(installer._prepare_repo(source, "git", directory))

    def assert_payload(self, directory, expected):
        actual = {str(p.relative_to(directory)): p.read_text()
                  for p in directory.rglob("*") if p.is_file()
                  and ".git" not in p.relative_to(directory).parts}
        self.assertEqual(actual, expected)
        self.assertFalse((directory / ".git").exists(), "Git metadata was installed")

    def test_root_paths_include_complete_payload(self):
        for paths, ref in [(["."], "main"), (["./"], self.commit),
                           ([".", "skills/alpha"], "main")]:
            with self.subTest(paths=paths, ref=ref):
                checkout = self.checkout(paths, ref)
                destination = Path(tempfile.mkdtemp(dir=self.root)) / "installed"
                installer._copy_skill(str(checkout / paths[0]), str(destination))
                self.assert_payload(destination, self.files)
                with self.assertRaises(installer.InstallError):
                    installer._copy_skill(str(checkout / paths[0]), str(destination))
                self.assert_payload(destination, self.files)

    def test_nested_skills_keep_sparse_selection(self):
        paths = ["skills/alpha", "skills/beta"]
        checkout = self.checkout(paths)
        self.assertFalse((checkout / "unselected/payload.txt").exists())
        self.assertFalse((checkout / "scripts/run.py").exists())
        for path in paths:
            destination = self.root / Path(path).name
            installer._copy_skill(str(checkout / path), str(destination))
            expected = {name[len(path) + 1:]: value for name, value in self.files.items()
                        if name.startswith(path + "/")}
            self.assert_payload(destination, expected)

    def test_root_honors_tag_instead_of_default_branch(self):
        git(self.origin, "tag", "before-update")
        (self.origin / "docs/usage.md").write_text("Changed on main\n")
        git(self.origin, "commit", "-am", "Change default branch")
        checkout = self.checkout(["."], "before-update")
        destination = self.root / "pinned"
        installer._copy_skill(str(checkout), str(destination))
        self.assert_payload(destination, self.files)


if __name__ == "__main__":
    unittest.main()
