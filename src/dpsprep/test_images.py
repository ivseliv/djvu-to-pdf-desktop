from PIL import Image, ImageChops, ImageStat

from dpsprep import djvu_cli as decode
from dpsprep.options import ImageMode

from .images import process_djvu_page


# A simple score function for Pillow images.
# We previously used the pytest-image-diff module, which used the diffimg module.
# It turned out that diffimg uses a similar approach, so we dropped the dependency in favor of a few-liner.
def calculate_image_diff_score(a: Image.Image, b: Image.Image) -> float:
    assert a.size == b.size, 'We only support diffing images with identical sizes'
    assert a.mode == b.mode, 'We only support diffing images with the same mode'

    diff = ImageChops.difference(a, b)
    stat = ImageStat.Stat(diff)
    return max(stat.rms) / 256  # The ImageStat module uses 256 bins


def test_process_djvu_page_bitonal() -> None:
    document = decode.Document('fixtures/lipsum_words.djvu')

    fixture = Image.open('fixtures/lipsum_01.png')
    result = process_djvu_page(document.pages[0], mode=ImageMode.INFER)

    assert result.resolution == document.pages[0].dpi

    assert calculate_image_diff_score(fixture, result.pil_image) < 0.05


def test_process_djvu_page_downsamples_without_changing_physical_size() -> None:
    document = decode.Document('fixtures/lipsum_words.djvu')
    page = document.pages[0]
    target_dpi = max(72, page.dpi // 2)

    result = process_djvu_page(
        document.pages[0],
        mode=ImageMode.INFER,
        max_dpi=target_dpi,
    )

    assert result.resolution == target_dpi
    assert result.pil_image.width == round(page.size[0] * target_dpi / page.dpi)
    assert result.pil_image.height == round(page.size[1] * target_dpi / page.dpi)
