#!/usr/bin/env python3
"""Check Python imports against requirements.txt, fail CI on drift.

Run: python3 src/import_sync.py .
     python3 src/import_sync.py myproject
     python3 src/import_sync.py --requirements reqs.txt myproject
     python3 src/import_sync.py --ignore boto3,pytest .

The tool walks a project for Python files, reads the third-party imports,
and compares them with requirements.txt. It reports two kinds of drift:

- MISSING: imported in code, but not declared in requirements.txt.
- UNUSED: declared in requirements.txt, but never imported.

It is report-only: it never writes or changes any file. Exit code 0 means
no drift, 1 means drift was found, 2 means a usage or input problem
(missing project folder, missing requirements file, a Python file that
cannot be parsed).

What is skipped, and why:

- Standard library imports (os, sys, json, ...) — checked against
  sys.stdlib_module_names, so they never count as third-party.
- First-party imports — a name that matches a top-level .py file or a
  top-level package folder in the project is the project's own code,
  not a dependency.
- Relative imports (from . import x) — they are inside the project.

Package names are compared the PEP 503 way: lowercased, with runs of
"-", "_" and "." treated as one "-". A small fixed table maps import
names that differ from their package names (PIL -> Pillow,
sklearn -> scikit-learn, yaml -> PyYAML, and a few more). Anything
else must match by its normalized name, or be named with --ignore.

Python standard library only.
"""

import argparse
import ast
import contextlib
import re
import sys
from pathlib import Path

VERSION = "1.0.0"

# Import names that differ from the package that provides them.
# A fixed, documented list — only these names are mapped.
IMPORT_TO_PACKAGE = {
    "PIL": "Pillow",
    "sklearn": "scikit-learn",
    "yaml": "PyYAML",
    "bs4": "beautifulsoup4",
    "dateutil": "python-dateutil",
    "cv2": "opencv-python",
    "jwt": "PyJWT",
    "psycopg2": "psycopg2-binary",
    "dotenv": "python-dotenv",
    "attr": "attrs",
}

# Directory names never walked while looking for Python files.
DEFAULT_EXCLUDES = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    ".tox",
    "build",
    "dist",
    ".eggs",
}

NAME_CHARS = re.compile(r"^[-A-Za-z0-9_.]+$")


def normalize(name):
    """Normalize a package or import name the PEP 503 way."""
    return re.sub(r"[-_.]+", "-", name).lower()


def package_for_import(import_name):
    """Return the package name an import name belongs to."""
    return IMPORT_TO_PACKAGE.get(import_name, import_name)


def parse_args(argv):
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="import_sync",
        description=(
            "Check Python imports against requirements.txt. Reports "
            "imports that are not declared (MISSING) and declared "
            "packages that are never imported (UNUSED). Exits 1 on "
            "drift, 2 on a usage problem."
        ),
    )
    parser.add_argument(
        "project",
        nargs="?",
        default=".",
        metavar="PROJECT",
        help="Project folder to scan (default: current folder).",
    )
    parser.add_argument(
        "--requirements",
        metavar="FILE",
        help="Requirements file to check against "
        "(default: requirements.txt in the project folder).",
    )
    parser.add_argument(
        "--exclude",
        metavar="DIRS",
        help="Comma-separated directory names to skip while scanning, "
        "in addition to the built-in list (.git, __pycache__, "
        ".venv, venv, env, .tox, build, dist, .eggs).",
    )
    parser.add_argument(
        "--ignore",
        metavar="PKGS",
        help="Comma-separated package names to ignore on both sides "
        "(an ignored import is not reported missing, an ignored "
        "declared package is not reported unused).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="import_sync " + VERSION,
    )
    return parser.parse_args(argv)


def split_list(value):
    """Split a comma-separated flag value into a list of names."""
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def first_party_names(project):
    """Return top-level module names that belong to the project itself.

    A top-level .py file (app.py -> app) or a top-level folder with an
    __init__.py (mypackage/__init__.py -> mypackage) is first-party:
    importing it is not a dependency on an outside package.
    """
    names = set()
    for path in project.iterdir():
        if path.is_file() and path.suffix == ".py":
            names.add(path.stem)
        elif path.is_dir() and (path / "__init__.py").is_file():
            names.add(path.name)
    return names


def collect_imports(project, excludes):
    """Walk a project and collect third-party imports.

    Returns (imports, unreadable) where imports maps an import name to
    the (file, line) of its first sighting, and unreadable is a list of
    (file, error) for Python files that could not be parsed.
    """
    first_party = first_party_names(project)
    stdlib = set(sys.stdlib_module_names)
    imports = {}
    unreadable = []

    def walk(folder):
        for path in sorted(folder.iterdir()):
            if path.is_dir():
                if path.name in excludes or path.name.startswith("."):
                    continue
                if path.name.endswith(".egg-info"):
                    continue
                walk(path)
            elif path.is_file() and path.suffix == ".py":
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8"))
                except (SyntaxError, UnicodeDecodeError) as exc:
                    unreadable.append((path, str(exc)))
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            add_import(
                                alias.name.split(".")[0],
                                node.lineno,
                                path,
                                project,
                                first_party,
                                stdlib,
                                imports,
                            )
                    elif isinstance(node, ast.ImportFrom):
                        if node.level:
                            continue
                        if node.module:
                            add_import(
                                node.module.split(".")[0],
                                node.lineno,
                                path,
                                project,
                                first_party,
                                stdlib,
                                imports,
                            )

    walk(project)
    return imports, unreadable


def add_import(name, lineno, path, project, first_party, stdlib, imports):
    """Record one import, unless it is stdlib, first-party, or seen."""
    if name in stdlib or name in first_party or name in imports:
        return
    try:
        shown = str(path.relative_to(project))
    except ValueError:
        shown = str(path)
    imports[name] = (shown, lineno)


def parse_requirement_name(line):
    """Return the package name from one requirements line, or None.

    Returns (name, skipped_reason): name is the package name, or None
    when the line is not a package (an option line, or text that cannot
    be read as a package name). skipped_reason says why, or is None.
    """
    text = line.strip()
    if not text or text.startswith("#"):
        return None, None
    if "#" in text:
        text = text.split("#", 1)[0].strip()
        if not text:
            return None, None
    if text.startswith("-r") or text.startswith("--requirement"):
        return None, "include"
    if text.startswith("-"):
        return None, "option"
    if ";" in text:
        text = text.split(";", 1)[0].strip()
    if "@" in text:
        text = text.split("@", 1)[0].strip()
    if "[" in text:
        text = text.split("[", 1)[0].strip()
    for sep in ("=", "<", ">", "!", "~", " ", "\t"):
        if sep in text:
            text = text.split(sep, 1)[0].strip()
    if not text or not NAME_CHARS.match(text):
        return None, "unreadable"
    return text, None


def read_requirements(path, seen=None):
    """Read a requirements file, following -r includes.

    Returns (declared, skipped) where declared maps a normalized
    package name to the name as written, and skipped is a list of
    (file, line number, text) for lines that were not read as packages.
    Raises FileNotFoundError for a missing file and ValueError for a
    repeated include (a cycle).
    """
    if seen is None:
        seen = set()
    resolved = path.resolve()
    if resolved in seen:
        raise ValueError("requirements include cycle at %s" % path)
    seen.add(resolved)
    declared = {}
    skipped = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for number, raw in enumerate(lines, start=1):
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        if text.startswith("-r") or text.startswith("--requirement"):
            if "=" in text:
                include = text.split("=", 1)[1].strip()
            elif len(text.split(None, 1)) > 1:
                include = text.split(None, 1)[1].strip()
            else:
                include = ""
            if not include:
                skipped.append((str(path), number, raw.strip()))
                continue
            include_path = (path.parent / include).resolve()
            sub_declared, sub_skipped = read_requirements(include_path, seen)
            for key, value in sub_declared.items():
                declared.setdefault(key, value)
            skipped.extend(sub_skipped)
            continue
        name, reason = parse_requirement_name(raw)
        if name is None:
            if reason:
                skipped.append((str(path), number, raw.strip()))
            continue
        declared.setdefault(normalize(name), name)
    return declared, skipped


def check(project, requirements_path, excludes, ignore):
    """Run the check. Returns (report_lines, exit_code)."""
    imports, unreadable = collect_imports(project, excludes)
    if unreadable:
        lines = []
        for path, error in unreadable:
            lines.append("ERROR: %s: could not parse (%s)" % (path, error))
        lines.append(
            "Cannot check drift: %d Python file(s) could not be read."
            % len(unreadable)
        )
        return lines, 2
    try:
        declared, skipped = read_requirements(requirements_path)
    except FileNotFoundError:
        return ["ERROR: requirements file not found: %s" % requirements_path], 2
    except (OSError, UnicodeDecodeError) as exc:
        return ["ERROR: could not read %s (%s)" % (requirements_path, exc)], 2
    except ValueError as exc:
        return ["ERROR: %s" % exc], 2

    ignored = {normalize(name) for name in ignore}
    missing = []
    for import_name in sorted(imports):
        package = package_for_import(import_name)
        if normalize(package) in ignored:
            continue
        if normalize(package) not in declared:
            shown, lineno = imports[import_name]
            missing.append((package, import_name, shown, lineno))
    unused = []
    imported_packages = {
        normalize(package_for_import(name)) for name in imports
    }
    for key in sorted(declared):
        if key in ignored or key in imported_packages:
            continue
        unused.append(declared[key])

    lines = []
    for package, import_name, shown, lineno in missing:
        lines.append(
            "MISSING %s (imported as %s in %s:%d)"
            % (package, import_name, shown, lineno)
        )
    for package in unused:
        lines.append(
            "UNUSED  %s (declared in %s, never imported)"
            % (package, requirements_path.name)
        )
    for path, number, text in skipped:
        lines.append("SKIPPED %s:%d: %s" % (path, number, text))
    lines.append(
        "%d third-party import(s), %d declared, %d missing, %d unused"
        % (len(imports), len(declared), len(missing), len(unused))
    )
    if missing or unused:
        lines.append("DRIFT — imports do not match %s" % requirements_path.name)
        return lines, 1
    lines.append("OK — imports match %s" % requirements_path.name)
    return lines, 0


def run(argv=None, stdout=None, stderr=None):
    """Run the CLI. Returns the exit code. Testable without a process."""
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    try:
        with contextlib.redirect_stderr(err):
            args = parse_args(argv if argv is not None else sys.argv[1:])
    except SystemExit as exc:
        # argparse exits 2 on a bad flag; run() returns codes, never raises.
        return exc.code if isinstance(exc.code, int) else 2
    project = Path(args.project)
    if not project.is_dir():
        print("ERROR: project folder not found: %s" % project, file=err)
        return 2
    requirements_path = (
        Path(args.requirements)
        if args.requirements
        else project / "requirements.txt"
    )
    excludes = set(DEFAULT_EXCLUDES)
    excludes.update(split_list(args.exclude))
    ignore = split_list(args.ignore)
    lines, code = check(project, requirements_path, excludes, ignore)
    for line in lines:
        print(line, file=out)
    return code


def main():
    """Entry point for the command line."""
    sys.exit(run())


if __name__ == "__main__":
    main()
