"""Generate the project's blank text-layer font using Python 3.11 (Unicode 14)."""

import unicodedata
from pathlib import Path

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen


def main() -> None:
    if unicodedata.unidata_version != '14.0.0':
        raise SystemExit('Use Python 3.11 to regenerate the checked-in font.')
    # fpdf2 preserves each original Unicode scalar in the PDF ToUnicode map.
    # Reuse outline-less glyphs across planes to stay below the TrueType limit.
    slots = [f'blank{i}' for i in range(4096)]
    order = ['.notdef', *slots]
    cmap = {
        cp: slots[cp % len(slots)] for cp in range(0x110000)
        if unicodedata.category(chr(cp)) not in {'Cn', 'Cs', 'Cc'}
    }
    font = FontBuilder(1000, isTTF=True)
    font.setupGlyphOrder(order)
    font.setupCharacterMap(cmap)
    font.setupGlyf({name: TTGlyphPen(None).glyph() for name in order})
    font.setupHorizontalMetrics(dict.fromkeys(order, (600, 0)))
    font.setupHorizontalHeader(ascent=800, descent=-200)
    font.setupNameTable({
        'familyName': 'DjVu PDF Text Layer', 'styleName': 'Regular',
        'uniqueFontIdentifier': 'DjVuPDFTextLayer-Regular-1',
        'fullName': 'DjVu PDF Text Layer Regular', 'psName': 'DjVuPDFTextLayer-Regular',
        'version': 'Version 1.0', 'licenseDescription': 'CC0-1.0',
    })
    font.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200, fsType=0)
    font.setupPost()
    font.font['head'].created = font.font['head'].modified = 3847392000
    font.font.recalcTimestamp = False
    font.save(Path(__file__).resolve().parents[1] / 'src/dpsprep/text-layer.ttf')


if __name__ == '__main__':
    main()
