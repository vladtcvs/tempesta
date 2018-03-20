import unittest
from helpers import shell, remote

TEST_DISABLED = "disabled"
TEST_PENDING = "pending"
TEST_SUCCESS = "success"
TEST_FAIL = "fail"
TEST_ERROR = "error"

class TempestaTestResult(unittest.TextTestResult):
    matcher = None

    def addError(self, test, err):
        unittest.TextTestResult.addError(self, test, err)

    def addExpectedFailure(self, test, err):
        unittest.TextTestResult.addExpectedFailure(self, test, err)

    def addFailure(self, test, err):
        unittest.TextTestResult.addFailure(self, test, err)

    def addSkip(self, test, reason):
        unittest.TextTestResult.addSkip(self, test, reason)

    def addSuccess(self, test):
        unittest.TextTestResult.addSuccess(self, test)

    def addUnexpectedSuccess(self, test):
        unittest.TextTestResult.addUnexpectedSuccess(self, test)

class TempestaTest(object):
    test = None
    state = TEST_DISABLED
    message = ""
    name = ""

    def __init__(self, test):
        self.test = test
        self.name = test.id()
        self.reset()

    def reset(self):
        self.state = TEST_PENDING
        self.message = ""

    def disable(self):
        self.state = TEST_DISABLED
        self.message = "Test is disabled"

class TempestaTestEngine(object):

    def __init__(self, v_level=1):
        all_tests = []
        loader = unittest.TestLoader()
        shell.testsuite_flatten(all_tests, loader.discover('.'))
        self.v_level = v_level
        self.tests = [TempestaTest(t) for t in all_tests]
        remote.connect()

    def __resultclass(self):
        return type('Result', (TempestaTestResult,), {'matcher': self})

    def list_of_tests(self):
        return [(t.name, t.state != TEST_DISABLED) for t in self.tests]

    def list_of_enabled_tests(self):
        return [t.name for t in self.tests if t.state != TEST_DISABLED]

    def enable_test(self, name=""):
        for t in self.tests:
            if t.name.startswith(name):
                t.reset()

    def disable_test(self, name=""):
        for t in self.tests:
            if t.name.startswith(name):
                t.disable()

    def reset_enabled_tests(self):
        for t in self.tests:
            if t.state != TEST_DISABLED:
                t.reset()

    def run_tests(self):
        pending = [t.test for t in self.tests if t.state == TEST_PENDING]
        testsuite = unittest.TestSuite(pending)
        testRunner = unittest.runner.TextTestRunner(verbosity=self.v_level,
                                        resultclass=self.__resultclass())
        testRunner.run(testsuite)
