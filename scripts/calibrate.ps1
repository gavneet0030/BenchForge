$ErrorActionPreference = "Stop"
$Python = Join-Path (Get-Location) ".venv\Scripts\python.exe"
$Database = "data/benchforge-calibration-20.sqlite"
$Summary = "data/processed/calibration-summary.csv"

$Delays = @(0, 250000, 500000, 1000000)
$TrialsPerDelay = 20
$Results = [System.Collections.Generic.List[object]]::new()

foreach ($Delay in $Delays) {
    for ($Trial = 1; $Trial -le $TrialsPerDelay; $Trial++) {
        $Seed = 31000 + ($Delay / 1000) + $Trial
        $Output = "data/raw/calibration20-delay-$Delay-trial-$Trial.jsonl"

        Write-Host ""
        Write-Host "=== Delay ${Delay} ns | Trial $Trial/$TrialsPerDelay ===" -ForegroundColor Cyan

        $AbText = (& $Python -m benchforge.cli ab `
            --runs 30 `
            --warmup 3 `
            --iterations 100000 `
            --baseline-delay-ns 0 `
            --candidate-delay-ns $Delay `
            --seed $Seed `
            --output $Output `
            --database $Database 2>&1 | Out-String)

        $AbExit = $LASTEXITCODE
        Write-Host $AbText

        if ($AbExit -ne 0) {
            throw "A/B run failed for delay=$Delay trial=$Trial. See output above."
        }

        $RunMatch = [regex]::Match($AbText, 'Run ID:\s*([0-9a-fA-F-]{36})')
        if (-not $RunMatch.Success) {
            throw "Could not extract the Run ID for delay=$Delay trial=$Trial."
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

        $CompareExit = $LASTEXITCODE
        Write-Host $CompareText

        if ($CompareExit -ne 0) {
            throw "Comparison failed for run $RunId. See output above."
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
            throw "Could not parse comparison output for run $RunId."
        }

        $EffectRatio = [double]::Parse(
            $EffectMatch.Groups[1].Value,
            [Globalization.CultureInfo]::InvariantCulture
        )
        $CiLow = [double]::Parse(
            $CiMatch.Groups[1].Value,
            [Globalization.CultureInfo]::InvariantCulture
        )
        $CiHigh = [double]::Parse(
            $CiMatch.Groups[2].Value,
            [Globalization.CultureInfo]::InvariantCulture
        )
        $PValue = [double]::Parse(
            $PMatch.Groups[1].Value,
            [Globalization.CultureInfo]::InvariantCulture
        )

        $Results.Add([pscustomobject]@{
            delay_ns = $Delay
            trial = $Trial
            runs = 30
            iterations = 100000
            seed = $Seed
            run_id = $RunId
            effect_ratio = $EffectRatio
            effect_percent = [math]::Round(($EffectRatio - 1) * 100, 4)
            ci_low_ratio = $CiLow
            ci_high_ratio = $CiHigh
            ci_low_percent = [math]::Round(($CiLow - 1) * 100, 4)
            ci_high_percent = [math]::Round(($CiHigh - 1) * 100, 4)
            wilcoxon_p = $PValue
            decision = $DecisionMatch.Groups[1].Value
        })

        $Results | Export-Csv -Path $Summary -NoTypeInformation
        Write-Host "Recorded trial: delay=$Delay ns, decision=$($DecisionMatch.Groups[1].Value)" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "=== CALIBRATION SUMMARY ===" -ForegroundColor Cyan
$Results |
    Sort-Object delay_ns, trial |
    Format-Table delay_ns, trial, effect_percent, ci_low_percent, ci_high_percent, wilcoxon_p, decision -AutoSize

Write-Host "CSV saved to: $Summary" -ForegroundColor Green
