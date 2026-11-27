The visible contents of a DjVu file are well-compressed images (see [LeCun's project page](http://yann.lecun.com/ex/djvu/index.html)). But a DjVu file also contains a "text layer" stored as metadata attached to invisible rectangular blocks. PDF does not support such constructs, so we do a little hack.

The converter reads text and bounding boxes with `djvused` and renders the
page image with `ddjvu`. It embeds a [generated font](../scripts/build_text_font.py)
with blank glyphs and searchable Unicode mappings. Each word or text block is
positioned and scaled using its coordinates in the DjVu page.

The following screenshot displays the result of converting a DjVu document:

![Image](./screenshots/lipsum_with_image.png)

The following screenshot displays the same document without the background image and with the invisible font replaced by Times New Roman:

![Image](./screenshots/lipsum_with_text.png)

Since the image is actually drawn on top of the text, there is no harm in using an actual visible font, possibly rendered using a transparent "color". Still, when searching and selecting text, the scrambled letters from the second image would be highlighted. With the invisible font, there are no visible glyphs to highlight, so an illusory "block" containing the text is highlighted instead.

See [`text.py`](https://github.com/kcroker/dpsprep/blob/master/src/dpsprep/outline/text.py) for the implementation.
