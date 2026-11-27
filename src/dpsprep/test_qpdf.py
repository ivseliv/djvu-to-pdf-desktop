import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pikepdf
import pytest
from djvu import sexpr

from dpsprep.outline.outline import OutlineTransformVisitor
from dpsprep.pdf import IncrementalChunkAssembler, _write_chunks_to_destination
from dpsprep.qpdf import _sample_pages, find_qpdf, merge_pdfs, validate_chunk_text, validate_output
from dpsprep.workdir import WorkingDirectory, writing_checkpoint


if TYPE_CHECKING:
    from dpsprep.options import DpsPrepOptions


def _blank_pdf(path: Path, pages: int = 1) -> None:
    with pikepdf.Pdf.new() as pdf:
        for _ in range(pages):
            pdf.add_blank_page(page_size=(100, 100))
        pdf.save(path)


@pytest.mark.skipif(shutil.which('qpdf') is None, reason='qpdf executable is required')
def test_external_qpdf_preserves_pages_and_nested_outline(tmp_path: Path) -> None:
    chunks: list[Path] = []
    for number in range(2):
        path = tmp_path / f'chunk_{number}.pdf'
        with pikepdf.Pdf.new() as pdf:
            pdf.add_blank_page(page_size=(100, 100))
            pdf.save(path)
        chunks.append(path)

    expression = sexpr.ListExpression([
        sexpr.SymbolExpression(sexpr.Symbol('bookmarks')),
        sexpr.ListExpression([
            sexpr.StringExpression(b'Chapter'),
            sexpr.StringExpression(b'#2'),
            sexpr.ListExpression([
                sexpr.StringExpression(b'Section'),
                sexpr.StringExpression(b'#1'),
            ]),
        ]),
    ])
    outline = OutlineTransformVisitor().visit(expression)
    output = tmp_path / 'output.pdf'
    _write_chunks_to_destination(chunks, output, outline, 2, 2, None)

    with pikepdf.open(output) as pdf:
        assert len(pdf.pages) == 2
        with pdf.open_outline() as result_outline:
            assert result_outline.root[0].title == 'Chapter'
            assert result_outline.root[0].children[0].title == 'Section'


def test_missing_configured_qpdf_fails_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('DPSPREP_QPDF', '/definitely/missing/qpdf')
    with pytest.raises(Exception, match='Configured QPDF executable is unavailable'):
        find_qpdf()


@pytest.mark.skipif(shutil.which('qpdf') is None, reason='qpdf executable is required')
def test_qpdf_argument_file_handles_spaces_and_unicode(tmp_path: Path) -> None:
    source = tmp_path / 'блок с пробелами.pdf'
    with pikepdf.Pdf.new() as pdf:
        pdf.add_blank_page(page_size=(100, 100))
        pdf.save(source)
    output = tmp_path / 'готовый файл.pdf'

    argument_file = tmp_path / 'список аргументов.txt'
    merge_pdfs([source], output, argument_file)

    with pikepdf.open(output) as pdf:
        assert len(pdf.pages) == 1
    assert '--object-streams=generate' in argument_file.read_text(encoding='utf-8').splitlines()


def test_qpdf_argument_file_rejects_line_breaks(tmp_path: Path) -> None:
    with pytest.raises(Exception, match='line breaks'):
        merge_pdfs([tmp_path / 'bad\nname.pdf'], tmp_path / 'output.pdf', tmp_path / 'arguments.txt')


@pytest.mark.skipif(shutil.which('qpdf') is None, reason='qpdf executable is required')
def test_validation_detects_a_missing_sampled_text_layer(tmp_path: Path) -> None:
    reference = tmp_path / 'reference.pdf'
    output = tmp_path / 'output.pdf'
    with pikepdf.Pdf.new() as pdf:
        page = pdf.add_blank_page(page_size=(100, 100))
        page.Contents = pdf.make_stream(b'BT (searchable) Tj ET')
        pdf.save(reference)
    with pikepdf.Pdf.new() as pdf:
        pdf.add_blank_page(page_size=(100, 100))
        pdf.save(output)

    with pytest.raises(Exception, match='text layer is missing'):
        validate_output(output, 1, reference)


def test_chunk_validation_checks_text_before_large_merge(tmp_path: Path) -> None:
    reference = tmp_path / 'reference.pdf'
    chunk = tmp_path / 'chunk.pdf'
    with pikepdf.Pdf.new() as pdf:
        page = pdf.add_blank_page(page_size=(100, 100))
        page.Contents = pdf.make_stream(b'BT (searchable) Tj ET')
        pdf.save(reference)
    with pikepdf.Pdf.new() as pdf:
        page = pdf.add_blank_page(page_size=(100, 100))
        page.Contents = pdf.make_stream(b'BT (searchable) Tj ET')
        pdf.save(chunk)

    validate_chunk_text([chunk], reference, expected_pages=1, chunk_size=64)


def test_working_disk_shortage_is_detected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    workdir = WorkingDirectory(tmp_path / 'source.djvu', tmp_path / 'output.pdf', tmp_path)
    options = SimpleNamespace(workdir=workdir, no_text=True)
    _blank_pdf(tmp_path / 'chunk_0001.pdf', 64)
    assembler = IncrementalChunkAssembler(cast('DpsPrepOptions', options), 64)
    monkeypatch.setattr('dpsprep.pdf.shutil.disk_usage', lambda _path: SimpleNamespace(free=0))

    with pytest.raises(Exception, match='working disk is almost full'):
        assembler.consume_ready()


def test_assembler_resumes_from_an_existing_chunk(tmp_path: Path) -> None:
    workdir = WorkingDirectory(tmp_path / 'source.djvu', tmp_path / 'output.pdf', tmp_path)
    options = SimpleNamespace(workdir=workdir, no_text=True)
    _blank_pdf(tmp_path / 'chunk_0001.pdf', 64)
    assembler = IncrementalChunkAssembler(cast('DpsPrepOptions', options), 64)

    assembler.consume_ready()

    assert assembler.next_page == 64


def test_incomplete_legacy_chunk_keeps_ready_pages(tmp_path: Path) -> None:
    workdir = WorkingDirectory(tmp_path / 'source.djvu', tmp_path / 'output.pdf', tmp_path)
    options = SimpleNamespace(workdir=workdir, no_text=True)
    (tmp_path / 'chunk_0001.pdf').write_bytes(b'incomplete')
    _blank_pdf(workdir.get_page_pdf_path(0))
    assembler = IncrementalChunkAssembler(cast('DpsPrepOptions', options), 2)
    assembler.mark_page_ready(0)
    assembler.consume_ready()
    assert assembler.next_page == 0
    assert workdir.get_page_pdf_path(0).exists()


def test_failed_checkpoint_write_does_not_replace_previous_result(tmp_path: Path) -> None:
    target = tmp_path / 'merged.pdf'
    target.write_bytes(b'previous result')

    def incomplete_write() -> None:
        with writing_checkpoint(target) as partial:
            partial.write_bytes(b'incomplete')
            raise OSError('out of space')

    with pytest.raises(OSError, match='out of space'):
        incomplete_write()
    assert target.read_bytes() == b'previous result'
    assert list(tmp_path.iterdir()) == [target]


def test_chunk_images_are_not_retained_by_text_reader(tmp_path: Path) -> None:
    workdir = WorkingDirectory(tmp_path / 'book.djvu', tmp_path / 'out.pdf', tmp_path)
    _blank_pdf(workdir.text_layer_pdf_path)
    _blank_pdf(workdir.get_page_pdf_path(0))
    options = SimpleNamespace(workdir=workdir, no_text=False)
    assembler = IncrementalChunkAssembler(cast('DpsPrepOptions', options), 1)
    assembler.mark_text_ready()
    assembler.mark_page_ready(0)
    assembler.consume_ready()
    assert assembler.text_pdf is not None
    assert assembler.text_pdf.pages[0].inheritable.Resources.XObject is None


def test_short_books_validate_last_page() -> None:
    assert _sample_pages(3) == [0, 1, 2]
    assert _sample_pages(6236)[-1] == 6235
