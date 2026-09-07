# Due to some compatibility issues, we only support multiprocessing-based concurrency with explicit message passing.
# This is discussed in the concurrency notes in the project's wiki.

import logging
import multiprocessing
import os
from multiprocessing.pool import Pool
from typing import TYPE_CHECKING

import djvu.decode
from rich.progress import Progress, TaskID

from dpsprep.exceptions import DpsPrepConcurrencyError
from dpsprep.options import DpsPrepOptions
from dpsprep.pdf import CHUNK_SIZE, IncrementalChunkAssembler

from .counter import PageCounter
from .message import (
    ExceptionWorkerMessage,
    ImagePageProcessedMessage,
    LogRecordWorkerMessage,
    TextLayerAlreadyProcessedMessage,
    TextLayerFinishedMessage,
    TextPageProcessedMessage,
)
from .worker import SubprocessWorker


if TYPE_CHECKING:
    from multiprocessing.connection import Connection

    from .message import WorkerMessage


logger = logging.getLogger(__name__)
PROGRESS_PREFIX = '__DPSPREP_PROGRESS__ '
TEXT_PROGRESS_PREFIX = '__DPSPREP_TEXT_PROGRESS__ '


class SubprocessPageProcessor:
    options: DpsPrepOptions
    document: djvu.decode.Document

    parent_conn: 'Connection[WorkerMessage]'
    child_conn: 'Connection[WorkerMessage]'
    pool: Pool

    rich_progress: Progress
    rich_task: TaskID

    def __init__(self, options: DpsPrepOptions, document: djvu.decode.Document) -> None:
        self.options = options
        self.document = document

        self.parent_conn, self.child_conn = multiprocessing.Pipe()
        self.pool = Pool(processes=self.options.pool_size)

    def on_child_error(self, err: BaseException | None) -> None:
        if err:
            logger.exception('Worker error.', exc_info=err)
            self.child_conn.send(ExceptionWorkerMessage(err))

    # ruff: ignore[complex-structure]
    def process(self) -> None:
        worker = SubprocessWorker(self.options, self.child_conn)
        counter = PageCounter(len(self.document.pages))
        if self.options.no_text:
            for page in range(counter.total):
                counter.text[page] = True

        rich_progress = Progress()
        rich_task = rich_progress.add_task('Processing pages', total=counter.total)
        report_progress = os.environ.get('DPSPREP_GUI_PROGRESS') == '1'
        assembler = IncrementalChunkAssembler(self.options, counter.total) if self.options.stream_to_dest else None
        if assembler is not None and (self.options.no_text or self.options.workdir.text_layer_pdf_path.exists()):
            if not self.options.no_text:
                assembler.mark_text_ready()
            # Resume from already completed chunks before workers receive
            # their initial page window. Otherwise a restarted job needlessly
            # renders its first pages again while text recovery is starting.
            assembler.consume_ready()
        manager = multiprocessing.Manager() if assembler is not None else None
        page_limit = (
            manager.Value('i', min(counter.total, assembler.next_page + CHUNK_SIZE * 2))
            if manager is not None and assembler is not None
            else None
        )
        resume_page = assembler.next_page if assembler is not None else 0
        for page in range(resume_page):
            counter.images[page] = True
            counter.text[page] = True
        if resume_page:
            rich_progress.update(rich_task, completed=resume_page)
        workers_joined = False

        def advance_progress() -> None:
            rich_progress.advance(rich_task)
            if report_progress:
                completed = int(rich_progress.tasks[rich_task].completed)
                print(f'{PROGRESS_PREFIX}{completed}/{counter.total}', flush=True)

        try:  # ruff: ignore[too-many-nested-blocks]
            with rich_progress:
                if not self.options.no_text:
                    self.pool.apply_async(
                        worker.process_text,
                        error_callback=self.on_child_error,
                    )

                for worker_id in range(self.options.pool_size):
                    self.pool.apply_async(
                        worker.process_page_bg, [worker_id, page_limit, resume_page],
                        error_callback=self.on_child_error,
                    )

                self.pool.close()

                while not rich_progress.finished:
                    # ruff: ignore[too-many-statements-in-try-clause]
                    try:
                        if self.parent_conn.poll(0.1):
                            match message := self.parent_conn.recv():
                                case ExceptionWorkerMessage():
                                    # ruff: ignore[raise-within-try]
                                    raise DpsPrepConcurrencyError('Worker error') from message.error

                                case LogRecordWorkerMessage():
                                    logger.handle(message.record)

                                case ImagePageProcessedMessage():
                                    counter.images[message.page] = True

                                    if assembler is not None:
                                        assembler.mark_page_ready(message.page)
                                        assembler.consume_ready()
                                        if page_limit is not None:
                                            page_limit.value = min(counter.total, assembler.next_page + CHUNK_SIZE * 2)

                                    if counter.text[message.page]:
                                        advance_progress()

                                case TextPageProcessedMessage():
                                    was_processed = counter.text[message.page]
                                    counter.text[message.page] = True
                                    if report_progress:
                                        print(f'{TEXT_PROGRESS_PREFIX}{message.page + 1}/{counter.total}', flush=True)

                                    if counter.images[message.page] and not was_processed:
                                        advance_progress()

                                case TextLayerAlreadyProcessedMessage():
                                    if report_progress:
                                        print(f'{TEXT_PROGRESS_PREFIX}{counter.total}/{counter.total}', flush=True)
                                    for page in range(counter.total):
                                        was_processed = counter.text[page]
                                        counter.text[page] = True

                                        if counter.images[page] and not was_processed:
                                            advance_progress()

                                case TextLayerFinishedMessage():
                                    if assembler is not None:
                                        assembler.mark_text_ready()
                                        assembler.consume_ready()
                                        if page_limit is not None:
                                            page_limit.value = min(counter.total, assembler.next_page + CHUNK_SIZE * 2)

                    except KeyboardInterrupt:
                        logger.info('Conversion interrupted. Terminating all workers.')
                        raise

                    except DpsPrepConcurrencyError:
                        logger.info('Terminating all other workers.')
                        raise

                self.pool.join()
                workers_joined = True
                if assembler is not None:
                    assembler.consume_ready()
        finally:
            # No worker may still be writing into the working directory when
            # the CLI's outer cleanup runs.  This also covers disk-space and
            # QPDF errors raised outside the worker-message error path.
            if not workers_joined:
                self.pool.terminate()
                self.pool.join()
            if manager is not None:
                manager.shutdown()
