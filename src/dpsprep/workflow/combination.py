import logging
import shutil

import pdfrw

from dpsprep import djvu_cli as decode
from dpsprep.ocrmypdf_adapter import perform_ocr
from dpsprep.options import DpsPrepOptions
from dpsprep.outline import extract_outline_as_pdfdict
from dpsprep.pdf import combine_pdfs_on_fs_with_text, combine_pdfs_on_fs_without_text


logger = logging.getLogger(__name__)


def prepare_document_combination(
    document: decode.Document,
) -> tuple[pdfrw.IndirectPdfDict, int]:
    outline = pdfrw.IndirectPdfDict()

    if document.outline:
        logger.info('Processing metadata.')
        outline = extract_outline_as_pdfdict(document)
        logger.info('Metadata processed.')
    else:
        logger.info('No metadata to process.')

    return outline, len(document.pages)


def combine_prepared_document(
    options: DpsPrepOptions,
    outline: pdfrw.IndirectPdfDict,
    page_count: int,
) -> None:

    logger.info('Combining everything.')

    if options.no_text:
        combine_pdfs_on_fs_without_text(options, outline, page_count)

        # Streaming is a desktop-only path for conversions that do not use
        # OCR/optimization.  The PDF already lives at its final destination.
        if options.stream_to_dest:
            return

        ocr_success = False

        if options.ocr_options:
            logger.info('Performing OCR.')
            ocr_success = perform_ocr(options)
        else:
            logger.info('Skipping the text layer.')

        if not ocr_success:
            shutil.copy(
                options.workdir.combined_pdf_without_text_path,
                options.workdir.combined_pdf_path,
            )
    else:
        combine_pdfs_on_fs_with_text(options, outline, page_count)


def combine_document(options: DpsPrepOptions, document: decode.Document) -> None:
    """Compatibility wrapper for callers that do not need early DjVu release."""
    outline, page_count = prepare_document_combination(document)
    combine_prepared_document(options, outline, page_count)
