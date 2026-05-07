#requires -version 5.1
<#
.SYNOPSIS
    Create a Desktop shortcut to the built VideoFeed.exe.

.DESCRIPTION
    Creates VideoFeed.lnk on the user's Desktop pointing at
    dist\VideoFeed\VideoFeed.exe with the project's favicon as its icon.
    Idempotent — re-running overwrites the existing shortcut.

.PARAMETER ExePath
    Override the path to the built .exe. Defaults to
    <repo>\dist\VideoFeed\VideoFeed.exe.

.PARAMETER ShortcutPath
    Override where the shortcut goes. Defaults to <Desktop>\VideoFeed.lnk.

.EXAMPLE
    .\scripts\install_shortcut.ps1
#>
[CmdletBinding()]
param(
    [string]$ExePath,
    [string]$ShortcutPath
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot

if (-not $ExePath) {
    $ExePath = Join-Path $repoRoot "dist\VideoFeed\VideoFeed.exe"
}
if (-not (Test-Path $ExePath)) {
    throw "VideoFeed.exe not found at $ExePath. Run scripts\build_bundle.ps1 first."
}

if (-not $ShortcutPath) {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $ShortcutPath = Join-Path $desktop "VideoFeed.lnk"
}

$icon = Join-Path $repoRoot "frontend\public\assets\logo\favicon.ico"

$wshell = New-Object -ComObject WScript.Shell
$lnk = $wshell.CreateShortcut($ShortcutPath)
$lnk.TargetPath = $ExePath
$lnk.WorkingDirectory = Split-Path -Parent $ExePath
$lnk.Description = "VideoFeed — self-hosted home video streaming"
if (Test-Path $icon) {
    $lnk.IconLocation = "$icon,0"
}
$lnk.Save()

Write-Host ">> Shortcut created: $ShortcutPath" -ForegroundColor Green
Write-Host ">> Target: $ExePath" -ForegroundColor Green
