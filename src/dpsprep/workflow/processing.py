import logging
from time import time

import djvu.decode

from dpsprep.images import failsafe_save_djvu_page, process_djvu_page
from dpsprep.logging import human_readable_size
from dpsprep.options import DEFAULT_IMAGE_MODE, DpsPrepOptions
from dpsprep.outline import extract_text_as_fpdf
from dpsprep.outline.text import TextExtractionTracker
from dpsprep.pdf import is_valid_pdf
from dpsprep.workdir import writing_checkpoint


logger = logging.getLogger(__name__)


def process_page_bg(options: DpsPrepOptions, document: djvu.decode.Document, i: int) -> None:
    page_number = i + 1

    if options.workdir.get_page_pdf_path(i).exists():
        if is_valid_pdf(options.workdir.get_page_pdf_path(i), 1):
            logger.debug(f'Image data from page {page_number} already processed.')
            return
        logger.debug(f'Invalid page generated for {page_number}, regenerating.')
    else:
        logger.debug(f'Processing image data from page {page_number}.')

    start_time = time()
    mode = options.mode_overrides.get_value_for_zero_based_page(i) or DEFAULT_IMAGE_MODE
    dpi_override = options.dpi_overrides.get_value_for_zero_based_page(i)
    page_bg = process_djvu_page(document.pages[i], mode, i, max_dpi=options.max_dpi, dpi_override=dpi_override)
    with writing_checkpoint(options.workdir.get_page_pdf_path(i)) as partial:
        failsafe_save_djvu_page(page_bg, partial, options, i)
    pdf_size = options.workdir.get_page_pdf_path(i).stat().st_size

    message = (
        f'Processed and saved image data for page {page_number} in {time() - start_time:.2f}s. '
        f'The result has {page_bg.mode} mode, DPI {page_bg.resolution} and size {human_readable_size(pdf_size)}.'
    )

    logger.debug(message)


def process_text(
    options: DpsPrepOptions,
    document: djvu.decode.Document,
    tracker: TextExtractionTracker | None = None,
) -> None:
    if is_valid_pdf(options.workdir.text_layer_pdf_path, len(document.pages)):
        logger.info('Text data already processed.')

        if tracker:
            tracker.mark_all_pages_as_processed()

        return

    logger.debug('Processing text data.')

    start_time = time()
    fpdf = extract_text_as_fpdf(document, options, tracker)
    with writing_checkpoint(options.workdir.text_layer_pdf_path) as partial:
        fpdf.output(str(partial))

    pdf_size = options.workdir.text_layer_pdf_path.stat().st_size
    logger.info(f'Text data with size {human_readable_size(pdf_size) } processed in {time() - start_time:.2f}s and written to working directory')
