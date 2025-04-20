import logging

# Compatibility with BLISS 2.0 and 2.1
# This kind of import is such code which can be stored locally at beamlines
# to create custom flint plots
from flint.client.plots import BasePlot  # noqa


_logger = logging.getLogger("__name__")
_logger.error(
    "Module `bliss.flint.client.plots` is deprecated since BLISS 2.2. Please use `flint.client.plots` instead."
)
