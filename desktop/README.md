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

The default balanced profile renders page images at no more than 300 DPI and
uses JPEG quality 40. Compact grayscale, compact bitonal, and full-resolution
profiles are also available. Downsampling changes only page imagery; text and
bookmark geometry retains the original physical page dimensions.

For large files, the desktop worker keeps at most two 64-page windows in flight,
assembles completed pages into blocks, and removes their one-page PDFs at once.
Standalone QPDF then joins the blocks with bounded memory.  The GUI owns a
persistent per-user job directory in `/var/tmp/djvu-to-pdf-jobs-<uid>` on macOS
and Linux (existing privately owned `/var/tmp/djvu-to-pdf-jobs` directories
are retained). Windows uses the user's temporary directory. The GUI can resume an
interrupted conversion, and offers explicit cleanup.  On the same disk it
publishes the verified result with an atomic rename, so saving does not require
a third full copy of a very large PDF.

The progress bar covers source staging, text-layer transfer, page rendering,
block assembly, QPDF merging, validation, and final publication. Validation
checks the PDF structure, page count, and representative text-layer content
before the result is reported as complete.

## Release packaging

Build each platform on that platform. A frozen app must ship a 64-bit
DjVuLibre runtime as well as the Python bindings; test it on a clean machine.

Install QPDF with Homebrew on the build machine, then run
`desktop/macos/build-arm64.sh`. The script embeds the QPDF executable, its
dynamic library, license, and notices. It sets the desktop version in the app
metadata, applies an ad-hoc signature, verifies both binaries are arm64, and
converts a text-bearing fixture with a restricted `PATH` to prove that the app
does not use Homebrew at runtime.

Produce separate macOS `arm64`, macOS `x86_64`, and Windows `x86_64` builds.
On macOS, sign and notarize the `.app` before publishing; on Windows, code-sign
the installer. The Windows build is a release gate because `djvulibre-python`
needs a compatible 64-bit DjVuLibre runtime.

For the modern Windows x64 build contract and the deliberately separate
Windows XP legacy plan, see [windows/README.md](windows/README.md).

The project remains GPL-3.0-or-later. Ship the project license and audit every
bundled native dependency before any public distribution.
