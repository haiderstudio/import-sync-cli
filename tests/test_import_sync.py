#!/usr/bin/env python3
"""Tests for import_sync. Standard library unittest only."""

import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from import_sync import (  # noqa: E402
    IMPORT_TO_PACKAGE,
    collect_imports,
    normalize,
    package_for_import,
    parse_requirement_name,
    read_requirements,
    run,
)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
EXAMPLES = ROOT / "examples"


def run_cli(argv):
    """Run the CLI in-process and capture stdout/stderr. Returns (code, out, err)."""
    out = io.StringIO()
    err = io.StringIO()
    code = run(argv, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def make_project(files):
    """Create a temp project folder from {relative path: text}."""
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp, root


class TestNormalize(unittest.TestCase):
    def test_lowercase_and_dashes(self):
        self.assertEqual(normalize("Pillow"), "pillow")
        self.assertEqual(normalize("scikit_learn"), "scikit-learn")
        self.assertEqual(normalize("PyYAML"), "pyyaml")
        self.assertEqual(normalize("zope.interface"), "zope-interface")
        self.assertEqual(normalize("Django"), "django")

    def test_mapping_table(self):
        self.assertEqual(package_for_import("PIL"), "Pillow")
        self.assertEqual(package_for_import("sklearn"), "scikit-learn")
        self.assertEqual(package_for_import("yaml"), "PyYAML")
        self.assertEqual(package_for_import("bs4"), "beautifulsoup4")
        self.assertEqual(package_for_import("cv2"), "opencv-python")
        self.assertEqual(package_for_import("requests"), "requests")
        self.assertEqual(len(IMPORT_TO_PACKAGE), 10)


class TestParseRequirementName(unittest.TestCase):
    def test_plain_and_pinned(self):
        self.assertEqual(parse_requirement_name("requests")[0], "requests")
        self.assertEqual(parse_requirement_name("requests==2.31.0")[0], "requests")
        self.assertEqual(parse_requirement_name("flask>=2.0")[0], "flask")
        self.assertEqual(parse_requirement_name("numpy~=1.26")[0], "numpy")
        self.assertEqual(parse_requirement_name("Django<5")[0], "Django")

    def test_extras_markers_and_urls(self):
        self.assertEqual(parse_requirement_name("requests[security]")[0], "requests")
        self.assertEqual(
            parse_requirement_name('uvicorn; python_version < "3.9"')[0], "uvicorn"
        )
        self.assertEqual(
            parse_requirement_name("mypackage @ https://example.com/x.tar.gz")[0],
            "mypackage",
        )

    def test_comments_and_blank(self):
        self.assertEqual(parse_requirement_name("# a comment")[0], None)
        self.assertEqual(parse_requirement_name("")[0], None)
        self.assertEqual(parse_requirement_name("requests  # pinned")[0], "requests")

    def test_option_lines_are_skipped(self):
        name, reason = parse_requirement_name("--index-url https://x")
        self.assertIsNone(name)
        self.assertEqual(reason, "option")
        name, reason = parse_requirement_name("-e .")
        self.assertIsNone(name)
        self.assertEqual(reason, "option")

    def test_include_lines_are_flagged(self):
        name, reason = parse_requirement_name("-r base.txt")
        self.assertIsNone(name)
        self.assertEqual(reason, "include")


class TestReadRequirements(unittest.TestCase):
    def test_reads_and_normalizes(self):
        tmp, root = make_project(
            {"requirements.txt": "requests==2.31.0\nPillow\nscikit_learn\n"}
        )
        try:
            declared, skipped = read_requirements(root / "requirements.txt")
            self.assertEqual(
                declared, {"requests": "requests", "pillow": "Pillow", "scikit-learn": "scikit_learn"}
            )
            self.assertEqual(skipped, [])
        finally:
            tmp.cleanup()

    def test_follows_r_includes(self):
        tmp, root = make_project(
            {
                "requirements.txt": "-r base.txt\nflask\n",
                "base.txt": "requests\n",
            }
        )
        try:
            declared, skipped = read_requirements(root / "requirements.txt")
            self.assertEqual(declared, {"requests": "requests", "flask": "flask"})
            self.assertEqual(skipped, [])
        finally:
            tmp.cleanup()

    def test_include_cycle_is_an_error(self):
        tmp, root = make_project(
            {"requirements.txt": "-r other.txt\n", "other.txt": "-r requirements.txt\n"}
        )
        try:
            with self.assertRaises(ValueError):
                read_requirements(root / "requirements.txt")
        finally:
            tmp.cleanup()

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            read_requirements(Path("/nonexistent/requirements.txt"))

    def test_skipped_lines_are_listed(self):
        tmp, root = make_project(
            {"requirements.txt": "requests\n--index-url https://x\n-e .\n"}
        )
        try:
            declared, skipped = read_requirements(root / "requirements.txt")
            self.assertEqual(declared, {"requests": "requests"})
            self.assertEqual(len(skipped), 2)
            self.assertIn("--index-url", skipped[0][2])
        finally:
            tmp.cleanup()

    def test_duplicates_count_once(self):
        tmp, root = make_project(
            {"requirements.txt": "requests\nrequests==2.0\n"}
        )
        try:
            declared, _skipped = read_requirements(root / "requirements.txt")
            self.assertEqual(declared, {"requests": "requests"})
        finally:
            tmp.cleanup()


class TestCollectImports(unittest.TestCase):
    def test_import_forms(self):
        tmp, root = make_project(
            {
                "code.py": (
                    "import os\n"
                    "import numpy as np\n"
                    "from pandas import DataFrame\n"
                    "from . import local\n"
                    "from .utils import thing\n"
                )
            }
        )
        try:
            imports, unreadable = collect_imports(root, set())
            self.assertEqual(unreadable, [])
            self.assertEqual(set(imports), {"numpy", "pandas"})
            self.assertEqual(imports["numpy"][1], 2)
        finally:
            tmp.cleanup()

    def test_stdlib_is_skipped(self):
        tmp, root = make_project(
            {"code.py": "import os\nimport sys\nimport json\nimport typing\nfrom pathlib import Path\n"}
        )
        try:
            imports, unreadable = collect_imports(root, set())
            self.assertEqual(imports, {})
            self.assertEqual(unreadable, [])
        finally:
            tmp.cleanup()

    def test_first_party_is_skipped(self):
        tmp, root = make_project(
            {
                "app.py": "import requests\n",
                "mypackage/__init__.py": "",
                "mypackage/core.py": "import flask\n",
                "main.py": "import app\nfrom mypackage import core\nimport boto3\n",
            }
        )
        try:
            imports, unreadable = collect_imports(root, set())
            self.assertEqual(set(imports), {"requests", "flask", "boto3"})
        finally:
            tmp.cleanup()

    def test_imports_inside_functions_count(self):
        tmp, root = make_project(
            {"code.py": "def f():\n    import requests\n    return requests\n"}
        )
        try:
            imports, _unreadable = collect_imports(root, set())
            self.assertEqual(set(imports), {"requests"})
        finally:
            tmp.cleanup()

    def test_dotted_import_uses_top_level(self):
        tmp, root = make_project({"code.py": "import google.cloud.storage\n"})
        try:
            imports, _unreadable = collect_imports(root, set())
            self.assertEqual(set(imports), {"google"})
        finally:
            tmp.cleanup()

    def test_excluded_dirs_are_not_walked(self):
        tmp, root = make_project(
            {
                "main.py": "import requests\n",
                "vendor/hidden.py": "import boto3\n",
                ".venv/lib/code.py": "import flask\n",
            }
        )
        try:
            imports, _unreadable = collect_imports(root, {"vendor", ".venv"})
            self.assertEqual(set(imports), {"requests"})
        finally:
            tmp.cleanup()

    def test_unreadable_file_is_reported(self):
        tmp, root = make_project({"broken.py": "def broken(:\n", "good.py": "import requests\n"})
        try:
            imports, unreadable = collect_imports(root, set())
            self.assertEqual(set(imports), {"requests"})
            self.assertEqual(len(unreadable), 1)
            self.assertIn("broken.py", str(unreadable[0][0]))
        finally:
            tmp.cleanup()


class TestCliDrift(unittest.TestCase):
    def test_drift_found_exit_1(self):
        tmp, root = make_project(
            {
                "app.py": "import requests\nimport numpy\n",
                "requirements.txt": "requests\nboto3\n",
            }
        )
        try:
            code, out, _err = run_cli([str(root)])
            self.assertEqual(code, 1)
            self.assertIn("MISSING numpy (imported as numpy in app.py:2)", out)
            self.assertIn("UNUSED  boto3 (declared in requirements.txt, never imported)", out)
            self.assertIn("2 third-party import(s), 2 declared, 1 missing, 1 unused", out)
            self.assertIn("DRIFT", out)
        finally:
            tmp.cleanup()

    def test_clean_exit_0(self):
        tmp, root = make_project(
            {
                "app.py": "import requests\n",
                "requirements.txt": "requests==2.31.0\n",
            }
        )
        try:
            code, out, _err = run_cli([str(root)])
            self.assertEqual(code, 0)
            self.assertIn("OK — imports match requirements.txt", out)
            self.assertIn("1 third-party import(s), 1 declared, 0 missing, 0 unused", out)
        finally:
            tmp.cleanup()

    def test_mapping_avoids_false_missing(self):
        tmp, root = make_project(
            {
                "app.py": "from PIL import Image\nimport yaml\n",
                "requirements.txt": "Pillow\nPyYAML\n",
            }
        )
        try:
            code, out, _err = run_cli([str(root)])
            self.assertEqual(code, 0)
            self.assertIn("OK — imports match requirements.txt", out)
        finally:
            tmp.cleanup()

    def test_ignore_covers_both_sides(self):
        tmp, root = make_project(
            {
                "app.py": "import requests\n",
                "requirements.txt": "requests\nboto3\n",
            }
        )
        try:
            code, out, _err = run_cli(["--ignore", "boto3", str(root)])
            self.assertEqual(code, 0)
            code, out, _err = run_cli(["--ignore", "requests", str(root)])
            self.assertEqual(code, 1)
            self.assertIn("UNUSED  boto3", out)
        finally:
            tmp.cleanup()

    def test_custom_requirements_path(self):
        tmp, root = make_project(
            {
                "app.py": "import requests\n",
                "deps.txt": "requests\n",
            }
        )
        try:
            code, out, _err = run_cli(["--requirements", str(root / "deps.txt"), str(root)])
            self.assertEqual(code, 0)
        finally:
            tmp.cleanup()

    def test_default_project_is_current_folder(self):
        tmp, root = make_project(
            {
                "app.py": "import requests\n",
                "requirements.txt": "requests\n",
            }
        )
        try:
            import os

            old = os.getcwd()
            os.chdir(root)
            try:
                code, out, _err = run_cli([])
                self.assertEqual(code, 0)
            finally:
                os.chdir(old)
        finally:
            tmp.cleanup()


class TestCliErrors(unittest.TestCase):
    def test_missing_project_is_exit_2(self):
        code, _out, err = run_cli(["/nonexistent/project"])
        self.assertEqual(code, 2)
        self.assertIn("project folder not found", err)

    def test_missing_requirements_is_exit_2(self):
        tmp, root = make_project({"app.py": "import requests\n"})
        try:
            code, out, _err = run_cli([str(root)])
            self.assertEqual(code, 2)
            self.assertIn("requirements file not found", out)
        finally:
            tmp.cleanup()

    def test_unreadable_python_file_is_exit_2(self):
        tmp, root = make_project(
            {"broken.py": "def broken(:\n", "requirements.txt": "requests\n"}
        )
        try:
            code, out, _err = run_cli([str(root)])
            self.assertEqual(code, 2)
            self.assertIn("could not parse", out)
        finally:
            tmp.cleanup()

    def test_bad_flag_is_exit_2(self):
        code, _out, err = run_cli(["--no-such-flag"])
        self.assertEqual(code, 2)
        self.assertIn("unrecognized arguments", err)


class TestExamplesAndDocs(unittest.TestCase):
    def test_sample_output_matches_real_run(self):
        result = subprocess.run(
            [sys.executable, "src/import_sync.py", "examples/sample-project"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1)
        committed = (EXAMPLES / "sample-output.txt").read_text(encoding="utf-8")
        self.assertEqual(result.stdout, committed)

    def test_clean_example_is_clean(self):
        result = subprocess.run(
            [sys.executable, "src/import_sync.py", "examples/clean-project"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("OK — imports match requirements.txt", result.stdout)

    def test_readme_shows_real_outputs(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        sample = (EXAMPLES / "sample-output.txt").read_text(encoding="utf-8")
        for line in sample.strip().splitlines():
            self.assertIn(line, readme)

    def test_run_example_script_works(self):
        result = subprocess.run(
            ["sh", "examples/run-example.sh"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("MISSING PyYAML", result.stdout)
        self.assertIn("OK — imports match requirements.txt", result.stdout)

    def test_runs_from_any_folder(self):
        tmp, root = make_project(
            {
                "app.py": "import requests\n",
                "requirements.txt": "requests\n",
            }
        )
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "src" / "import_sync.py"),
                    str(root),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("OK — imports match requirements.txt", result.stdout)
        finally:
            tmp.cleanup()


class TestStdlibOnly(unittest.TestCase):
    def test_no_third_party_imports(self):
        allowed = set(sys.stdlib_module_names) | {"import_sync"}
        for path in (ROOT / "src").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for line in text.splitlines():
                if line.startswith("import "):
                    for name in line[7:].split(","):
                        top = name.strip().split(".")[0].split(" ")[0]
                        self.assertIn(top, allowed, "%s in %s" % (top, path))
                elif line.startswith("from "):
                    top = line.split()[1].split(".")[0]
                    self.assertIn(top, allowed, "%s in %s" % (top, path))
        for path in (ROOT / "tests").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for line in text.splitlines():
                if line.startswith("import "):
                    for name in line[7:].split(","):
                        top = name.strip().split(".")[0].split(" ")[0]
                        self.assertIn(top, allowed, "%s in %s" % (top, path))
                elif line.startswith("from "):
                    top = line.split()[1].split(".")[0]
                    self.assertIn(
                        top, allowed | {"import_sync"}, "%s in %s" % (top, path)
                    )


class TestRepoHygiene(unittest.TestCase):
    def test_no_placeholders_or_secrets(self):
        banned = (
            "TODO",
            "FIXME",
            "lorem",
            "coming soon",
            "API_KEY",
            "api_key",
            "password =",
        )
        # The tests directory is excluded from this scan: the test file
        # itself names the banned words in its own check list, and it
        # uses temp paths. The shipped content is what must be clean.
        # .git and __pycache__ are excluded too: GitHub's checkout ships
        # sample hooks under .git/hooks/ that contain "TODO", so a scan
        # without the exclusion passes locally and fails on CI.
        shipped = [
            path
            for path in ROOT.rglob("*")
            if path.is_file()
            and "__pycache__" not in str(path)
            and ".git" not in path.parts
            and "tests" not in path.parts
        ]
        for path in shipped:
            text = path.read_text(errors="ignore")
            for word in banned:
                self.assertNotIn(word, text, "%s in %s" % (word, path))

    def test_no_absolute_paths(self):
        # Same exclusion as the placeholder scan: the test file itself
        # names temp paths in its own check list.
        shipped = [
            path
            for path in ROOT.rglob("*")
            if path.is_file()
            and "__pycache__" not in str(path)
            and ".git" not in path.parts
            and "tests" not in path.parts
        ]
        for path in shipped:
            text = path.read_text(errors="ignore")
            self.assertNotIn(
                "/home/hatch", text, "absolute path in %s" % path
            )
            self.assertNotIn("/tmp/", text, "absolute path in %s" % path)

    def test_gitignore_lists_env_first(self):
        lines = (ROOT / ".gitignore").read_text().splitlines()
        self.assertEqual(lines[0], ".env")


if __name__ == "__main__":
    unittest.main()
