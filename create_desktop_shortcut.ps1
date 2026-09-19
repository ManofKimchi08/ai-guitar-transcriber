$wsh = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktop "AI Band Transcriber.lnk"

$shortcut = $wsh.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "C:\Users\dlwjd\.gemini\antigravity\scratch\ai_band_transcriber\AI_Band_Transcriber.exe"
$shortcut.WorkingDirectory = "C:\Users\dlwjd\.gemini\antigravity\scratch\ai_band_transcriber"
$shortcut.Description = "AI Band Transcriber (TAB & Sheet Music Generator)"
$shortcut.Save()

Write-Host "Desktop shortcut created successfully at: $shortcutPath"
