"""pytest with a time limit on every test, so a test stuck in a loop fails in seconds, not after the whole run's limit.
Run from the project's folder:  python pytest_limit.py [pytest arguments]"""
import os
import sys

sys.path[0] = os.getcwd()   # as `python -m pytest` does: the project's folder, not this file's, so its modules are found first
import signal

import pytest


class TestTimeout(BaseException):   # not an Exception: code under test that catches Exception cannot swallow it
    __test__ = False


class Limit:
    def pytest_addoption(self, parser):
        parser.addoption("--test-limit", type=int, default=20, help="seconds one test may take")

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item):
        seconds = item.config.getoption("--test-limit")

        def stop(*_):
            raise TestTimeout(f"the test ran for more than {seconds}s: probably an infinite loop")

        signal.signal(signal.SIGALRM, stop)
        signal.alarm(seconds)
        yield
        signal.alarm(0)


sys.exit(pytest.main(sys.argv[1:], plugins=[Limit()]))
