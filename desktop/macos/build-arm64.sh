#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
desktop_version=$(PYTHONPATH="$project_root/src" python3 -c 'from dpsprep_desktop import __version__; print(__version__)')
qpdf_bin=$(command -v qpdf)
qpdf_real=$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$qpdf_bin")
qpdf_prefix=$(CDPATH= cd -- "$(dirname -- "$qpdf_real")/.." && pwd)
qpdf_lib=$(find "$qpdf_prefix/lib" -maxdepth 1 -name 'libqpdf.*.dylib' -type f | sort | tail -1)

if [ -z "$qpdf_lib" ]; then
    echo "Could not locate libqpdf in $qpdf_prefix/lib" >&2
    exit 1
fi

cd "$project_root"
uv run --with 'pyinstaller>=6.12' pyinstaller \
    --noconfirm \
    --windowed \
    --name 'DjVu to PDF' \
    --add-data 'LICENSES:LICENSES' \
    --add-data "$qpdf_prefix/LICENSE.txt:LICENSES/QPDF" \
    --add-data "$qpdf_prefix/NOTICE.md:LICENSES/QPDF" \
    --add-binary "$qpdf_real:qpdf/bin" \
    --add-binary "$qpdf_lib:qpdf/lib" \
    --collect-all dpsprep \
    --collect-all dpsprep_desktop \
    --collect-all djvu \
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
codesign --force --deep --sign - "$app"
codesign --verify --deep --strict "$app"

file "$app/Contents/MacOS/DjVu to PDF" | grep -q 'arm64'
file "$app/Contents/Frameworks/qpdf/bin/qpdf" | grep -q 'arm64'

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
    --run-dpsprep --overwrite --stream-to-dest --pool-size 2 \
    --tmp "$smoke_dir" \
    "$project_root/fixtures/lipsum_words.djvu" \
    "$smoke_dir/smoke.pdf"
PATH=/usr/bin:/bin "$app/Contents/Frameworks/qpdf/bin/qpdf" --check "$smoke_dir/smoke.pdf"
echo "Created DjVu to PDF $desktop_version for macOS arm64: $app"
