# Contributing

BenchForge is an experimental systems-benchmarking project. Contributions
should preserve honest reporting of measurement limits and avoid claiming
cross-machine results without evidence.

## Development setup

Use Python 3.12 or newer, CMake 3.20 or newer, and a C++20 compiler. From the
repository root:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
cmake -S native -B build/native -DCMAKE_BUILD_TYPE=Release
cmake --build build/native --config Release --parallel
ctest --test-dir build/native -C Release --output-on-failure
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
```

For Linux, activate `.venv` and use `python` in place of the Windows
interpreter path. See the README for OS-specific prerequisites.

## Change expectations

- Add focused tests for behavior changes and regressions.
- Keep network downloads out of routine unit tests; use small local fixtures.
- Keep raw datasets, databases, benchmark output, virtual environments, and
  build products out of commits.
- Validate the relevant native build and Python tests after changing workloads,
  persistence, or statistical calculations.
- Document unavailable platform or runtime checks rather than implying they
  passed.
- Never include credentials, private local paths, or unrelated user files.

Open a pull request with a concise description, verification commands and
results, and any known limitations. Do not publish datasets or derived files
without confirming their applicable terms.
