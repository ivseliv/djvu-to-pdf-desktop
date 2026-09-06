import pathlib

import pdfrw

from dpsprep.options import DpsPrepOptions


def is_valid_pdf(path: pathlib.Path) -> bool:
    try:
        pdfrw.PdfReader(path)
    except pdfrw.errors.PdfParseError:
        return False
    else:
        return True


CHUNK_SIZE = 64


def _chunk_path(options: DpsPrepOptions, chunk_number: int) -> pathlib.Path:
    return options.workdir.working / f'chunk_{chunk_number:04d}.pdf'


def _write_chunks_to_destination(
    chunks: list[pathlib.Path], destination: pathlib.Path, outline: pdfrw.IndirectPdfDict
) -> None:
    """Join already assembled chunks without retaining an extra combined PDF.

    `pdfrw` writes a complete document at the end, so merging every page in a
    single writer makes the temporary page PDFs and the final PDF coexist.  By
    replacing a small batch of page PDFs with a chunk immediately, peak disk
    use is roughly the chunks plus the final output, rather than pages,
    combined output, and a copied destination all at once.
    """
    writer = pdfrw.PdfWriter()
    for chunk in chunks:
        chunk_pdf = pdfrw.PdfReader(chunk)
        for page in chunk_pdf.pages:
            writer.addpage(page)
    writer.trailer.Root.Outlines = outline
    writer.write(destination)


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

    _write_chunks_to_destination(chunks, options.workdir.dest, outline)

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
