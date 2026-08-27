# Crea un collegamento a MaraMouse sul Desktop, con l'icona personalizzata.
# Uso: tasto destro sul file -> "Esegui con PowerShell"
#      oppure da terminale: powershell -ExecutionPolicy Bypass -File Crea-Collegamento.ps1

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$desktop = [Environment]::GetFolderPath('Desktop')
$linkPath = Join-Path $desktop 'MaraMouse.lnk'

$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($linkPath)
$sc.TargetPath = Join-Path $root 'MaraMouse.bat'
$sc.WorkingDirectory = $root
$sc.IconLocation = Join-Path $root 'assets\MaraMouseLogo.ico'
$sc.Description = 'MaraMouse - controllo del PC a gesti della mano'
$sc.Save()

Write-Host "Collegamento creato sul desktop: $linkPath"
