#requires -version 5.1
<#
.SYNOPSIS
    Build the VideoFeed Windows desktop bundle.

.DESCRIPTION
    Runs the full release pipeline:
      1. Builds the React SPA (npm run build) into frontend_static/.
      2. Cleans previous PyInstaller artefacts (build/, dist/VideoFeed/).
      3. Runs `pyinstaller videofeed.spec`.

    The script is idempotent - re-runs only rebuild whatever changed.
    Output: dist/VideoFeed/VideoFeed.exe (plus supporting DLLs + frontend_static/).

.PARAMETER SkipFrontend
    Skip the npm build step. Useful when iterating on backend bundling and
    the SPA artefacts are already up to date.

.PARAMETER VenvPython
    Path to the python.exe inside the venv that has pyinstaller installed.
    Defaults to .venv\Scripts\python.exe at the repo root.

.EXAMPLE
    .\scripts\build_bundle.ps1
    .\scripts\build_bundle.ps1 -SkipFrontend
#>
[CmdletBinding()]
param(
    [switch]$SkipFrontend,
    [string]$VenvPython
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (-not $VenvPython) {
    $VenvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
}
if (-not (Test-Path $VenvPython)) {
    throw "Python venv not found at $VenvPython. Pass -VenvPython or run: python -m venv .venv && .venv\Scripts\pip install -r backend\requirements-dev.txt"
}

# 1. Build the SPA -----------------------------------------------------------
if (-not $SkipFrontend) {
    Write-Host ">> Building frontend..." -ForegroundColor Cyan
    Push-Location frontend
    try {
        if (-not (Test-Path node_modules)) {
            npm install
            if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
        }
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
    } finally {
        Pop-Location
    }
} else {
    Write-Host ">> Skipping frontend build (-SkipFrontend)" -ForegroundColor Yellow
}

if (-not (Test-Path "frontend_static\index.html")) {
    throw "frontend_static\index.html missing - frontend build did not produce expected output."
}

# 2. Clean previous artefacts -----------------------------------------------
Write-Host ">> Cleaning previous bundle..." -ForegroundColor Cyan
foreach ($dir in @("build", "dist\VideoFeed")) {
    if (Test-Path $dir) {
        Remove-Item -Recurse -Force $dir
    }
}

# 3. Run PyInstaller --------------------------------------------------------
Write-Host ">> Running PyInstaller..." -ForegroundColor Cyan
# Merge stderr into stdout (2>&1) so PowerShell does not treat PyInstaller's
# normal INFO log lines (which it writes to stderr) as native-command errors
# under $ErrorActionPreference="Stop". Exit code stays authoritative.
& $VenvPython -m PyInstaller --noconfirm videofeed.spec 2>&1 | ForEach-Object { Write-Host $_ }
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed (exit $LASTEXITCODE)" }

$exePath = Join-Path $repoRoot "dist\VideoFeed\VideoFeed.exe"
if (-not (Test-Path $exePath)) {
    throw "Expected $exePath to exist after build, but it does not."
}

$sizeMB = [math]::Round((Get-Item $exePath).Length / 1MB, 1)
Write-Host ">> Done. Built $exePath ($sizeMB MB exe; full bundle in dist\VideoFeed\)" -ForegroundColor Green
Write-Host ">> Next: run scripts\install_shortcut.ps1 to add a Desktop shortcut." -ForegroundColor Green
