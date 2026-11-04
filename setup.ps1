$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectRoot

if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    py -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Python 3 could not create the virtual environment." }
}

& ".\.venv\Scripts\python.exe" -m pip install -e .
if ($LASTEXITCODE -ne 0) { throw "The local package installation failed." }
Write-Host "HWiNFOterm is ready. Run .\Start-HWiNFOterm.cmd"
