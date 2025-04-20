# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
Patch to modify the behavior of the ptpython PythonCompleter
The code for def signature_toolbar corresponds to ptpython version 2.0.4
"""
import collections
import jedi
from importlib.metadata import version
from ptpython.completer import PythonCompleter
from bliss.common.utils import autocomplete_property


class BlissPythonCompleter(PythonCompleter):
    def get_completions(self, document, complete_event):
        """
        Get Python completions. Hide those starting with "_" (unless user first types the underscore).
        """
        allow_underscore = document.text.endswith("_") or document.text.rpartition(".")[
            -1
        ].startswith("_")

        try:
            if allow_underscore:
                yield from PythonCompleter.get_completions(
                    self, document, complete_event
                )
            else:
                yield from (
                    c
                    for c in PythonCompleter.get_completions(
                        self, document, complete_event
                    )
                    if not c.text.startswith("_")
                )

        except Exception:
            pass  # tmp fix see issue 2906 # https://gitlab.esrf.fr/bliss/bliss/-/merge_requests/3944


if version("jedi") < "0.19":
    jedi.api.Interpreter._allow_descriptor_getattr_default = False
    jedi.inference.compiled.access.ALLOWED_DESCRIPTOR_ACCESS += (
        autocomplete_property,
        collections._tuplegetter,  # type: ignore
    )
else:
    jedi.inference.compiled.access.ALLOWED_DESCRIPTOR_ACCESS += (autocomplete_property,)
