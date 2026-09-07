<#
.SYNOPSIS
Builds the modern Windows x64 edition of DjVu to PDF.

.DESCRIPTION
This script must run on a 64-bit Windows machine.  PyInstaller is not a
cross-compiler, so running it on macOS or Linux cannot produce a Windows EXE.

The DjVu binding is supplied as a separately tested wheel.  The upstream
djvulibre-python project does not publish Windows wheels, so accepting a wheel
explicitly prevents pip from silently attempting a different, unverified
native build during a release.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateScript({ Test-Path $_ -PathType Leaf })]
    [string]$BindingWheel,

    [string]$DjVuLibreRoot = (Join-Path $PSScriptRoot "..\\..\\third_party\\windows-x64\\djvulibre"),

    [string]$QpdfRoot = (Join-Path $PSScriptRoot "..\\..\\third_party\\windows-x64\\qpdf"),

    [string]$PythonExe = "py",

    [ValidateSet("3.11", "3.12", "3.13")]
    [string]$PythonVersion = "3.11",

    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\\..")).Path
$BuildRoot = Join-Path $ProjectRoot "build\\windows-x64"
$DistRoot = Join-Path $ProjectRoot "dist\\windows-x64"
$VenvRoot = Join-Path $BuildRoot ".venv"
$VenvPython = Join-Path $VenvRoot "Scripts\\python.exe"
$DjVuLibreDll = Join-Path $DjVuLibreRoot "bin\\libdjvulibre-21.dll"
$QpdfExe = Join-Path $QpdfRoot "bin\\qpdf.exe"

function Invoke-SystemPython {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)

    if ($PythonExe -eq "py") {
        & py "-$PythonVersion" @Arguments
    }
    else {
        & $PythonExe @Arguments
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed with exit code $LASTEXITCODE."
    }
}

function Invoke-VenvPython {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)

    & $VenvPython @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Virtual-environment Python command failed with exit code $LASTEXITCODE."
    }
}

if (-not [Environment]::Is64BitOperatingSystem) {
    throw "A 64-bit Windows installation is required for the Windows x64 edition."
}
if (-not (Test-Path $DjVuLibreDll -PathType Leaf)) {
    throw @"
DjVuLibre runtime not found: $DjVuLibreDll

Provide the matching 64-bit runtime at:
  third_party\\windows-x64\\djvulibre\\bin\\libdjvulibre-21.dll

See desktop\\windows\\README.md for the required native-runtime contract.
"@
}
if (-not (Test-Path $QpdfExe -PathType Leaf)) {
    throw "QPDF runtime not found: $QpdfExe. Extract the official Windows x64 QPDF package under third_party\\windows-x64\\qpdf."
}

if ($Clean) {
    foreach ($Path in @($BuildRoot, $DistRoot)) {
        if (Test-Path $Path) {
            Remove-Item -Recurse -Force $Path
        }
    }
}

New-Item -ItemType Directory -Force -Path $BuildRoot, $DistRoot | Out-Null
Invoke-SystemPython -c "import struct; assert struct.calcsize('P') == 8, 'Use a 64-bit Python'; print('Using 64-bit Python')"
Invoke-SystemPython -m venv $VenvRoot

Invoke-VenvPython -m pip install --upgrade pip
Invoke-VenvPython -m pip install "pyinstaller>=6.12"
Invoke-VenvPython -m pip install $BindingWheel
Invoke-VenvPython -m pip install --no-deps $ProjectRoot
Invoke-VenvPython -m pip install "click>=8" "fpdf2>=2.8.6" "pdfrw>=0.4" "pikepdf>=10.5.1" "pillow>=12.2.0" "rich>=13" "typing_extensions>=4.5"

$env:PATH = "$(Split-Path $DjVuLibreDll);$env:PATH"
Invoke-VenvPython -c "import struct; import djvu.decode, djvu.sexpr; assert struct.calcsize('P') == 8; print('DjVu bindings imported successfully')"

Invoke-VenvPython -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --name "DjVu to PDF" `
    --distpath $DistRoot `
    --workpath (Join-Path $BuildRoot "pyinstaller") `
    --specpath $BuildRoot `
    --paths (Join-Path $ProjectRoot "src") `
    --add-binary "$DjVuLibreDll;." `
    --add-data "$(Join-Path $ProjectRoot 'LICENSES');LICENSES" `
    --collect-all dpsprep `
    --collect-all dpsprep_desktop `
    --collect-all djvu `
    --collect-all pikepdf `
    --hidden-import djvu.decode `
    --hidden-import djvu.sexpr `
    (Join-Path $ProjectRoot "desktop\\launcher.py")

$App = Join-Path $DistRoot "DjVu to PDF\\DjVu to PDF.exe"
if (-not (Test-Path $App -PathType Leaf)) {
    throw "PyInstaller did not produce the expected executable: $App"
}

$AppDirectory = Split-Path $App
$BundledQpdf = Join-Path $AppDirectory "qpdf"
$LicenseDirectory = Join-Path $AppDirectory "LICENSES"
New-Item -ItemType Directory -Force -Path $BundledQpdf | Out-Null
New-Item -ItemType Directory -Force -Path $LicenseDirectory | Out-Null
Copy-Item -Recurse -Force (Join-Path $QpdfRoot "bin") $BundledQpdf
if (Test-Path (Join-Path $QpdfRoot "LICENSE.txt")) {
    Copy-Item -Force (Join-Path $QpdfRoot "LICENSE.txt") (Join-Path $LicenseDirectory "QPDF-LICENSE.txt")
}
if (Test-Path (Join-Path $QpdfRoot "NOTICE.md")) {
    Copy-Item -Force (Join-Path $QpdfRoot "NOTICE.md") (Join-Path $LicenseDirectory "QPDF-NOTICE.md")
}
# DjVuLibre builds can depend on companion DLLs (for example a runtime library).
# Copy every explicitly staged runtime DLL, not only the primary library.
Get-ChildItem -Path (Split-Path $DjVuLibreDll) -Filter "*.dll" -File | Copy-Item -Destination $AppDirectory -Force

& $App --run-dpsprep --help
if ($LASTEXITCODE -ne 0) {
    throw "The packaged CLI self-check failed with exit code $LASTEXITCODE."
}

$BundledQpdfExe = Join-Path $BundledQpdf "bin\\qpdf.exe"
& $BundledQpdfExe --version
if ($LASTEXITCODE -ne 0) {
    throw "The bundled QPDF runtime self-check failed with exit code $LASTEXITCODE."
}

$SmokeOutput = Join-Path $BuildRoot "autonomous-smoke.pdf"
& $App --run-dpsprep --overwrite --stream-to-dest --pool-size 2 --max-dpi 300 `
    (Join-Path $ProjectRoot "fixtures\\lipsum_words.djvu") $SmokeOutput
if ($LASTEXITCODE -ne 0) {
    throw "The packaged conversion smoke test failed with exit code $LASTEXITCODE."
}
& $BundledQpdfExe --check $SmokeOutput
if ($LASTEXITCODE -ne 0) {
    throw "The packaged PDF validation failed with exit code $LASTEXITCODE."
}
Remove-Item -Force $SmokeOutput

Write-Host "Windows x64 build created: $App"
