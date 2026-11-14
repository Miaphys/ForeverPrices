# Builds dist\ForeverPrices.exe (single file, no console window) from tray.py + companion.py.
# Run from anywhere:  powershell -ExecutionPolicy Bypass -File build.ps1
# PyInstaller logs to stderr; with 'Stop', Windows PowerShell 5.1 would treat that as a failure.
$ErrorActionPreference = 'Continue'
Set-Location $PSScriptRoot
python -m PyInstaller --noconfirm --clean --onefile --noconsole `
    --name ForeverPrices --icon "$PSScriptRoot\icon.ico" `
    --hidden-import pystray._win32 `
    --exclude-module pygame --exclude-module numpy --exclude-module tkinter `
    --exclude-module matplotlib --exclude-module PIL.ImageQt --exclude-module PyQt5 --exclude-module PySide6 `
    --distpath dist --workpath build --specpath build `
    tray.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
Get-Item dist\ForeverPrices.exe | Select-Object FullName, @{n='MB';e={[math]::Round($_.Length/1MB,1)}}
