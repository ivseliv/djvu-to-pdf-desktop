import pathlib
import os

import pdfrw
import pikepdf
from pdfrw.objects.pdfstring import PdfString

from dpsprep.options import DpsPrepOptions


def is_valid_pdf(path: pathlib.Path) -> bool:
    try:
        pdfrw.PdfReader(path)
    except pdfrw.errors.PdfParseError:
        return False
    else:
        return True


CHUNK_SIZE = 64
COMBINE_PROGRESS_PREFIX = '__DPSPREP_COMBINE_PROGRESS__ '


def _report_combine_progress(completed: int, total: int) -> None:
    if os.environ.get('DPSPREP_GUI_PROGRESS') == '1':
        print(f'{COMBINE_PROGRESS_PREFIX}{completed}/{total}', flush=True)


def _chunk_path(options: DpsPrepOptions, chunk_number: int) -> pathlib.Path:
    return options.workdir.working / f'chunk_{chunk_number:04d}.pdf'


def _write_chunks_to_destination(
    chunks: list[pathlib.Path], destination: pathlib.Path, outline: pdfrw.IndirectPdfDict, progress_offset: int
) -> None:
    """Join assembled chunks with QPDF without retaining another work PDF.

    pdfrw reserializes all indirect objects during the final merge and is not
    reliable for multi-gigabyte books. PikePDF is backed by QPDF and copies the
    page objects directly. This keeps peak disk use to the chunks plus one
    final output, instead of pages, a combined output, and a copied result.
    """
    with pikepdf.Pdf.new() as output_pdf:
        for chunk_number, chunk in enumerate(chunks, start=1):
            with pikepdf.open(chunk) as chunk_pdf:
                output_pdf.pages.extend(chunk_pdf.pages)
            _report_combine_progress(progress_offset + chunk_number, progress_offset + len(chunks))

        with output_pdf.open_outline() as output_outline:
            output_outline.root.extend(_convert_outline_items(outline.First))

        output_pdf.save(destination)


def _convert_outline_items(node: pdfrw.PdfDict | None) -> list[pikepdf.OutlineItem]:
    """Translate the existing DjVu-derived outline into QPDF bookmarks."""
    items: list[pikepdf.OutlineItem] = []
    while node is not None:
        title = PdfString.decode(node.Title) if node.Title is not None else ''
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
    text_pdf = pdfrw.PdfReader(options.workdir.text_layer_pdf_path) if with_text else None
    chunks: list[pathlib.Path] = []
    total_chunks = (max_page + CHUNK_SIZE - 1) // CHUNK_SIZE

    for first_page in range(0, max_page, CHUNK_SIZE):
        last_page = min(first_page + CHUNK_SIZE, max_page)
        chunk_path = _chunk_path(options, len(chunks) + 1)
        writer = pdfrw.PdfWriter()

        for page_number in range(first_page, last_page):
            image_path = options.workdir.get_page_pdf_path(page_number)
            image_pdf = pdfrw.PdfReader(image_path)
            image_page = image_pdf.pages[0]
            if with_text:
                assert text_pdf is not None
                text_page = text_pdf.pages[page_number]
                merger = pdfrw.PageMerge(text_page)
                merger.add(image_page).render()
                writer.addpage(text_page)
            else:
                writer.addpage(image_page)

        writer.write(chunk_path)
        # A completed chunk is self-contained, so these large rendered files
        # are no longer needed even if the conversion later fails.
        for page_number in range(first_page, last_page):
            options.workdir.get_page_pdf_path(page_number).unlink(missing_ok=True)
        chunks.append(chunk_path)
        _report_combine_progress(len(chunks), total_chunks * 2)

    _write_chunks_to_destination(chunks, options.workdir.dest, outline, total_chunks)

    for chunk in chunks:
        chunk.unlink(missing_ok=True)


def combine_pdfs_on_fs_with_text(options: DpsPrepOptions, outline: pdfrw.IndirectPdfDict) -> None:
    if options.stream_to_dest:
        text_pdf = pdfrw.PdfReader(options.workdir.text_layer_pdf_path)
        _stream_pages_in_chunks(options, outline, len(text_pdf.pages), with_text=True)
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

    writer.trailer.Root.Outlines = outline
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

    writer.trailer.Root.Outlines = outline
    writer.write(options.workdir.combined_pdf_without_text_path)
