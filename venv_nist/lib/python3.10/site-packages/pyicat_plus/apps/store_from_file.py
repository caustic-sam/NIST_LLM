import os
import sys
import logging
import argparse
from glob import glob

from ..client import defaults
from ..client.main import IcatClient
from ..utils.log_utils import basic_config

logger = logging.getLogger(__name__)


def main(argv=None):
    basic_config(logger=logger, level=logging.DEBUG, format="%(message)s")

    if argv is None:
        argv = sys.argv

    parser = argparse.ArgumentParser(description="Register stored data with ICAT")
    add_store_parameters(parser)
    args = parser.parse_args(argv[1:])
    apply_store_parameters(args)

    client = IcatClient(metadata_urls=args.metadata_urls)

    for filename in args.files:
        logger.debug("Register", filename)
        client.store_dataset_from_file(filename)

    client.disconnect()


def add_store_parameters(parser):
    parser.add_argument("filter", help="File search filter")

    parser.add_argument(
        "--queue",
        dest="metadata_urls",
        action="append",
        help="ActiveMQ queue URLS",
        default=[],
    )


def apply_store_parameters(args):
    args.files = sorted(glob(args.filter), key=os.path.getmtime)

    if not args.metadata_urls:
        args.metadata_urls = defaults.METADATA_BROKERS


if __name__ == "__main__":
    sys.exit(main())
