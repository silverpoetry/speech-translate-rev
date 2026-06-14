param(
    [string]$ShortcutPath = (Join-Path ([Environment]::GetFolderPath("Desktop")) "Speech Translate.lnk"),
    [string]$PythonwPath = "",
    [string]$ScriptPath = (Join-Path $PSScriptRoot "Run.pyw"),
    [string]$IconPath = (Join-Path $PSScriptRoot "speech_translate\assets\icon.ico"),
    [string]$AppUserModelId = "Dadangdut33.SpeechTranslate.WebviewUI"
)

$ErrorActionPreference = "Stop"

function Resolve-PythonwPath {
    param([string]$ProjectRoot)

    $candidates = @(
        (Join-Path $ProjectRoot ".venv314\Scripts\pythonw.exe"),
        (Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"),
        (Join-Path $ProjectRoot "venv\Scripts\pythonw.exe")
    )

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    $command = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    throw "Unable to locate pythonw.exe. Pass -PythonwPath explicitly."
}

if ([string]::IsNullOrWhiteSpace($PythonwPath)) {
    $PythonwPath = Resolve-PythonwPath -ProjectRoot $PSScriptRoot
} else {
    $PythonwPath = (Resolve-Path -LiteralPath $PythonwPath).Path
}

$ScriptPath = (Resolve-Path -LiteralPath $ScriptPath).Path
$IconPath = (Resolve-Path -LiteralPath $IconPath).Path
$ShortcutPath = [System.IO.Path]::GetFullPath($ShortcutPath)
$ShortcutDir = Split-Path -Parent $ShortcutPath
if ($ShortcutDir -and -not (Test-Path -LiteralPath $ShortcutDir)) {
    New-Item -ItemType Directory -Path $ShortcutDir | Out-Null
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($ShortcutPath)
$shortcut.TargetPath = $PythonwPath
$shortcut.Arguments = ('"{0}"' -f $ScriptPath)
$shortcut.WorkingDirectory = $PSScriptRoot
$shortcut.IconLocation = $IconPath
$shortcut.Description = "Speech Translate (pythonw launcher, AppID: $AppUserModelId)"
$shortcut.Save()

Write-Output "Created shortcut: $ShortcutPath"
Write-Output "Target: $PythonwPath"
Write-Output "AppUserModelID is set by speech_translate.__main__ at process startup: $AppUserModelId"
