$ErrorActionPreference = 'Continue'
$root = (Get-Location).Path
$python = Join-Path $root '.venv\Scripts\python.exe'
$failures = 0
function Section($name) { Write-Host "`n========== $name ==========" -ForegroundColor Cyan }
function Result($name, $code) { if ($code -eq 0) { Write-Host "PASS: $name" -ForegroundColor Green } else { Write-Host "FAIL: $name (exit $code)" -ForegroundColor Red; $script:failures++ } }
Section 'ENVIRONMENT'
Write-Host "Root: $root"
& $python --version
Write-Host "Python exit: $LASTEXITCODE"
Section 'GIT STATUS'
git status --short
Write-Host "Git status exit: $LASTEXITCODE"
Section 'DIFF SUMMARY'
git diff --stat
git diff --cached --stat
Section 'WHITESPACE CHECK'
git diff --check
Result 'git diff --check' $LASTEXITCODE
Section 'PYTEST SUITE'
& $python -m pytest -q
Result 'pytest' $LASTEXITCODE
Section 'RUFF LINT'
& $python -m ruff check src tests scripts
Result 'Ruff' $LASTEXITCODE
Section 'PROJECT CONFIGURATION'
foreach ($file in @('pyproject.toml','CMakeLists.txt','native/CMakeLists.txt','src/benchforge/cli.py','src/benchforge/ab.py','src/benchforge/stats.py','src/benchforge/storage.py','scripts/analyze_calibration.py','scripts/analyze_order_effect.py','scripts/analyze_sequence_drift.py')) {
    if (Test-Path $file) { Write-Host "PRESENT  $file" } else { Write-Host "MISSING  $file" }
}
Section 'PLACEHOLDER / TODO SCAN'
$scanFiles = Get-ChildItem '.\src\benchforge' -Recurse -File -Filter '*.py' -ErrorAction SilentlyContinue
$matches = $scanFiles | Select-String -Pattern 'TODO|FIXME|NotImplementedError|placeholder|implement me' -CaseSensitive:$false
if ($matches) { $matches | ForEach-Object { Write-Host ("{0}:{1}: {2}" -f $_.Path, $_.LineNumber, $_.Line.Trim()) } } else { Write-Host 'No common placeholder markers found in src/benchforge.' }
Section 'RECENT COMMITS'
git log -5 --oneline
Section 'SUMMARY'
if ($failures -eq 0) { Write-Host 'All executed verification checks passed.' -ForegroundColor Green } else { Write-Host "$failures verification check(s) failed. Review output above." -ForegroundColor Yellow }
Write-Host 'No source code or experiment database was intentionally modified by this verification run.'
exit ([Math]::Min($failures, 10))
