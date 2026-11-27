# ruff: file-ignore[no-self-use, unused-unpacked-variable]

import logging
import unicodedata
from collections.abc import Iterable
from contextlib import nullcontext
from importlib import resources
from typing import Protocol

from fpdf import FPDF
from fpdf.drawing_primitives import Transform

from dpsprep import djvu_cli as decode
from dpsprep import djvu_sexpr as sexpr
from dpsprep.options import DpsPrepOptions

from .sexpr_visitor import SExpressionVisitor


BASE_FONT_SIZE = 10
TAB_SIZE = 4
logger = logging.getLogger(__name__)


class TextExtractVisitor(SExpressionVisitor[str]):
    def iter_chars(self, string: str) -> Iterable[str]:
        for char in string:
            code = unicodedata.category(char)

            # Line Separator (Zl) | Space Separator (Zs)
            if code in {'Zl', 'Zs'}:
                yield ' '

            # Paragraph Separator (Zp)
            elif code == 'Zp':
                yield '\n'

            # Control (Cc)
            elif code == 'Cc':
                if char == '\t':
                    yield ' ' * TAB_SIZE
                elif char == '\n':
                    yield ' '

            # These break FPDF.
            # A full list of categories can be found in https://www.compart.com/en/unicode/category
            # Format (Cf) | Surrogate (Cs):
            elif code in {'Cf', 'Cs'}:
                pass

            else:
                yield char

    def visit_string(self, node: sexpr.StringExpression) -> str:
        try:
            string = node.value  # This getter is not static - it does UTF-8 conversion and fails for some DjVu files
        except ValueError as err:
            logger.warning(f'Could not decode {node!r}: {err}')
            return ''
        else:
            return ''.join(self.iter_chars(string))

    # ruff: ignore[unused-method-argument]
    def visit_plain_list(self, node: sexpr.ListExpression) -> str:
        return ''

    def visit_list_word(self, node: sexpr.ListExpression) -> str | None:
        _, x1, y1, x2, y2, *children = node
        return ''.join(self.visit(child) or '' for child in children)

    visit_list_char = visit_list_word

    def visit_list_line(self, node: sexpr.ListExpression) -> str:
        _, x1, y1, x2, y2, *rest = node
        return ' '.join(self.visit(child) or '' for child in rest)

    def visit_list_para(self, node: sexpr.ListExpression) -> str:
        _, x1, y1, x2, y2, *rest = node
        return '\n'.join(self.visit(child) or '' for child in rest)

    visit_list_column = visit_list_para
    visit_list_region = visit_list_para
    visit_list_page = visit_list_para


class TextDrawVisitor(SExpressionVisitor):
    pdf: FPDF
    dpi: int
    extractor: TextExtractVisitor

    def __init__(self, pdf: FPDF, dpi: int, raw_height: int) -> None:
        self.pdf = pdf
        self.dpi = dpi
        self.raw_height = raw_height
        self.extractor = TextExtractVisitor()

    def draw_text(self, x1: int, x2: int, y1: int, y2: int, text: str) -> None:
        # text-layer.ttf has a 1000-unit em with an 800-unit ascender and
        # 200-unit descender. Fit the full selection rectangle to the TXT zone.
        height = (y2 - y1) / self.dpi
        self.pdf.set_font('Invisible', size=height * 72 if height > 0 else BASE_FONT_SIZE)
        self.pdf.set_stretching(100)
        desired_width = (x2 - x1) / self.dpi
        actual_width = self.pdf.get_string_width(text)

        if actual_width == 0:
            return

        if desired_width > 0:
            self.pdf.set_stretching(100 * desired_width / actual_width)
        baseline = (self.raw_height - y1) / self.dpi - 0.2 * max(0, height)

        try:
            self.pdf.text(x=x1 / self.dpi, y=baseline, text=text)
        except TypeError as err:
            logger.warning(f'FPDF refuses to draw {text!r}: {err}')

    def iter_loose_string_content(self, expressions: list[sexpr.Expression]) -> Iterable[str]:
        for child in expressions:
            if not isinstance(child, sexpr.StringExpression):
                continue

            if (text := self.extractor.visit(child)) is not None:
                yield text

    def get_loose_string_content(self, expressions: list[sexpr.Expression], delimiter: str) -> str:
        return delimiter.join(self.iter_loose_string_content(expressions))

    def visit_list_word(self, node: sexpr.ListExpression) -> None:
        _, x1, y1, x2, y2, *rest = node
        text = self.extractor.visit(node)

        if text is not None:
            self.draw_text(x1.value, x2.value, y1.value, y2.value, text)

    visit_list_char = visit_list_word

    def visit_list_line(self, node: sexpr.ListExpression) -> None:
        _, x1, y1, x2, y2, *rest = node

        text = self.get_loose_string_content(rest, ' ')

        if len(text) > 0:
            self.draw_text(x1.value, x2.value, y1.value, y2.value, text)

        for child in rest:
            if not isinstance(child, sexpr.StringExpression):
                self.visit(child)

    def visit_list_para(self, node: sexpr.ListExpression) -> None:
        _, x1, y1, x2, y2, *rest = node

        text = self.get_loose_string_content(rest, '\n')

        if len(text) > 0:
            self.draw_text(x1.value, x2.value, y1.value, y2.value, text)

        for child in rest:
            if not isinstance(child, sexpr.StringExpression):
                self.visit(child)

    visit_list_column = visit_list_para
    visit_list_page = visit_list_para
    visit_list_region = visit_list_para

    def visit_plain_list(self, node: sexpr.ListExpression) -> None:
        if len(node) == 0:
            logger.debug('Ignoring empty list expression.')
        else:
            logger.debug(f'Ignoring unrecognized but nonempty list expression {node}.')

    def visit_string(self, _node: sexpr.StringExpression) -> None:
        logger.debug('Ignoring unpositioned text expression.')


class TextExtractionTracker(Protocol):
    def mark_page_as_processed(self, i: int) -> None:
        ...

    def mark_all_pages_as_processed(self) -> None:
        ...


def extract_text_as_fpdf(
    document: decode.Document,
    options: DpsPrepOptions,
    tracker: TextExtractionTracker | None = None,
) -> FPDF:
    pdf = FPDF(unit='in')

    with resources.as_file(resources.files('dpsprep') / 'text-layer.ttf') as fspath:
        pdf.add_font(
            family='Invisible',
            fname=fspath.as_posix(),
            style='',
        )

    for i, page in enumerate(document.pages):
        page_dpi = options.dpi_overrides.get_value_for_zero_based_page(i) or page.dpi

        # This annotation will get fixed in the next release after 2.8.7
        #   See https://github.com/py-pdf/fpdf2/commit/9dbd2da8c382f03bac7f23721d6dde9f130dd967
        pdf.add_page(format=(page.width / page_dpi, page.height / page_dpi))  # type: ignore[arg-type]

        logger.debug(f'Processing text for page {i + 1}.')
        # DjVu stores TXT zones in the unrotated page coordinate system.
        # ddjvu applies the display rotation to the raster automatically.
        raw_width, raw_height = page.raw_size
        transforms = {
            1: Transform(0, -1, 1, 0, 0, raw_width / page_dpi),
            2: Transform(-1, 0, 0, -1, raw_width / page_dpi, raw_height / page_dpi),
            3: Transform(0, 1, -1, 0, raw_height / page_dpi, 0),
        }
        with (pdf.transform(transforms[page.rotation]) if page.rotation else nullcontext()):
            TextDrawVisitor(pdf, page_dpi, raw_height).visit(page.text)

        if tracker:
            tracker.mark_page_as_processed(i)

    return pdf
