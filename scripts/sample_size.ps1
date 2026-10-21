$ErrorActionPreference = "Stop"
$Python = Join-Path (Get-Location) ".venv\Scripts\python.exe"

$Database = "data/benchforge-sample-size.sqlite"
$Summary = "data/processed/sample-size-control-summary.csv"
$OutputDir = "data/raw/sample-size-control"

New-Item -ItemType Directory -Force -Path "data/processed" | Out-Null
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

if (-not (Test-Path $Python)) {
    throw "Project virtual-environment Python not found: $Python"
}

$PairCounts = @(30, 50, 100)
$TrialsPerCount = 20
$Results = [System.Collections.Generic.List[object]]::new()

foreach ($Pairs in $PairCounts) {
    for ($Trial = 1; $Trial -le $TrialsPerCount; $Trial++) {
        $Seed = 47000 + ($Pairs * 100) + $Trial
        $Output = "$OutputDir/control-pairs-$Pairs-trial-$Trial.jsonl"

        Write-Host ""
        Write-Host "=== CONTROL | Pairs $Pairs | Trial $Trial/$TrialsPerCount ===" -ForegroundColor Cyan

        $AbText = (& $Python -m benchforge.cli ab `
            --runs $Pairs `
            --warmup 3 `
            --iterations 100000 `
            --baseline-delay-ns 0 `
            --candidate-delay-ns 0 `
            --seed $Seed `
            --output $Output `
            --database $Database 2>&1 | Out-String)

        if ($LASTEXITCODE -ne 0) {
            Write-Host $AbText
            throw "A/B run failed: pairs=$Pairs trial=$Trial"
        }

        $RunMatch = [regex]::Match($AbText, 'Run ID:\s*([0-9a-fA-F-]{36})')
        if (-not $RunMatch.Success) {
            Write-Host $AbText
            throw "Could not extract Run ID for pairs=$Pairs trial=$Trial"
        }

        $RunId = $RunMatch.Groups[1].Value

        $CompareText = (& $Python -m benchforge.cli compare `
            --run-id $RunId `
            --database $Database `
            --threshold 0.03 `
            --confidence 0.95 `
            --n-resamples 5000 `
            --alpha 0.05 `
            --seed 2026 2>&1 | Out-String)

        if ($LASTEXITCODE -ne 0) {
            Write-Host $CompareText
            throw "Comparison failed: run=$RunId"
        }

        $EffectMatch = [regex]::Match(
            $CompareText,
            'median paired candidate/baseline ratio\s*=\s*([0-9.eE+-]+)'
        )
        $CiMatch = [regex]::Match(
            $CompareText,
            'paired bootstrap CI:\s*\[\s*([0-9.eE+-]+)\s*,\s*([0-9.eE+-]+)\s*\]'
        )
        $PMatch = [regex]::Match(
            $CompareText,
            'Wilcoxon signed-rank p-value:\s*([0-9.eE+-]+)'
        )
        $DecisionMatch = [regex]::Match(
            $CompareText,
            'Decision:\s*(REGRESSION|PASS|INCONCLUSIVE)'
        )

        if (-not ($EffectMatch.Success -and $CiMatch.Success -and
                  $PMatch.Success -and $DecisionMatch.Success)) {
            Write-Host $CompareText
            throw "Could not parse comparison for run=$RunId"
        }

        $Culture = [Globalization.CultureInfo]::InvariantCulture
        $Ratio = [double]::Parse($EffectMatch.Groups[1].Value, $Culture)
        $CiLow = [double]::Parse($CiMatch.Groups[1].Value, $Culture)
        $CiHigh = [double]::Parse($CiMatch.Groups[2].Value, $Culture)
        $PValue = [double]::Parse($PMatch.Groups[1].Value, $Culture)
        $Decision = $DecisionMatch.Groups[1].Value

        $Results.Add([pscustomobject]@{
            pairs = $Pairs
            trial = $Trial
            iterations = 100000
            seed = $Seed
            run_id = $RunId
            effect_ratio = $Ratio
            effect_percent = [math]::Round(($Ratio - 1) * 100, 4)
            ci_low_percent = [math]::Round(($CiLow - 1) * 100, 4)
            ci_high_percent = [math]::Round(($CiHigh - 1) * 100, 4)
            wilcoxon_p = $PValue
            decision = $Decision
        })

        $Results | Export-Csv -Path $Summary -NoTypeInformation
        Write-Host "Pairs=$Pairs | Decision=$Decision | Effect=$([math]::Round(($Ratio - 1) * 100, 2))%" -ForegroundColor Green
    }
}

Write-Host "`n=== SAMPLE-SIZE CONTROL SUMMARY ===" -ForegroundColor Cyan

$Results |
    Group-Object pairs |
    ForEach-Object {
        $Group = @($_.Group)
        $Total = $Group.Count
        $Regression = @($Group | Where-Object decision -eq "REGRESSION").Count
        $Pass = @($Group | Where-Object decision -eq "PASS").Count
        $Inconclusive = @($Group | Where-Object decision -eq "INCONCLUSIVE").Count

        [pscustomobject]@{
            pairs = $Group[0].pairs
            trials = $Total
            regressions = $Regression
            passes = $Pass
            inconclusive = $Inconclusive
            regression_fraction = [math]::Round($Regression / $Total, 3)
            pass_fraction = [math]::Round($Pass / $Total, 3)
            inconclusive_fraction = [math]::Round($Inconclusive / $Total, 3)
        }
    } |
    Format-Table -AutoSize

Write-Host "Trial-level CSV: $Summary" -ForegroundColor Green
Write-Host "Dedicated database: $Database" -ForegroundColor Green
