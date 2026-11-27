"""Small, explicit adapter for the standalone QPDF executable."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from time import monotonic

import click
import pikepdf


QPDF_PROGRESS_PREFIX = '__DPSPREP_QPDF_PROGRESS__ '
VALIDATION_PROGRESS_PREFIX = '__DPSPREP_VALIDATION_ACTIVE__ '


def find_qpdf() -> Path:
    """Find bundled QPDF first and never silently choose a Python merger."""
    override = os.environ.get('DPSPREP_QPDF')
    candidates: list[Path] = []
    if override:
        candidate = Path(override)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
        raise click.ClickException(f'Configured QPDF executable is unavailable: {candidate}')
    if getattr(sys, 'frozen', False):
        bundle_root = Path(getattr(sys, '_MEIPASS', Path(sys.executable).parent))
        candidates.extend([
            bundle_root / 'qpdf' / 'bin' / 'qpdf',
            bundle_root / 'qpdf' / 'bin' / 'qpdf.exe',
            Path(sys.executable).parent.parent / 'Frameworks' / 'qpdf' / 'bin' / 'qpdf',
            Path(sys.executable).parent / 'qpdf' / 'bin' / 'qpdf.exe',
        ])
    system_qpdf = shutil.which('qpdf')
    if system_qpdf:
        candidates.append(Path(system_qpdf))

    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise click.ClickException('QPDF is not available. Reinstall the desktop application.')


def merge_pdfs(inputs: list[Path], output: Path, argument_file: Path) -> None:
    """Merge PDFs with bounded memory using QPDF's one-argument-per-line file."""
    for path in [*inputs, output]:
        if '\n' in str(path) or '\r' in str(path):
            raise click.ClickException('PDF paths containing line breaks are not supported by QPDF argument files.')
    # Object streams force a PDF 1.5+ cross-reference stream. Traditional
    # xref tables have ten-digit byte offsets and become invalid once a PDF
    # grows beyond 9,999,999,999 bytes.
    arguments = [
        '--empty',
        '--progress',
        '--object-streams=generate',
        '--pages',
        *(str(path) for path in inputs),
        '--',
        str(output),
    ]
    argument_file.write_text('\n'.join(arguments) + '\n', encoding='utf-8')
    process = subprocess.Popen(
        [str(find_qpdf()), f'@{argument_file}'],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    output_lines: list[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        output_lines.append(line)
        if match := re.search(r'(\d+)%', line):
            print(f'{QPDF_PROGRESS_PREFIX}{match.group(1)}', flush=True)
    return_code = process.wait()
    if return_code != 0:
        raise click.ClickException(f'QPDF could not assemble the PDF:\n{"".join(output_lines).strip()}')


def check_pdf(path: Path) -> int:
    started = monotonic()
    with tempfile.TemporaryFile(mode='w+t', encoding='utf-8') as output:
        process = subprocess.Popen(
            [str(find_qpdf()), '--check', '--show-npages', str(path)],
            text=True,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        while True:
            try:
                return_code = process.wait(timeout=5)
                break
            except subprocess.TimeoutExpired:
                if os.environ.get('DPSPREP_GUI_PROGRESS') == '1':
                    print(f'{VALIDATION_PROGRESS_PREFIX}{int(monotonic() - started)}', flush=True)
        output.seek(0)
        details = output.read().strip()
    if return_code != 0:
        raise click.ClickException(f'QPDF validation failed:\n{details}')
    for line in reversed(details.splitlines()):
        if line.strip().isdigit():
            return int(line.strip())
    raise click.ClickException(f'QPDF validation did not report a page count:\n{details}')


def validate_output(path: Path, expected_pages: int, text_reference: Path | None = None) -> None:
    """Validate structure, page count, and representative searchable text."""
    actual_pages = check_pdf(path)
    if actual_pages != expected_pages:
        raise click.ClickException(
            f'PDF validation failed: expected {expected_pages} pages, found {actual_pages}.',
        )
    if text_reference is not None:
        _validate_text_pages(path, text_reference, expected_pages)


def validate_chunk_text(chunks: list[Path], text_reference: Path, expected_pages: int, chunk_size: int) -> None:
    """Check sampled text in small blocks before the large QPDF merge."""
    sample = _sample_pages(expected_pages)
    with pikepdf.open(text_reference) as reference:
        expected_text = [index for index in sample if _page_has_text(reference.pages[index])]
    missing: list[int] = []
    by_chunk: dict[int, list[int]] = {}
    for index in expected_text:
        by_chunk.setdefault(index // chunk_size, []).append(index)
    for chunk_index, page_indexes in by_chunk.items():
        with pikepdf.open(chunks[chunk_index]) as chunk:
            missing.extend(
                index for index in page_indexes if not _page_has_text(chunk.pages[index % chunk_size])
            )
    if missing:
        pages = ', '.join(str(index + 1) for index in missing)
        raise click.ClickException(
            f'PDF validation failed: the searchable text layer is missing on sampled page(s) {pages}.',
        )


def _validate_text_pages(path: Path, text_reference: Path, expected_pages: int) -> None:
    sample = _sample_pages(expected_pages)
    with pikepdf.open(path) as pdf, pikepdf.open(text_reference) as reference:
        text_pages = [index for index in sample if _page_has_text(reference.pages[index])]
        missing_text = [index for index in text_pages if not _page_has_text(pdf.pages[index])]
    if missing_text:
        pages = ', '.join(str(index + 1) for index in missing_text)
        raise click.ClickException(
            f'PDF validation failed: the searchable text layer is missing on sampled page(s) {pages}.',
        )


def _sample_pages(expected_pages: int) -> list[int]:
    if expected_pages <= 20:
        return list(range(expected_pages))
    return [index * (expected_pages - 1) // 19 for index in range(20)]


def _page_has_text(page: pikepdf.Page) -> bool:
    try:
        return any(
            str(getattr(instruction, 'operator', '')) in {'Tj', 'TJ', "'", '"'}
            for instruction in pikepdf.parse_content_stream(page)
        )
    except (pikepdf.PdfError, ValueError):
        return False
