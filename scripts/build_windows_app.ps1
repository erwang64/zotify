$ErrorActionPreference = "Stop"

Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

Write-Host "==> Build Zotify GUI executable"

python -m pip install --upgrade pip | Out-Null
python -m pip install pyinstaller | Out-Null

$entryScript = Join-Path $repoRoot "zotify\__main__.py"
$logoDir = Join-Path $repoRoot "logo"
$iconPath = Join-Path $repoRoot "logo\icon.ico"

if (-not (Test-Path $entryScript)) {
    throw "Entry script not found: $entryScript"
}

if (-not (Test-Path $logoDir)) {
    throw "Logo directory not found: $logoDir"
}

$pyInstallerArgs = @(
  "--noconfirm",
  "--clean",
  "--windowed",
  "--onefile",
  "--name", "Zotify",
  "--add-data", "logo;logo"
)

if (Test-Path $iconPath) {
    $pyInstallerArgs += @("--icon", $iconPath)
}

$pyInstallerArgs += $entryScript
python -m PyInstaller @pyInstallerArgs
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE. Ferme Zotify.exe puis relance le script."
}

$distExe = Join-Path $repoRoot "dist\Zotify.exe"
if (-not (Test-Path $distExe)) {
    throw "Build failed. Missing executable: $distExe"
}

$startMenuDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$shortcutPath = Join-Path $startMenuDir "Zotify.lnk"

$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $distExe
$shortcut.Arguments = "--gui"
$shortcut.WorkingDirectory = (Split-Path $distExe -Parent)
if (Test-Path $iconPath) {
    $shortcut.IconLocation = $iconPath
} else {
    $shortcut.IconLocation = "$distExe,0"
}
$shortcut.Description = "Zotify GUI"
$shortcut.Save()

Write-Host ""
Write-Host "Build terminé:"
Write-Host " - EXE: $distExe"
Write-Host " - Raccourci menu Démarrer: $shortcutPath"
Write-Host ""
Write-Host "Tu peux maintenant ouvrir Windows et chercher: Zotify"
