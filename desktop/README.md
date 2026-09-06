# dpsprep desktop prototype

`dpsprep-gui` is a native Tk desktop front end. It launches the project's public
CLI in a worker process, so a conversion remains offline and the GUI does not
depend on a private `dpsprep` Python API.

In a frozen app, the executable re-launches itself with an internal
`--run-dpsprep` argument. That starts only the CLI worker (not another GUI), so
the window can continue to show conversion logs and offer cancellation.
The PyInstaller launcher also calls `multiprocessing.freeze_support()` before
loading the UI; this is required for dpsprep's page-processing workers.

## Apple Silicon and multiprocessing

Apple Silicon is supported natively by the `arm64` build. The **Advanced
settings** panel lets users set the number of worker processes; leaving it
empty delegates the choice to dpsprep. The frozen `arm64` app has been smoke
tested with four concurrent workers, including extraction of the resulting
PDF's text layer. Keep `freeze_support()` in `desktop/launcher.py`: without it
macOS worker processes can accidentally re-open the GUI instead of processing
pages.

## Run during development

```console
uv sync --no-dev
uv run dpsprep-gui
```

The source DjVu's existing text layer and bookmarks are retained by default.
The desktop interface does not perform OCR or run PDF optimization: it only
transfers the searchable source layer into the generated PDF.

For large files, the desktop worker assembles page PDFs in batches of 64 and
writes the result directly to a temporary file beside the selected destination.
This avoids keeping three full PDF copies at once. The GUI removes its working
directory after either a successful conversion or a failure, including an
out-of-space error.

## Release packaging

Build each platform on that platform. A frozen app must ship a 64-bit
DjVuLibre runtime as well as the Python bindings; test it on a clean machine.

```console
uv run --with 'pyinstaller>=6.12' pyinstaller --noconfirm --windowed --name "DjVu to PDF" \
  --add-data 'LICENSES:LICENSES' \
  --collect-all dpsprep --collect-all dpsprep_desktop --collect-all djvu \
  desktop/launcher.py
```

Produce separate macOS `arm64`, macOS `x86_64`, and Windows `x86_64` builds.
On macOS, sign and notarize the `.app` before publishing; on Windows, code-sign
the installer. The Windows build is a release gate because `djvulibre-python`
needs a compatible 64-bit DjVuLibre runtime.

For the modern Windows x64 build contract and the deliberately separate
Windows XP legacy plan, see [windows/README.md](windows/README.md).

The project remains GPL-3.0-or-later. Ship the project license and audit every
bundled native dependency before any public distribution.
