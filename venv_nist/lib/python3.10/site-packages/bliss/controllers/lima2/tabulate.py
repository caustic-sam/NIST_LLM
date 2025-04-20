# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.


from tabulate import tabulate as format


# class Formatter(object):
#     def __init__(self):
#         self.types = {}
#         self.htchar = "\t"
#         self.lfchar = "\n"
#         self.indent = 0
#         self.set_formater(object, self.__class__.format_object)
#         self.set_formater(dict, self.__class__.format_dict)
#         self.set_formater(list, self.__class__.format_list)
#         self.set_formater(tuple, self.__class__.format_tuple)

#     def set_formater(self, obj, callback):
#         self.types[obj] = callback

#     def __call__(self, value, **args):
#         for key in args:
#             setattr(self, key, args[key])
#         formater = self.types[type(value) if type(value) in self.types else object]
#         return formater(self, value, self.indent)

#     def format_object(self, value, indent):
#         return repr(value)

#     def format_dict(self, value, indent):
#         items = [
#             self.lfchar
#             + self.htchar * (indent + 1)
#             + repr(key)
#             + ": "
#             + (
#                 self.types[
#                     type(value[key]) if type(value[key]) in self.types else object
#                 ]
#             )(self, value[key], indent + 1)
#             for key in value
#         ]
#         return "{%s}" % (",".join(items) + self.lfchar + self.htchar * indent)

#     def format_list(self, value, indent):
#         items = [
#             self.lfchar
#             + self.htchar * (indent + 1)
#             + (self.types[type(item) if type(item) in self.types else object])(
#                 self, item, indent + 1
#             )
#             for item in value
#         ]
#         return "[%s]" % (",".join(items) + self.lfchar + self.htchar * indent)

#     def format_tuple(self, value, indent):
#         items = [
#             self.lfchar
#             + self.htchar * (indent + 1)
#             + (self.types[type(item) if type(item) in self.types else object])(
#                 self, item, indent + 1
#             )
#             for item in value
#         ]
#         return "(%s)" % (",".join(items) + self.lfchar + self.htchar * indent)


class Formatter:
    def __init__(self):
        self.types = {}
        self.htchar = "\t"
        self.lfchar = "\n"
        self.indent = 0
        self.set_formater(object, self.__class__.format_object)
        self.set_formater(dict, self.__class__.format_dict)
        self.set_formater(list, self.__class__.format_list)
        self.set_formater(tuple, self.__class__.format_tuple)

    def set_formater(self, obj, callback):
        self.types[obj] = callback

    def __call__(self, value, **args):
        for key in args:
            setattr(self, key, args[key])
        formater = self.types[type(value) if type(value) in self.types else object]
        return formater(self, value, self.indent)

    def format_object(self, value, indent):
        return repr(value)

    def format_dict(self, value, indent):
        items = [
            self.htchar * indent
            + key
            + ": "
            + (
                self.types[
                    type(value[key]) if type(value[key]) in self.types else object
                ]
            )(self, value[key], indent + 1)
            for key in value
        ]
        return self.lfchar.join(items) + self.lfchar + self.htchar * indent

    def format_list(self, value, indent):
        items = [
            self.htchar * indent
            + f"[{i}]"
            + self.lfchar
            + (self.types[type(item) if type(item) in self.types else object])(
                self, item, indent + 1
            )
            for i, item in enumerate(value)
        ]
        return self.lfchar.join(items) + self.htchar * indent

    def format_tuple(self, value, indent):
        items = [
            self.htchar * indent
            + (self.types[type(item) if type(item) in self.types else object])(
                self, item, indent + 1
            )
            for item in value
        ]
        return self.lfchar.join(items) + self.lfchar + self.htchar * indent


def tabulate(table: dict):
    """Pretty-print dict as tabular data"""
    pretty = Formatter()
    p = {k: pretty(v) for k, v in table.items()}
    return format(p.items())
