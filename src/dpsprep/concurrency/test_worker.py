from .worker import first_worker_page


def test_first_worker_page_skips_completed_chunks() -> None:
    assert [first_worker_page(worker, 4, 5184) for worker in range(4)] == [5184, 5185, 5186, 5187]


def test_first_worker_page_preserves_worker_striping() -> None:
    for resume_page in (0, 1, 63, 64, 5185):
        for worker in range(4):
            page = first_worker_page(worker, 4, resume_page)
            assert page >= resume_page
            assert page % 4 == worker
