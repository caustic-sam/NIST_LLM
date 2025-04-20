import logging
from blisswriter.utils import logging_utils


def config_root_logger(args):
    logger = logging.getLogger("blisswriter")
    logging_utils.cliconfig(logger, args)
