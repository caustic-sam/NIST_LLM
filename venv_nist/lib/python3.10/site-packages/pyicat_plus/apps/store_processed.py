import sys
import argparse
from . import store_raw
from ..client.main import IcatClient


def main(argv=None):
    if argv is None:
        argv = sys.argv

    parser = argparse.ArgumentParser(description="Register raw data with ICAT")

    store_raw.add_store_parameters(parser)
    add_process_parameters(parser)

    args = parser.parse_args(argv[1:])

    store_raw.apply_store_parameters(args)
    apply_process_parameters(args)

    client = IcatClient(metadata_urls=args.metadata_urls)
    client.store_processed_data(
        beamline=args.beamline,
        proposal=args.proposal,
        dataset=args.dataset,
        path=args.path,
        metadata=args.metadata,
        raw=args.raw,
    )

    client.disconnect()


def add_process_parameters(parser):
    parser.add_argument(
        "--raw",
        action="append",
        required=True,
        help="Raw dataset directories",
    )


def apply_process_parameters(args):
    pass


if __name__ == "__main__":
    sys.exit(main())
