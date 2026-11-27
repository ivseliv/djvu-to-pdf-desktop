import logging
import multiprocessing
import os
from collections.abc import Generator
from contextlib import closing
from multiprocessing.connection import Connection, wait
from typing import cast

import djvu.decode
from rich.progress import Progress

from dpsprep.exceptions import DpsPrepConcurrencyError
from dpsprep.options import DpsPrepOptions
from dpsprep.pdf import CHUNK_SIZE, IncrementalChunkAssembler, is_valid_pdf

from .counter import PageCounter
from .message import (
    ImagePageProcessedMessage,
    LogRecordWorkerMessage,
    TextLayerAlreadyProcessedMessage,
    TextLayerFinishedMessage,
    TextPageProcessedMessage,
    WorkerMessage,
)
from .worker import SharedPageLimit, SubprocessWorker


logger = logging.getLogger(__name__)
PROGRESS_PREFIX = '__DPSPREP_PROGRESS__ '
TEXT_PROGRESS_PREFIX = '__DPSPREP_TEXT_PROGRESS__ '


class SubprocessPageProcessor:
    def __init__(self, options: DpsPrepOptions, document: djvu.decode.Document) -> None:
        self.options = options
        self.document = document

    def _worker_messages(self, page_limit: SharedPageLimit | None, resume_page: int) -> Generator[WorkerMessage, None, None]:
        # One writer per pipe prevents concurrent large log messages from
        # corrupting one another. EOF also lets us detect a native worker crash.
        readers: dict[Connection, multiprocessing.Process] = {}
        try:
            for worker_id in range(min(self.options.pool_size, len(self.document.pages))):
                reader, writer = multiprocessing.Pipe(duplex=False)
                worker = SubprocessWorker(self.options, writer)
                process = multiprocessing.Process(target=worker.process_stripe, args=(worker_id, page_limit, resume_page))
                try:
                    process.start()
                except BaseException:
                    reader.close()
                    raise
                finally:
                    writer.close()
                readers[reader] = process

            while readers:
                for reader in cast('list[Connection]', wait(readers)):
                    try:
                        message = reader.recv()
                    except EOFError:
                        process = readers.pop(reader)
                        reader.close()
                        process.join()
                        if process.exitcode:
                            raise DpsPrepConcurrencyError(f'Worker {process.pid} exited with code {process.exitcode}.') from None
                    else:
                        yield message
        finally:
            for reader, process in readers.items():
                if process.is_alive():
                    process.terminate()
                process.join()
                reader.close()

    # ruff: ignore[complex-structure]
    def process(self) -> None:
        counter = PageCounter(len(self.document.pages))
        report_progress = os.environ.get('DPSPREP_GUI_PROGRESS') == '1'
        assembler = IncrementalChunkAssembler(self.options, counter.total) if self.options.stream_to_dest else None
        if assembler is not None:
            if not self.options.no_text and is_valid_pdf(self.options.workdir.text_layer_pdf_path, counter.total):
                assembler.mark_text_ready()
            assembler.consume_ready()
        resume_page = assembler.next_page if assembler is not None else 0
        if resume_page == counter.total:
            return
        page_limit = multiprocessing.Value('i', min(counter.total, resume_page + CHUNK_SIZE * 2)) if assembler else None
        for page in range(counter.total):
            counter.text[page] = self.options.no_text or page < resume_page
            counter.images[page] = page < resume_page

        with Progress() as progress, closing(self._worker_messages(page_limit, resume_page)) as messages:
            task = progress.add_task('Processing pages', total=counter.total, completed=resume_page)

            def advance_progress() -> None:
                progress.advance(task)
                if report_progress:
                    completed = int(progress.tasks[task].completed)
                    print(f'{PROGRESS_PREFIX}{completed}/{counter.total}', flush=True)

            for message in messages:
                match message:
                    case LogRecordWorkerMessage():
                        logger.handle(message.record)
                    case ImagePageProcessedMessage():
                        counter.images[message.page] = True
                        if counter.text[message.page]:
                            advance_progress()
                        if assembler is not None:
                            assembler.mark_page_ready(message.page)
                    case TextPageProcessedMessage():
                        if counter.images[message.page] and not counter.text[message.page]:
                            advance_progress()
                        counter.text[message.page] = True
                        if report_progress:
                            print(f'{TEXT_PROGRESS_PREFIX}{message.page + 1}/{counter.total}', flush=True)
                    case TextLayerAlreadyProcessedMessage():
                        if report_progress:
                            print(f'{TEXT_PROGRESS_PREFIX}{counter.total}/{counter.total}', flush=True)
                        for page in range(counter.total):
                            if counter.images[page] and not counter.text[page]:
                                advance_progress()
                            counter.text[page] = True
                    case TextLayerFinishedMessage():
                        if assembler is not None:
                            assembler.mark_text_ready()
                if assembler is not None and isinstance(message, (ImagePageProcessedMessage, TextLayerFinishedMessage)):
                    assembler.consume_ready()
                    if page_limit is not None:
                        page_limit.value = min(counter.total, assembler.next_page + CHUNK_SIZE * 2)

            if not progress.finished:
                raise DpsPrepConcurrencyError('Page workers stopped before processing all pages.')
