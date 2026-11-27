#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
desktop_version=$(PYTHONPATH="$project_root/src" python3 -c 'from dpsprep_desktop import __version__; print(__version__)')
qpdf_bin=$(command -v qpdf)
qpdf_real=$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$qpdf_bin")
qpdf_prefix=$(CDPATH= cd -- "$(dirname -- "$qpdf_real")/.." && pwd)
qpdf_lib=$(find "$qpdf_prefix/lib" -maxdepth 1 -name 'libqpdf.*.dylib' -type f | sort | tail -1)
djvu_prefix=$(brew --prefix djvulibre)

if [ -z "$qpdf_lib" ]; then
    echo "Could not locate libqpdf in $qpdf_prefix/lib" >&2
    exit 1
fi

cd "$project_root"
uv run --frozen --no-default-groups --with 'pyinstaller==6.12.0' python desktop/macos/release_materials.py --notices build/third-party-notices
uv run --frozen --no-default-groups --with 'pyinstaller==6.12.0' pyinstaller \
    --noconfirm \
    --windowed \
    --osx-bundle-identifier org.ivseliv.djvu-to-pdf \
    --name 'DjVu to PDF' \
    --add-data 'LICENSES:LICENSES' \
    --add-data 'build/third-party-notices:LICENSES/third-party' \
    --add-data "$djvu_prefix/COPYRIGHT:LICENSES/DjVuLibre" \
    --add-data "$djvu_prefix/COPYING:LICENSES/DjVuLibre" \
    --add-data "$qpdf_prefix/LICENSE.txt:LICENSES/QPDF" \
    --add-data "$qpdf_prefix/NOTICE.md:LICENSES/QPDF" \
    --add-binary "$qpdf_real:qpdf/bin" \
    --add-binary "$qpdf_lib:qpdf/lib" \
    --add-binary "$djvu_prefix/bin/ddjvu:djvulibre/bin" \
    --add-binary "$djvu_prefix/bin/djvused:djvulibre/bin" \
    --exclude-module djvu \
    --exclude-module pytest \
    --collect-all dpsprep \
    --collect-all dpsprep_desktop \
    --collect-all pikepdf \
    desktop/launcher.py

app="$project_root/dist/DjVu to PDF.app"
set_or_add_plist_string() {
    key=$1
    value=$2
    plist=$3
    /usr/libexec/PlistBuddy -c "Set :$key $value" "$plist" 2>/dev/null || \
        /usr/libexec/PlistBuddy -c "Add :$key string $value" "$plist"
}

set_or_add_plist_string CFBundleShortVersionString "$desktop_version" "$app/Contents/Info.plist"
set_or_add_plist_string CFBundleVersion "$desktop_version" "$app/Contents/Info.plist"
# A Homebrew helper may require newer macOS than the Python bootloader does.
minimum_macos=$(python3 - "$app" <<'PY'
import re
import subprocess
import sys
from pathlib import Path

versions = []
for binary in Path(sys.argv[1]).rglob('*'):
    if binary.is_symlink() or not binary.is_file():
        continue
    with binary.open('rb') as stream:
        if stream.read(4) not in (b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf'):
            continue
    info = subprocess.check_output(['otool', '-arch', 'arm64', '-l', str(binary)], text=True)
    match = re.search(r'cmd LC_(?:BUILD_VERSION|VERSION_MIN_MACOSX).*?\n\s*(?:minos|version) ([\d.]+)', info, re.S)
    if not match:
        raise SystemExit(f'Cannot determine deployment target of {binary}')
    versions.append(tuple(map(int, match[1].split('.'))))
print('.'.join(map(str, max(versions))))
PY
)
set_or_add_plist_string LSMinimumSystemVersion "$minimum_macos" "$app/Contents/Info.plist"
codesign --force --deep --sign - "$app"
codesign --verify --deep --strict "$app"

file "$app/Contents/MacOS/DjVu to PDF" | grep -q 'arm64'
file "$app/Contents/Frameworks/qpdf/bin/qpdf" | grep -q 'arm64'
for helper in ddjvu djvused; do
    file "$app/Contents/Frameworks/djvulibre/bin/$helper" | grep -q 'arm64'
done

# A restricted PATH alone cannot reveal absolute Homebrew dylib references.
# Inspect every Mach-O file so the release remains autonomous on a Mac that
# has neither Homebrew nor QPDF installed.
find "$app/Contents" -type f -print0 | while IFS= read -r -d '' candidate; do
    if file "$candidate" | grep -q 'Mach-O'; then
        if otool -L "$candidate" | grep -E '/(opt/homebrew|usr/local|Cellar)/'; then
            echo "Non-autonomous library reference in $candidate" >&2
            exit 1
        fi
    fi
done

smoke_dir=$(mktemp -d /private/tmp/dpsprep-build-smoke.XXXXXX)
trap 'rm -rf "$smoke_dir"' EXIT
PATH=/usr/bin:/bin "$app/Contents/MacOS/DjVu to PDF" \
    --run-dpsprep --overwrite --stream-to-dest --pool-size 2 --max-dpi 300 \
    --tmp "$smoke_dir" \
    "$project_root/fixtures/lipsum_words.djvu" \
    "$smoke_dir/smoke.pdf"
PATH=/usr/bin:/bin "$app/Contents/Frameworks/qpdf/bin/qpdf" --check "$smoke_dir/smoke.pdf"
pdftotext "$smoke_dir/smoke.pdf" "$smoke_dir/text.txt"
grep -iq 'Lorem ipsum' "$smoke_dir/text.txt"
echo "Created DjVu to PDF $desktop_version for macOS arm64: $app"
