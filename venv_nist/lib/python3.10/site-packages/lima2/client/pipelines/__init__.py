# This file is part of the Lima2 project
#
# Copyright (c) 2020-2024 Beamline Control Unit, ESRF
# Distributed under the MIT license. See LICENSE for more info.

"""Pipelines package

This package contains a module for each supported processing pipeline.

This file defines a dictionary of all supported pipelines by tango class name,
and the associated get_class() function:

```
legacy_class = pipelines.get_class(tango_class_name="LimaProcessingLegacy")
legacy_pipeline = legacy_class(...)
```
"""

from typing_extensions import Type

from lima2.client.pipeline import Pipeline
from lima2.client.pipelines.legacy import Legacy
from lima2.client.pipelines.smx import Smx
from lima2.client.pipelines.xpcs import Xpcs

# Dictionary of all pipelines supported by the client
pipelines = {
    "LimaProcessingLegacy": Legacy,
    "LimaProcessingSmx": Smx,
    "LimaProcessingXpcs": Xpcs,
}


def get_class(tango_class_name: str) -> Type[Pipeline]:
    """Get a pipeline class from its tango class name.

    Raise with some additional info if the class doesn't exist.
    """
    try:
        return pipelines[tango_class_name]
    except KeyError as e:
        raise KeyError(
            f"Pipeline {e} not found in the `pipelines` dictionary ({__file__})"
        ) from e
