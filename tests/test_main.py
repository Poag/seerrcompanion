import logging

import httpx

from seerrcompanion.main import safe_run


class Boom:
    def __init__(self, exc): self.exc = exc
    def run_once(self): raise self.exc


def _req():
    return httpx.Request("GET", "http://s/api/v1/request")


def test_transient_errors_are_one_line_warnings(caplog):
    with caplog.at_level(logging.WARNING):
        safe_run(Boom(httpx.ReadError("Connection reset by peer")))
        safe_run(Boom(httpx.HTTPStatusError("502", request=_req(), response=httpx.Response(502, request=_req()))))
    assert [r.levelno for r in caplog.records] == [logging.WARNING, logging.WARNING]
    assert all(r.exc_info is None for r in caplog.records)


def test_other_errors_keep_the_traceback(caplog):
    with caplog.at_level(logging.WARNING):
        safe_run(Boom(KeyError("x")))
        safe_run(Boom(httpx.HTTPStatusError("403", request=_req(), response=httpx.Response(403, request=_req()))))
    assert [r.levelno for r in caplog.records] == [logging.ERROR, logging.ERROR]
    assert all(r.exc_info for r in caplog.records)
