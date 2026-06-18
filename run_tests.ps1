param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$TestArgs
)

$ErrorActionPreference = "Stop"

function Resolve-ProjectPython {
    param([string]$ProjectRoot)

    $candidates = @(
        (Join-Path $ProjectRoot ".venv314\Scripts\python.exe"),
        (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
        (Join-Path $ProjectRoot "venv\Scripts\python.exe")
    )

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    throw "Project virtualenv python.exe not found. Expected .venv314, .venv, or venv."
}

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = Resolve-ProjectPython -ProjectRoot $projectRoot

if (-not $TestArgs -or $TestArgs.Count -eq 0) {
    $TestArgs = @("discover", "-s", "test", "-p", "*_test.py")
}

Write-Output "Using project python: $pythonExe"
if ($TestArgs[0] -eq "discover") {
    & $pythonExe -m unittest @TestArgs
    exit $LASTEXITCODE
}

$normalizedPatterns = @()
foreach ($arg in $TestArgs) {
    $candidate = $arg.Trim()
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        continue
    }

    if ($candidate.StartsWith("test.")) {
        $candidate = $candidate.Substring(5)
    }

    $candidate = [System.IO.Path]::GetFileName($candidate)
    if (-not $candidate.EndsWith(".py")) {
        $candidate = "$candidate.py"
    }

    $normalizedPatterns += $candidate
}

if ($normalizedPatterns.Count -eq 0) {
    throw "No valid test targets provided."
}

$failed = $false
foreach ($pattern in $normalizedPatterns) {
    Write-Output "Running test pattern: $pattern"
    & $pythonExe -m unittest discover -s test -p $pattern
    if ($LASTEXITCODE -ne 0) {
        $failed = $true
    }
}

if ($failed) {
    exit 1
}
