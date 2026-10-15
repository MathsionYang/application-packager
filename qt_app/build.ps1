[CmdletBinding()]
param(
    [string]$Python = '',
    [switch]$InstallDependencies
)

$ErrorActionPreference = 'Stop'
$projectDir = $PSScriptRoot
Push-Location -LiteralPath $projectDir
try {
    if ([string]::IsNullOrWhiteSpace($Python)) {
        $localPython = Join-Path $projectDir '.venv\Scripts\python.exe'
        if (Test-Path -LiteralPath $localPython) {
            $Python = $localPython
        } elseif (Get-Command py -ErrorAction SilentlyContinue) {
            $Python = (& py -3.13 -c 'import sys; print(sys.executable)')
            if ($LASTEXITCODE -ne 0) { throw 'Python 3.13 not found. Pass -Python with a supported interpreter path.' }
            $Python = $Python.Trim()
        } else {
            $Python = 'python'
        }
    }
    @'
import sys
assert (3, 10) <= sys.version_info[:2] < (3, 15), "Python 3.10-3.14 required"
print(sys.version)
'@ | & $Python -
    if ($LASTEXITCODE -ne 0) { throw 'Python interpreter check failed.' }
    if ($InstallDependencies) {
        & $Python -m pip install -r (Join-Path $projectDir 'requirements.txt')
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    }
    @'
import importlib.metadata as m
import PySide6.QtWidgets, PySide6.QtSvg, PyInstaller
assert m.version("PySide6-Essentials") == "6.12.0", "Install requirements.txt first"
print("Qt", m.version("PySide6-Essentials"), "PyInstaller", m.version("PyInstaller"))
'@ | & $Python -
    if ($LASTEXITCODE -ne 0) { throw 'Dependency check failed. Use -InstallDependencies or install requirements.txt.' }
    & $Python -m PyInstaller --clean --noconfirm --windowed --onedir --name DesktopPackager --distpath (Join-Path $projectDir 'dist') --workpath (Join-Path $projectDir 'build') --specpath (Join-Path $projectDir 'build') --hidden-import PySide6.QtSvg (Join-Path $projectDir 'main.py')
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
    $executable = Join-Path $projectDir 'dist\DesktopPackager\DesktopPackager.exe'
    if (-not (Test-Path -LiteralPath $executable)) { throw 'Build returned without the expected executable.' }
    Write-Host "Built: $executable"
    Write-Host 'Distribute the entire dist\DesktopPackager directory, including _internal.'
} finally {
    Pop-Location
}
