[CmdletBinding()]
param(
	[string]$ShortcutName = "OpenJob"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Launcher = Join-Path $PSScriptRoot "start_openjob.ps1"
$Desktop = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "$ShortcutName.lnk"
$PowerShell = Join-Path $PSHOME "powershell.exe"
$IconPath = Join-Path $RepoRoot "assets\openjob.ico"

$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $PowerShell
$Shortcut.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Launcher`""
$Shortcut.WorkingDirectory = $RepoRoot
$Shortcut.Description = "Start the OpenJob Chrome profile and local workbench"
if (Test-Path -LiteralPath $IconPath) {
	$Shortcut.IconLocation = "$IconPath,0"
}
$Shortcut.Save()

Write-Host "Created desktop shortcut: $ShortcutPath"
