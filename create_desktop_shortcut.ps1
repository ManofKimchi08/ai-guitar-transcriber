$wsh = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktop "AI Band Transcriber.lnk"

$shortcut = $wsh.CreateShortcut($shortcutPath)
$shortcut.TargetPath = Join-Path $PSScriptRoot "AI_Band_Transcriber.exe"
$shortcut.WorkingDirectory = $PSScriptRoot
$shortcut.Description = "AI Band Transcriber (TAB & Sheet Music Generator)"
$shortcut.Save()

Write-Host "Desktop shortcut created successfully at: $shortcutPath"
