# Deployment runbook

## Supported mode: local development

The complete application is a local Python CLI and C++ benchmark suite. It
stores benchmark and ETL metadata in SQLite and reads processed dataset
summaries from local files. GitHub Pages cannot host the Python/SQLite backend.

1. Install Python 3.12+, a C++20 compiler, and CMake 3.20+.
2. Create and activate a virtual environment.
3. Install `python -m pip install -e ".[dev]"`.
4. Build and test the native programs:

   ```powershell
   cmake -S native -B build/native -DCMAKE_BUILD_TYPE=Release
   cmake --build build/native --config Release --parallel
   ctest --test-dir build/native -C Release --output-on-failure
   ```

5. Start the dashboard: `benchforge dashboard --database data/benchforge.sqlite`.
   The local PDF workflow stores generated-ID source files and extracted text
   under `data/documents/` by default. Adjust the upload cap using
   `--max-pdf-bytes` (default 10 MiB, maximum 50 MiB) and the storage location
   using `--documents-dir`.
6. Browse `http://127.0.0.1:8765`; stop with Ctrl+C.

The dashboard is unauthenticated. It defaults to loopback and must not be made
public using `--allow-remote` without an independently designed security
boundary. Docker Compose also binds the host port to loopback by default.

## Docker Compose

Docker was not verified in the latest run. When Docker Desktop or another
compatible local daemon is available:

```powershell
docker compose config
docker compose up --build
```

Confirm `/healthz`, check the container logs, and stop with Ctrl+C. Inspect
named-volume contents and retention before using production or sensitive data.
Do not pass credentials or mount personal documents into a public container.

## GitHub Pages

No public site has been deployed. A static demo is a separate deliverable and
must use intentionally bundled, license-cleared public sample data with a clear
static/offline label. It cannot provide local database history, execute
pipelines, process uploaded PDFs, or expose the Python dashboard API.

Before enabling Pages:

1. Obtain maintainer approval to publish a static demo.
2. Verify repository Pages settings and whether publishing from GitHub Actions
   is available to this repository.
3. Review the demo data's license, attribution, and public visibility.
4. Use least-privilege workflow permissions (`pages: write`,
   `id-token: write` only for the deploy job); avoid secrets.
5. Deploy through GitHub Actions only after approval, then verify the exact
   published URL and record the workflow run.

No external host, paid service, cloud resource, or public deployment was
created for this project.

## Release controls

No commit, push, package upload, release, visibility change, or deployment has
been performed. Use [RELEASE_CHECKLIST.md](./RELEASE_CHECKLIST.md) and
[FINAL_VERIFICATION.md](./FINAL_VERIFICATION.md) before requesting publication
approval.
