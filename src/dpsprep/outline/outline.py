import logging
from urllib.parse import unquote

from pdfrw import IndirectPdfDict, PdfDict, PdfName

from dpsprep import djvu_cli as decode
from dpsprep import djvu_sexpr as sexpr

from .sexpr_visitor import SExpressionVisitor


logger = logging.getLogger(__name__)


# Based on
# https://github.com/pmaupin/pdfrw/issues/52#issuecomment-271190546
class OutlineTransformVisitor(SExpressionVisitor[PdfDict]):
    def __init__(self, document: decode.Document | None = None) -> None:
        self.document = document

    def _page_index(self, target: str) -> int | None:
        if not target.startswith('#'):
            return None
        fragment = target[1:]
        if fragment.isascii() and fragment.isdecimal():
            index = int(fragment) - 1
            if index >= 0 and (self.document is None or index < len(self.document.pages)):
                return index
        if self.document is None:
            return None
        names = self.document.page_names
        return names.get(fragment, names.get(unquote(fragment)))

    def visit_plain_list(self, node: sexpr.ListExpression, parent: IndirectPdfDict) -> PdfDict:
        if len(node) < 2 or not isinstance(node[0], sexpr.StringExpression) or not isinstance(node[1], sexpr.StringExpression):
            return None
        title, page, *rest = node
        try:
            page_number = self._page_index(page.value)
        except UnicodeDecodeError:
            page_number = None

        try:
            title_text = title.value
        except UnicodeDecodeError:
            logger.warning(f'Could not decode page title {title!r}; leaving it in escaped form.')
            title_text = str(title)

        bookmark = IndirectPdfDict(Parent=parent, Title=title_text)
        if page_number is not None:
            bookmark.A = PdfDict(D=[page_number, PdfName.Fit], S=PdfName.GoTo)
        for child in rest:
            self.visit(child, parent=bookmark)
        if page_number is None and bookmark.First is None:
            logger.warning(f'Skipping bookmark with unresolved destination {page}.')
            return None

        if parent.Count is None:
            parent.Count = 0
            parent.First = bookmark
        else:
            bookmark.Prev = parent.Last
            bookmark.Prev.Next = bookmark

        parent.Count += 1 + (bookmark.Count or 0)
        parent.Last = bookmark

        return bookmark

    def visit_list_bookmarks(self, node: sexpr.ListExpression) -> PdfDict:
        _, *rest = node

        outline = IndirectPdfDict()

        for child in rest:
            self.visit(child, parent=outline)

        return outline


def extract_outline_as_pdfdict(document: decode.Document) -> PdfDict:
    return OutlineTransformVisitor(document).visit(document.outline)
