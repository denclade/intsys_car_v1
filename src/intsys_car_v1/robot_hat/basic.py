# -*- coding: utf-8 -*-
#!/usr/bin/env python3
import logging


class _Basic_class(object):
    """
    Basic Class for all classes

    with debug function
    """
    _class_name = '_Basic_class'
    DEBUG_LEVELS = {'debug': logging.DEBUG,
                    'info': logging.INFO,
                    'warning': logging.WARNING,
                    'error': logging.ERROR,
                    'critical': logging.CRITICAL,
                    }
    """Debug level"""
    DEBUG_NAMES = ['critical', 'error', 'warning', 'info', 'debug']
    """Debug level names"""

    def __init__(self, debug_level='warning'):
        """
        Initialize the basic class

        :param debug_level: debug level, 0(critical), 1(error), 2(warning), 3(info) or 4(debug)
        :type debug_level: str/int
        """
        # One logger per class (not per object, which leaked a logger and handler each time).
        self.logger = logging.getLogger(f"intsys_car_v1.{type(self).__name__}")
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(asctime)s	[%(levelname)s]	%(message)s"))
            self.logger.addHandler(handler)
        self.ch = self.logger.handlers[0]
        self._debug = self.logger.debug
        self._info = self.logger.info
        self._warning = self.logger.warning
        self._error = self.logger.error
        self._critical = self.logger.critical
        self.debug_level = debug_level

    @property
    def debug_level(self):
        """Debug level"""
        return self._debug_level

    @debug_level.setter
    def debug_level(self, debug):
        """Debug level"""
        if debug in range(5):
            self._debug_level = self.DEBUG_NAMES[debug]
        elif debug in self.DEBUG_NAMES:
            self._debug_level = debug
        else:
            raise ValueError(
                f'Debug value must be 0(critical), 1(error), 2(warning), 3(info) or 4(debug), not "{debug}".')
        self.logger.setLevel(self.DEBUG_LEVELS[self._debug_level])
        self.ch.setLevel(self.DEBUG_LEVELS[self._debug_level])
        self._debug(f'Set logging level to [{self._debug_level}]')
