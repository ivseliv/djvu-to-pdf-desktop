import logging
import pathlib
from typing import NamedTuple

import PIL.features
from PIL import Image

from dpsprep import djvu_cli as decode
from dpsprep.options import DpsPrepOptions, ImageMode


logger = logging.getLogger(__name__)


image_modes = {'RGB': ImageMode.RGB, 'L': ImageMode.GRAYSCALE, '1': ImageMode.BITONAL}


class ProcessedPageBackground(NamedTuple):
    pil_image: Image.Image
    resolution: int
    mode: ImageMode


def process_djvu_page(
    page: decode.Page,
    mode: ImageMode,
    max_dpi: int | None = None,
    dpi_override: int | None = None,
) -> ProcessedPageBackground:
    source_width, source_height = page.size
    source_dpi = dpi_override or page.dpi
    resolution = min(source_dpi, max_dpi) if max_dpi is not None else source_dpi
    scale = resolution / source_dpi
    width = max(1, round(source_width * scale))
    height = max(1, round(source_height * scale))
    image = page.render(mode, (width, height))
    mode = image_modes[image.mode]
    if mode == ImageMode.BITONAL:
        if not PIL.features.check_codec('libtiff'):
            logger.warning('Bitonal image compression may suffer because Pillow has been built without libtiff support.')
    elif not PIL.features.check_codec('jpg'):
        logger.warning('Multitonal image compression may suffer because Pillow has been built without libjpeg support.')

    return ProcessedPageBackground(image, resolution, mode)


def failsafe_save_djvu_page(page_bg: ProcessedPageBackground, target: pathlib.Path, options: DpsPrepOptions, i: int) -> None:
    quality = options.quality_overrides.get_value_for_zero_based_page(i)
    dpi = page_bg.resolution

    # JPEG quality is intentionally irrelevant for one-bit pages. Passing it
    # to Pillow's TIFF-backed PDF encoder raises ValueError and used to emit
    # two warnings for every bitonal page in large books.
    if page_bg.mode == ImageMode.BITONAL:
        quality = None

    if quality is not None:
        try:
            page_bg.pil_image.save(
                target,
                format='PDF',
                quality=quality,
                resolution=dpi,
            )
        except ValueError:
            logger.warning(f'Failed to encode page {i + 1}. Trying again without setting quality.')
        else:
            return

    page_bg.pil_image.save(
        target,
        format='PDF',
        resolution=dpi,
    )
