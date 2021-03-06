"""Functional tests of caching responses."""

from __future__ import print_function
from testers import functional
from helpers import chains

__author__ = 'Tempesta Technologies, Inc.'
__copyright__ = 'Copyright (C) 2017 Tempesta Technologies, Inc.'
__license__ = 'GPL2'

# TODO: add tests for RFC compliance

class TestCacheDisabled(functional.FunctionalTest):

    messages = 10

    # Disable caching
    cache_mode = 0

    def chain(self, uri='/', cache_alowed=True):
        if self.cache_mode == 0:
            cache_alowed = False
        if cache_alowed:
            return chains.cache_repeated(self.messages, uri=uri)
        return chains.proxy_repeated(self.messages, uri=uri)

    def test_cache_fulfill_all(self):
        config = ('cache %d;\n'
                  'cache_fulfill * *;\n' % self.cache_mode)
        self.generic_test_routine(config, self.chain(cache_alowed=True))

    def test_cache_bypass_all(self):
        config = ('cache %d;\n'
                  'cache_bypass * *;\n' % self.cache_mode)
        self.generic_test_routine(config, self.chain(cache_alowed=False))

    def mixed_config(self):
        return ('cache %d;\n'
                'cache_fulfill suffix ".jpg" ".png";\n'
                'cache_bypass suffix ".avi";\n'
                'cache_bypass prefix "/static/dynamic_zone/";\n'
                'cache_fulfill prefix "/static/";\n'
                % self.cache_mode)

    def test_cache_fulfill_suffix(self):
        self.generic_test_routine(
            self.mixed_config(),
            self.chain(cache_alowed=True, uri='/picts/bear.jpg'))

    def test_cache_fulfill_suffix_2(self):
        self.generic_test_routine(
            self.mixed_config(),
            self.chain(cache_alowed=True, uri='/jsnfsjk/jnd.png'))

    def test_cache_bypass_suffix(self):
        self.generic_test_routine(
            self.mixed_config(),
            self.chain(cache_alowed=False, uri='/howto/film.avi'))

    def test_cache_bypass_prefix(self):
        self.generic_test_routine(
            self.mixed_config(),
            self.chain(cache_alowed=False,
                       uri='/static/dynamic_zone/content.html'))

    def test_cache_fulfill_prefix(self):
        self.generic_test_routine(
            self.mixed_config(),
            self.chain(cache_alowed=True, uri='/static/content.html'))


class TestCacheSharding(TestCacheDisabled):

    # Sharding mode.
    cache_mode = 1

class TestCacheReplicated(TestCacheDisabled):

    # Replicated mode.
    cache_mode = 2

# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
