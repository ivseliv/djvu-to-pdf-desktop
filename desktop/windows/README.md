# Windows editions

There are two deliberately separate Windows targets.  A modern Windows x64
release is the current deliverable.  Windows XP is a later legacy project, not
a compatibility checkbox on the same executable.

## Modern Windows x64

Target: 64-bit Windows 10 and Windows 11.  Build on 64-bit Windows with
CPython 3.11, 3.12, or 3.13.  PyInstaller is not a cross-compiler, so a macOS
or Linux build cannot make a valid Windows executable.

`dpsprep` needs the native `djvulibre-python` binding.  Its upstream project
does not publish a Windows wheel, therefore the release process uses a
separately built and tested wheel rather than hoping `pip` can build one during
packaging.  The wheel and DLL must use the same architecture, Python ABI and
DjVuLibre version.

Before building, place the verified 64-bit DjVuLibre runtime here (the folder
is intentionally ignored by Git):

```text
third_party/windows-x64/djvulibre/
└── bin/
    └── libdjvulibre-21.dll
```

Include any DLL dependencies required by that runtime in the same `bin`
folder.  Keep the matching `djvulibre-python` wheel outside the repository or
in a protected release-artifacts store.  It must import `djvu.decode` and
`djvu.sexpr` under the chosen x64 CPython version before it is accepted.

In PowerShell from the repository root:

```powershell
.\desktop\windows\build-x64.ps1 -BindingWheel C:\release-input\djvulibre_python-0.9.3-cp311-cp311-win_amd64.whl -Clean
```

The output is `dist\windows-x64\DjVu to PDF\DjVu to PDF.exe`.  The build script
performs an import test and starts the packaged command-line worker with
`--help`; release QA must additionally convert DjVu fixtures, extract the PDF
text with a PDF reader, and test on a clean Windows machine before signing the
installer.

The script packages the DjVuLibre DLL and GPL notices with the app.  Audit the
exact native runtime and include every required third-party notice before a
public release.

## Windows XP: later, separate legacy line

This desktop codebase requires Python 3.11 or newer and a current PyInstaller.
Neither is a Windows XP runtime.  An XP build cannot be derived by changing a
PyInstaller flag, and a current Windows x64 EXE must not be advertised as XP
compatible.

The XP project needs its own support decision and toolchain, at minimum:

1. choose XP SP3 32-bit, XP Professional x64, or both;
2. use an XP-compatible compiler, C runtime, Python/runtime and DjVuLibre;
3. build a separate UI/runtime, likely without the current Python 3.11 stack;
4. test on a real XP virtual machine with the intended DjVu corpus.

Keeping this work as a distinct `legacy-xp` branch or repository avoids
weakening the modern x64 release and makes its security/support limits clear.
