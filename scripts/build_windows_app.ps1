$ErrorActionPreference = "Stop"

Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

# Lire GUI_VERSION depuis zotify/gui.py
$guiPy = Join-Path $repoRoot "zotify\gui.py"
$version = "unknown"
if (Test-Path $guiPy) {
    $m = [regex]::Match((Get-Content $guiPy -Raw), 'GUI_VERSION:\s*Final\[str\]\s*=\s*"([^"]+)"')
    if ($m.Success) { $version = $m.Groups[1].Value }
}

Write-Host "==> Build Zotify GUI v$version"

python -m pip install --upgrade pip | Out-Null
python -m pip install pyinstaller | Out-Null
# pip install -e . omis : setuptools echoue avec browser_extension/ au meme niveau que zotify/

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
  "--add-data", "logo;logo",
  "--hidden-import", "customtkinter",
  "--hidden-import", "PIL",
  "--hidden-import", "PIL._tkinter_finder",
  "--hidden-import", "mutagen",
  "--hidden-import", "music_tag",
  "--hidden-import", "http.server",
  "--hidden-import", "zotify.gui_server"
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

# Dossier de release versionne : dist/Zotify-1.2.1/
$releaseDir = Join-Path $repoRoot "dist\Zotify-$version"
if (Test-Path $releaseDir) {
    Remove-Item $releaseDir -Recurse -Force
}
New-Item -ItemType Directory -Path $releaseDir | Out-Null

Copy-Item $distExe (Join-Path $releaseDir "Zotify.exe")
Copy-Item (Join-Path $repoRoot "README.md") $releaseDir
if (Test-Path (Join-Path $repoRoot "CHANGELOG.md")) {
    Copy-Item (Join-Path $repoRoot "CHANGELOG.md") $releaseDir
}
Copy-Item (Join-Path $repoRoot "browser_extension") (Join-Path $releaseDir "browser_extension") -Recurse
Copy-Item (Join-Path $repoRoot "spicetify_extension") (Join-Path $releaseDir "spicetify_extension") -Recurse

@"
Zotify GUI $version
Build: $(Get-Date -Format "yyyy-MM-dd HH:mm")
"@ | Set-Content (Join-Path $releaseDir "VERSION.txt") -Encoding UTF8

$startMenuDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$shortcutPath = Join-Path $startMenuDir "Zotify.lnk"

$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($shortcutPath)
$shortcut.TargetPath = (Join-Path $releaseDir "Zotify.exe")
$shortcut.Arguments = ""
$shortcut.WorkingDirectory = $releaseDir
if (Test-Path $iconPath) {
    $shortcut.IconLocation = $iconPath
} else {
    $shortcut.IconLocation = "$($shortcut.TargetPath),0"
}
$shortcut.Description = "Zotify GUI $version"
$shortcut.Save()

Write-Host ""
Write-Host "Build termine (v$version):"
Write-Host " - EXE: $(Join-Path $releaseDir 'Zotify.exe')"
Write-Host " - Release: $releaseDir"
Write-Host " - Raccourci menu Demarrer: $shortcutPath"
Write-Host ""
