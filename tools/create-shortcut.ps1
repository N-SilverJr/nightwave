# Creates Nightwave shortcuts on the Desktop and in the Start menu.
$app = Split-Path -Parent $PSScriptRoot
$q = [char]34
$ws = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
    $path = Join-Path $dir 'Nightwave.lnk'
    $l = $ws.CreateShortcut($path)
    $l.TargetPath = Join-Path $app '.venv\Scripts\pythonw.exe'
    $l.Arguments = $q + (Join-Path $app 'main.py') + $q
    $l.WorkingDirectory = $app
    $l.IconLocation = (Join-Path $app 'assets\icon.ico') + ',0'
    $l.Description = 'Nightwave media widget'
    $l.Save()
    Write-Host "Created $path"
}
