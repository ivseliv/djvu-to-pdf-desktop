"""Generate many distinct PDF blocks and stress the external QPDF merger."""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import time
from pathlib import Path

import pikepdf

from dpsprep.qpdf import merge_pdfs, validate_output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--pages', type=int, default=6500)
    parser.add_argument('--blocks', type=int, default=200)
    parser.add_argument('--payload-kib', type=int, default=16)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    block_paths: list[Path] = []
    base, remainder = divmod(args.pages, args.blocks)
    page_number = 0
    for block_number in range(args.blocks):
        block_path = args.output_dir / f'chunk_{block_number + 1:04d}.pdf'
        with pikepdf.Pdf.new() as pdf:
            for _ in range(base + (1 if block_number < remainder else 0)):
                page = pdf.add_blank_page(page_size=(612, 792))
                seed = hashlib.sha256(f'{block_number}:{page_number}'.encode()).digest()
                # A deterministic high-entropy PDF comment avoids QPDF
                # deduplicating or compressing the stress payload away.
                payload = b'% unique stress payload\n%'
                payload += hashlib.shake_256(seed).hexdigest(args.payload_kib * 512).encode()
                payload += b'\n'
                page.Contents = pdf.make_stream(payload)
                page_number += 1
            pdf.save(block_path, compress_streams=False)
        block_paths.append(block_path)

    destination = args.output_dir / 'stress-output.pdf'
    started = time.monotonic()
    merge_pdfs(block_paths, destination, args.output_dir / 'qpdf-arguments.txt')
    validate_output(destination, args.pages)
    elapsed = time.monotonic() - started
    maximum_rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    report = {
        'pages': args.pages,
        'blocks': args.blocks,
        'payload_kib': args.payload_kib,
        'output_bytes': destination.stat().st_size,
        'elapsed_seconds': round(elapsed, 3),
        'child_max_rss_bytes': maximum_rss,
    }
    print(json.dumps(report, indent=2))
    if maximum_rss > 256 * 1024**2:
        raise SystemExit(f'QPDF exceeded 256 MiB RSS: {maximum_rss}')


if __name__ == '__main__':
    main()
