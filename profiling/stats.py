# -*- coding: utf-8 -*-
"""
    profiling.stats
    ~~~~~~~~~~~~~~~

    Statistics classes.

"""
from __future__ import absolute_import, division
import inspect
from threading import RLock

from six import itervalues, with_metaclass

from .sortkeys import by_deep_time


__all__ = ['Statistics', 'RecordingStatistics', 'VoidRecordingStatistics',
           'FrozenStatistics']


def failure(funcname, message='{class} not allow {func}.', exctype=TypeError):
    """Generates a method which raises an exception."""
    def func(self, *args, **kwargs):
        fmtopts = {'func': funcname, 'obj': self, 'class': type(self).__name__}
        raise exctype(message.format(**fmtopts))
    func.__name__ = funcname
    return func


def stats_from_members(stats_class, members):
    stats = stats_class()
    for attr, value in zip(stats_class.__slots__, members):
        setattr(stats, attr, value)
    return stats


class default(object):

    __slots__ = ('value',)

    def __init__(self, value):
        self.value = value


class StatisticsMeta(type):

    def __new__(meta, name, bases, attrs):
        defaults = {}
        try:
            slots = attrs['__slots__']
        except KeyError:
            pass
        else:
            for attr in slots:
                if attr not in attrs:
                    continue
                elif isinstance(attrs[attr], default):
                    defaults[attr] = attrs.pop(attr).value
        cls = super(StatisticsMeta, meta).__new__(meta, name, bases, attrs)
        cls.__defaults__ = defaults
        return cls

    def __call__(cls, *args, **kwargs):
        obj = super(StatisticsMeta, cls).__call__(*args, **kwargs)
        for attr, value in cls.__defaults__.items():
            if not hasattr(obj, attr):
                setattr(obj, attr, value)
        return obj


class Statistics(with_metaclass(StatisticsMeta)):
    """Statistics of a function."""

    __slots__ = ('name', 'filename', 'lineno', 'module',
                 'own_count', 'deep_time')

    name = default(None)
    filename = default(None)
    lineno = default(None)
    module = default(None)
    #: The inclusive calling/sampling count.
    own_count = default(0)
    #: The exclusive execution time.
    deep_time = default(0.0)

    def __init__(self, **members):
        for attr, value in members.items():
            setattr(self, attr, value)

    @property
    def regular_name(self):
        name, module = self.name, self.module
        if name and module:
            return ':'.join([module, name])
        return name or module

    @property
    def deep_count(self):
        """The inclusive calling/sampling count.

        Calculates as sum of the own count and deep counts of the children.
        """
        return self.own_count + sum(stats.deep_count for stats in self)

    @property
    def own_time(self):
        """The exclusive execution time."""
        sub_time = sum(stats.deep_time for stats in self)
        return max(0., self.deep_time - sub_time)

    @property
    def deep_time_per_call(self):
        try:
            return self.deep_time / self.own_count
        except ZeroDivisionError:
            return 0.0

    @property
    def own_time_per_call(self):
        try:
            return self.own_time / self.own_count
        except ZeroDivisionError:
            return 0.0

    def sorted(self, order=by_deep_time):
        return sorted(self, key=order)

    def __iter__(self):
        """Override it to walk statistics children."""
        return iter(())

    def __len__(self):
        """Override it to count statistics children."""
        return 0

    def __reduce__(self):
        """Safen for Pickle."""
        members = [getattr(self, attr) for attr in self.__slots__]
        return (stats_from_members, (self.__class__, members,))

    def __hash__(self):
        """Statistics can be a key."""
        return hash((self.name, self.filename, self.lineno))

    def __repr__(self):
        # format name
        regular_name = self.regular_name
        name_string = "'{0}' ".format(regular_name) if regular_name else ''
        # format count
        deep_count = self.deep_count
        if self.own_count == deep_count:
            count_string = str(self.own_count)
        else:
            count_string = '{0}/{1}'.format(self.own_count, deep_count)
        # format time
        time_string = '{0:.6f}/{1:.6f}'.format(self.own_time, self.deep_time)
        # join all
        class_name = type(self).__name__
        return ('<{0} {1}count={2} time={3}>'
                ''.format(class_name, name_string, count_string, time_string))


class RecordingStatistics(Statistics):
    """Recordig statistics measures execution time of a code."""

    __slots__ = ('own_count', 'deep_time', 'code', 'lock', '_children')

    own_count = default(0)
    deep_time = default(0.0)

    def __init__(self, code=None):
        self.code = code
        self.lock = RLock()
        self._children = {}

    @property
    def name(self):
        if self.code is None:
            return
        name = self.code.co_name
        if name == '<module>':
            return
        return name

    @property
    def filename(self):
        return self.code and self.code.co_filename

    @property
    def lineno(self):
        return self.code and self.code.co_firstlineno

    @property
    def module(self):
        if self.code is None:
            return
        module = inspect.getmodule(self.code)
        if not module:
            return
        return module.__name__

    def get_child(self, code):
        with self.lock:
            return self._children[code]

    def add_child(self, code, stats):
        with self.lock:
            self._children[code] = stats

    def remove_child(self, code):
        with self.lock:
            del self._children[code]

    def discard_child(self, code):
        with self.lock:
            self._children.pop(code, None)

    def ensure_child(self, code, adding_stat_class=None):
        with self.lock:
            try:
                return self.get_child(code)
            except KeyError:
                stat_class = adding_stat_class or type(self)
                stats = stat_class(code)
                self.add_child(code, stats)
                return stats

    def __iter__(self):
        return itervalues(self._children)

    def __len__(self):
        return len(self._children)

    def __contains__(self, code):
        return code in self._children

    def __getstate__(self):
        raise TypeError('Cannot dump recording statistics')


class VoidRecordingStatistics(RecordingStatistics):
    """Statistics for an absent frame."""

    __slots__ = ('code', 'lock', '_children')

    _ignore = lambda x, *a, **k: None
    own_count = property(lambda x: 0, _ignore)
    deep_time = property(lambda x: sum(s.deep_time for s in x), _ignore)
    del _ignore


class FrozenStatistics(Statistics):
    """Frozen :class:`Statistics` to serialize by Pickle."""

    __slots__ = ('name', 'filename', 'lineno', 'module',
                 'own_count', 'deep_time', '_children')

    def __init__(self, stats=None):
        if stats is None:
            self._children = []
            return
        for attr in self.__slots__:
            setattr(self, attr, getattr(stats, attr))
        self._children = self._freeze_children(stats)

    @classmethod
    def _freeze_children(cls, stats):
        with stats.lock:
            return [cls(s) for s in stats]

    def __iter__(self):
        return iter(self._children)

    def __len__(self):
        return len(self._children)
