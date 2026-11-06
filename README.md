# import-sync-cli

Check Python imports against requirements.txt, fail CI on drift.

[![CI](https://github.com/haiderstudio/import-sync-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/haiderstudio/import-sync-cli/actions/workflows/ci.yml) [![Licence: MIT](https://img.shields.io/badge/Licence-MIT-yellow.svg)](LICENSE)

## What it does

`import_sync` walks a Python project, reads the third-party imports,
and compares them with `requirements.txt`. It reports two kinds of
drift:

- `MISSING` — imported in code, but not declared in requirements.txt.
- `UNUSED` — declared in requirements.txt, but never imported.

It is report-only: it never writes or changes any file. It exits 1
when drift is found, so a CI step can fail the build until the list
and the code agree again. It exits 0 when they match.

- It reads imports with Python's own `ast` module, so it sees the
  real imports — including ones inside functions — and it knows the
  file and line of each one.
- It skips standard library imports (`os`, `sys`, `json`, ...) using
  `sys.stdlib_module_names`, so they never count as third-party.
- It skips first-party imports: a name that matches a top-level
  `.py` file or a top-level package folder in the project is the
  project's own code, not a dependency.
- It follows `-r` includes in requirements files, and it reads
  pinned versions (`requests==2.31.0`), extras (`requests[security]`),
  environment markers (`; python_version < "3.9"`), and direct
  references (`name @ https://...`).
- Package names are compared the PEP 503 way: lowercased, with runs
  of `-`, `_`, and `.` treated as one `-`. A small fixed table maps
  import names that differ from their package names (`PIL` ->
  `Pillow`, `sklearn` -> `scikit-learn`, `yaml` -> `PyYAML`, and a
  few more). Anything else must match by its normalized name, or be
  named with `--ignore`.
- `--ignore` skips a package on both sides: an ignored import is not
  reported missing, and an ignored declared package is not reported
  unused.
- It runs fully offline: it sends nothing anywhere and needs no API
  key.

## What it does NOT do

- It does not write `requirements.txt`. It reports drift; fixing the
  list is your call. Tools like pipreqs generate the file instead —
  that is a different job.
- It does not check whether a declared package is installed, or
  which version is installed. It compares names in code with names
  in the file, nothing more.
- It does not check `pyproject.toml` dependencies. It reads
  `requirements.txt` only (or the file you name with
  `--requirements`).
- It does not know about namespace packages: a dotted import uses
  its top-level name (`import google.cloud.storage` counts as
  `google`), so a namespace package needs `--ignore` or a declared
  package whose normalized name matches that top level.
- It does not read imports hidden behind a syntax error. A Python
  file it cannot parse is reported as an error and the check stops
  with exit code 2 — a drift report is only worth anything when
  every file was read.
- It does not count optional imports specially: an import inside
  `try`/`except ImportError` still counts as an import. Use
  `--ignore` for an optional dependency you do not want to declare.

## Who it is for

Anyone with a Python project whose `requirements.txt` has drifted
from the code: a developer adding a CI step that fails the build
when a dependency is missing or dead, or someone cleaning up a
requirements file full of packages nobody imports any more. If the
list and the code agree, you get one quiet line; if they do not, the
report names the exact package, the exact file and line, and which
side is wrong.

## How this differs from similar tools

The big name in this space is pipreqs (bndr/pipreqs, 7.5k stars),
but pipreqs *generates* a requirements file from imports — writing
is its job, and it needs a separate install. This tool does the
opposite: it checks the file you already have and never writes it.

The closest check-only tool is kriskimmerle/reqsync (0 stars, one
commit, no tests, no CI). It does the same core job — phantom and
missing dependencies — but it scores and grades the project
(0–100, with a pass threshold) and also reads `pyproject.toml`.
This tool is a narrower slice: `requirements.txt` only, a plain
drift list with no score and no grade, and it fails CI on *any*
drift instead of letting a score above a threshold pass. It is also
a complete repo — tests, CI, examples, issue templates — where
reqsync is a single untested file.

FawltyDeps checks the packages installed in your environment
against your code, which needs an installed environment and
third-party packages of its own. This tool reads only the code and
the file, with the standard library only, and works on a fresh
checkout with nothing installed.

## Install

Python standard library only — no packages to download. Clone the
repo and run it in place:

```sh
git clone https://github.com/haiderstudio/import-sync-cli.git
cd import-sync-cli
```

Python 3.10 or newer.

## Usage

Check a project folder (it looks for `requirements.txt` inside it):

```sh
python3 src/import_sync.py examples/sample-project
```

Real output:

```text
MISSING PyYAML (imported as yaml in utils.py:3)
UNUSED  boto3 (declared in requirements.txt, never imported)
5 third-party import(s), 5 declared, 1 missing, 1 unused
DRIFT — imports do not match requirements.txt
```

The command exits 1, because the imports and the requirements do
not match. The same output is saved in
`examples/sample-output.txt`.

Check a project with no drift:

```sh
python3 src/import_sync.py examples/clean-project
```

Real output:

```text
1 third-party import(s), 1 declared, 0 missing, 0 unused
OK — imports match requirements.txt
```

The command exits 0.

Check against a different requirements file:

```sh
python3 src/import_sync.py --requirements deps.txt myproject
```

Ignore a package on both sides:

```sh
python3 src/import_sync.py --ignore boto3 examples/sample-project
```

Real output:

```text
MISSING PyYAML (imported as yaml in utils.py:3)
5 third-party import(s), 5 declared, 1 missing, 0 unused
DRIFT — imports do not match requirements.txt
```

`boto3` is no longer reported as unused, but the missing `PyYAML`
still fails the check, so the exit code is still 1.

Exit codes: 0 means no drift, 1 means drift was found, 2 means a
usage or input problem (missing project folder, missing
requirements file, a Python file that cannot be parsed, a bad flag).

## Examples

### 1. Fail CI on a missing dependency

The sample project imports `yaml` in `utils.py`, but `PyYAML` is
not in its `requirements.txt`:

```sh
python3 src/import_sync.py examples/sample-project
echo $?
```

```text
1
```

The exit code is 1. Put the same command in CI and the build fails
until the missing package is declared (or the import is removed).
With a clean project the same command prints
`OK — imports match requirements.txt` and exits 0.

### 2. Find a package nobody imports any more

The sample project declares `boto3`, but no file imports it:

```sh
python3 src/import_sync.py examples/sample-project
```

```text
MISSING PyYAML (imported as yaml in utils.py:3)
UNUSED  boto3 (declared in requirements.txt, never imported)
5 third-party import(s), 5 declared, 1 missing, 1 unused
DRIFT — imports do not match requirements.txt
```

The `UNUSED` line names the dead package and the file that declares
it. Remove it from `requirements.txt` (or start using it) and the
line goes away.

### 3. A requirements file that includes another one

```text
# base.txt
requests

# requirements.txt
-r base.txt
flask
```

```sh
python3 src/import_sync.py --requirements requirements.txt myproject
```

The `-r base.txt` line is followed, relative to the requirements
file, so `requests` counts as declared. An include cycle is an
error, not a silent loop.

## How it is tested

```sh
python3 tests/test_import_sync.py
```

39 tests, standard library `unittest` only — no third-party
packages. They cover: name normalization and the import-to-package
table, requirements parsing (pins, extras, markers, direct
references, comments, option lines, `-r` includes, include cycles,
duplicates), import collection (import forms, standard library,
first-party code, imports inside functions, dotted imports,
excluded folders, unparsable files), the drift check (missing and
unused, clean projects, the mapping table, `--ignore`,
`--requirements`, the default project folder), the usage errors
(missing folder, missing requirements file, unparsable file, bad
flag), the bundled examples (the committed
`examples/sample-output.txt` is compared against a fresh run, and
the README's quoted outputs are checked against it), the example
script, and a check that the code and the tests import standard
library only.

The bundled example is a real run, not made-up output:

```sh
sh examples/run-example.sh
```

It checks the sample project (drift on purpose) and the clean
project (no drift). The committed `examples/sample-output.txt` is
the output of the sample-project check.

## Contributing

Small, focused improvements are welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

MIT — see [LICENSE](LICENSE).

## Original work

Built from scratch for this repo. The import reading uses Python's
standard `ast` module and the standard library name list; no code
was copied from any existing import checker. The idea of comparing
imports with requirements is an old one — pipreqs generates the
file, reqsync checks it with a score — and this tool's slice
(requirements.txt only, plain drift list, no score, fail on any
drift) is its own.
