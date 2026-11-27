import errno
import os
import pathlib
import shutil
import tempfile

import click
import pdfrw
import pikepdf
from pdfrw.objects.pdfstring import PdfString

from dpsprep.options import DpsPrepOptions
from dpsprep.qpdf import merge_pdfs, validate_chunk_text, validate_output
from dpsprep.workdir import writing_checkpoint


def is_valid_pdf(path: pathlib.Path, expected_pages: int | None = None) -> bool:
    try:
        with pikepdf.open(path, attempt_recovery=False) as pdf:
            return expected_pages is None or len(pdf.pages) == expected_pages
    except (FileNotFoundError, pikepdf.PdfError):
        return False


CHUNK_SIZE = 64
COMBINE_PROGRESS_PREFIX = '__DPSPREP_COMBINE_PROGRESS__ '
# Leave enough room for QPDF's small temporary structures and filesystem
# metadata.  Requiring multiple GiB here rejects valid large jobs whose final
# merged file still fits; the estimate below already reserves a complete
# second copy of the output while the chunks are present.
DISK_SAFETY_BYTES = 512 * 1024**2


def _report_combine_progress(completed: int, total: int) -> None:
    if os.environ.get('DPSPREP_GUI_PROGRESS') == '1':
        print(f'{COMBINE_PROGRESS_PREFIX}{completed}/{total}', flush=True)


def _chunk_path(options: DpsPrepOptions, chunk_number: int) -> pathlib.Path:
    return options.workdir.working / f'chunk_{chunk_number:04d}.pdf'


class IncrementalChunkAssembler:
    """Consume completed page PDFs in order while workers continue decoding."""

    def __init__(self, options: DpsPrepOptions, max_page: int) -> None:
        self.options = options
        self.max_page = max_page
        self.next_page = 0
        self.text_pdf: pdfrw.PdfReader | None = None
        self.total_chunks = (max_page + CHUNK_SIZE - 1) // CHUNK_SIZE
        self.completed_pages: set[int] = set()

    def mark_page_ready(self, page: int) -> None:
        self.completed_pages.add(page)

    def mark_text_ready(self) -> None:
        if not self.options.no_text and self.text_pdf is None:
            self.text_pdf = pdfrw.PdfReader(self.options.workdir.text_layer_pdf_path)

    def consume_ready(self) -> None:
        if not self.options.no_text and self.text_pdf is None:
            return
        while self.next_page < self.max_page:
            first_page = self.next_page
            last_page = min(first_page + CHUNK_SIZE, self.max_page)
            chunk_number = first_page // CHUNK_SIZE + 1
            chunk_path = _chunk_path(self.options, chunk_number)
            page_paths = [self.options.workdir.get_page_pdf_path(page) for page in range(first_page, last_page)]

            if not chunk_path.exists() or not is_valid_pdf(chunk_path, last_page - first_page):
                if not all(page in self.completed_pages for page in range(first_page, last_page)):
                    return
                with writing_checkpoint(chunk_path) as partial:
                    self._write_chunk(partial, first_page, last_page)
            for page_path in page_paths:
                page_path.unlink(missing_ok=True)

            self.next_page = last_page
            _report_combine_progress(chunk_number, self.total_chunks + 2)
            self._check_working_space()

    def _write_chunk(self, chunk_path: pathlib.Path, first_page: int, last_page: int) -> None:
        writer = pdfrw.PdfWriter()
        for page_number in range(first_page, last_page):
            image_pdf = pdfrw.PdfReader(self.options.workdir.get_page_pdf_path(page_number))
            image_page = image_pdf.pages[0]
            if self.options.no_text:
                writer.addpage(image_page)
            else:
                assert self.text_pdf is not None
                # PageMerge mutates the page and its resource dictionaries.
                # Keep image streams owned by this chunk, never by the cached
                # whole-book text reader or other pages sharing font resources.
                original = self.text_pdf.pages[page_number]
                text_page = pdfrw.PdfDict(original)
                text_page.Resources = pdfrw.PdfDict(original.inheritable.Resources or {})
                text_page.Resources.XObject = pdfrw.PdfDict(text_page.Resources.XObject or {})
                merger = pdfrw.PageMerge(text_page)
                merger.add(image_page).render()
                writer.addpage(text_page)
        writer.write(chunk_path)

    def _check_working_space(self) -> None:
        # Decoder processes can temporarily consume many GiB of swap on macOS.
        # Requiring room for the final merged copy at this point causes false
        # failures: the swap is released before QPDF starts.  The exact final
        # requirement is checked in _write_chunks_to_destination after all
        # workers have exited.
        free = shutil.disk_usage(self.options.workdir.working).free
        if free < DISK_SAFETY_BYTES:
            raise click.ClickException(
                'The working disk is almost full. '
                f'At least {DISK_SAFETY_BYTES / 1024**3:.1f} GiB must remain; '
                f'{free / 1024**3:.1f} GiB is available.',
            )


def _write_chunks_to_destination(
    chunks: list[pathlib.Path],
    destination: pathlib.Path,
    outline: pdfrw.IndirectPdfDict,
    progress_offset: int,
    expected_pages: int,
    text_reference: pathlib.Path | None,
) -> None:
    """Join assembled chunks with QPDF without retaining another work PDF.

    The standalone QPDF process has bounded memory usage. Once it has produced
    one merged PDF, chunks are removed before metadata is added, keeping peak
    disk use to about two copies of the result.
    """
    merged_path = chunks[0].parent / 'merged.pdf'
    argument_path = chunks[0].parent / 'qpdf-arguments.txt'
    if text_reference is not None:
        validate_chunk_text(chunks, text_reference, expected_pages, CHUNK_SIZE)
    required = sum(chunk.stat().st_size for chunk in chunks) + DISK_SAFETY_BYTES
    free = shutil.disk_usage(merged_path.parent).free
    if free < required:
        raise click.ClickException(
            f'Not enough free space to merge PDF blocks: need {required / 1024**3:.1f} GiB, '
            f'available {free / 1024**3:.1f} GiB.',
        )
    _report_combine_progress(progress_offset, progress_offset + 2)
    if not merged_path.exists():
        with writing_checkpoint(merged_path) as partial:
            merge_pdfs(chunks, partial, argument_path)
        for chunk in chunks:
            chunk.unlink(missing_ok=True)
    _report_combine_progress(progress_offset + 1, progress_offset + 2)

    _finalize_merged_pdf(merged_path, destination, outline, expected_pages)
    _report_combine_progress(progress_offset + 2, progress_offset + 2)


def _finalize_merged_pdf(
    merged_path: pathlib.Path,
    destination: pathlib.Path,
    outline: pdfrw.IndirectPdfDict,
    expected_pages: int,
) -> None:

    # No metadata means no second serialization at all. This is the common
    # path for large scanned books and the one used by the 6,236-page fixture.
    if outline.First is None:
        validate_output(
            merged_path,
            expected_pages=expected_pages,
        )
        try:
            merged_path.replace(destination)
        except OSError as error:
            if error.errno != errno.EXDEV:
                raise
        else:
            return

    with tempfile.NamedTemporaryFile(dir=destination.parent, suffix='.pdf', delete=False) as handle:
        pending = pathlib.Path(handle.name)
    try:
        # Opening one already merged PDF is lazy in QPDF. We modify only the
        # outline tree; page streams are copied by QPDF rather than imported
        # from thousands of independent Python PDF objects.
        if outline.First is None:
            shutil.copyfile(merged_path, pending)
        else:
            with pikepdf.open(merged_path) as output_pdf:
                with output_pdf.open_outline() as output_outline:
                    output_outline.root.extend(_convert_outline_items(outline.First))
                output_pdf.save(pending)
            validate_output(pending, expected_pages=expected_pages)
        pending.replace(destination)
    finally:
        pending.unlink(missing_ok=True)
    merged_path.unlink(missing_ok=True)


def _convert_outline_items(node: pdfrw.PdfDict | None) -> list[pikepdf.OutlineItem]:
    """Translate the existing DjVu-derived outline into QPDF bookmarks."""
    items: list[pikepdf.OutlineItem] = []
    while node is not None:
        title = PdfString.decode(node.Title) if isinstance(node.Title, PdfString) else str(node.Title or '')
        destination = int(node.A.D[0]) if node.A is not None and node.A.D is not None else None
        item = pikepdf.OutlineItem(title, destination, page_location='Fit')
        item.children.extend(_convert_outline_items(node.First))
        items.append(item)
        node = node.Next
    return items


def _stream_pages_in_chunks(
    options: DpsPrepOptions,
    outline: pdfrw.IndirectPdfDict,
    max_page: int,
    *,
    with_text: bool,
) -> None:
    """Build the final PDF in batches, releasing rendered page PDFs early."""
    total_chunks = (max_page + CHUNK_SIZE - 1) // CHUNK_SIZE
    if options.workdir.merged_pdf_path.exists():
        _finalize_merged_pdf(
            options.workdir.merged_pdf_path,
            options.workdir.dest,
            outline,
            max_page,
        )
        return
    assembler = IncrementalChunkAssembler(options, max_page)
    if with_text:
        assembler.mark_text_ready()
    for page in range(max_page):
        if options.workdir.get_page_pdf_path(page).exists():
            assembler.mark_page_ready(page)
    assembler.consume_ready()
    if assembler.next_page != max_page:
        raise RuntimeError(f'Only {assembler.next_page} of {max_page} pages were assembled into chunks.')
    chunks = [_chunk_path(options, number) for number in range(1, total_chunks + 1)]

    _write_chunks_to_destination(
        chunks,
        options.workdir.dest,
        outline,
        total_chunks,
        max_page,
        options.workdir.text_layer_pdf_path if with_text else None,
    )

    for chunk in chunks:
        chunk.unlink(missing_ok=True)


def combine_pdfs_on_fs_with_text(options: DpsPrepOptions, outline: pdfrw.IndirectPdfDict, page_count: int | None = None) -> None:
    if options.stream_to_dest:
        if page_count is None:
            with pikepdf.open(options.workdir.text_layer_pdf_path) as text_pdf:
                page_count = len(text_pdf.pages)
        _stream_pages_in_chunks(options, outline, page_count, with_text=True)
        return

    text_pdf = pdfrw.PdfReader(options.workdir.text_layer_pdf_path)
    writer = pdfrw.PdfWriter()

    for i, text_page in enumerate(text_pdf.pages):
        # We take the one-page text PDF and add the image layer on top
        # Even if the font was not invisible, it would be hidden visually (but not during search or text highlight)
        image_pdf = pdfrw.PdfReader(options.workdir.get_page_pdf_path(i))
        image_page = image_pdf.pages[0]
        merger = pdfrw.PageMerge(text_page)
        merger.add(image_page).render()
        writer.addpage(text_page)

    _attach_legacy_outline(writer, outline)
    writer.write(options.workdir.combined_pdf_path)


def combine_pdfs_on_fs_without_text(options: DpsPrepOptions, outline: pdfrw.IndirectPdfDict, max_page: int) -> None:
    if options.stream_to_dest:
        _stream_pages_in_chunks(options, outline, max_page, with_text=False)
        return

    writer = pdfrw.PdfWriter()

    for i in range(max_page):
        image_pdf = pdfrw.PdfReader(options.workdir.get_page_pdf_path(i))
        image_page = image_pdf.pages[0]
        writer.addpage(image_page)

    _attach_legacy_outline(writer, outline)
    writer.write(options.workdir.combined_pdf_without_text_path)


def _attach_legacy_outline(writer: pdfrw.PdfWriter, outline: pdfrw.IndirectPdfDict) -> None:
    # In a local GoTo action, PDF requires an indirect page reference. The
    # streaming writer resolves these same zero-based indices via pikepdf.
    node = outline.First
    while node is not None:
        if node.A is not None:
            node.A.D[0] = writer.pagearray[node.A.D[0]]
        if node.First is not None:
            node = node.First
            continue
        while node is not outline and node.Next is None:
            node = node.Parent
        node = node.Next
    if outline.First is not None:
        outline.Type = pdfrw.PdfName.Outlines
        writer.trailer.Root.Outlines = outline
