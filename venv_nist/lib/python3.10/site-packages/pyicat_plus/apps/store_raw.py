import sys
import json
import argparse
from typing import Any, Tuple
from configparser import RawConfigParser

from ..client.main import IcatClient
from ..client import defaults


def main(argv=None):
    if argv is None:
        argv = sys.argv

    parser = argparse.ArgumentParser(description="Register processed data with ICAT")
    add_store_parameters(parser)
    args = parser.parse_args(argv[1:])
    apply_store_parameters(args)

    client = IcatClient(metadata_urls=args.metadata_urls)
    client.store_dataset(
        beamline=args.beamline,
        proposal=args.proposal,
        dataset=args.dataset,
        path=args.path,
        metadata=args.metadata,
    )
    client.disconnect()


def add_store_parameters(parser):
    parser.add_argument("--beamline", required=True, help="Beamline name")

    parser.add_argument("--proposal", required=True, help="Proposal name")

    parser.add_argument("--dataset", required=True, help="Dataset name")

    parser.add_argument("--sample", required=True, help="Sample name")

    parser.add_argument(
        "--path", required=True, help="Directory of the data to be registered"
    )

    parser.add_argument(
        "--metadatafile",
        type=argparse.FileType("r"),
        help="ICAT metadata parameters file",
    )

    parser.add_argument(
        "-p",
        "--parameter",
        dest="parameters",
        action="append",
        metavar="NAME=VALUE",
        help="ICAT metadata names and values (overwrite metadata from file)",
    )

    parser.add_argument(
        "--queue",
        dest="metadata_urls",
        action="append",
        help="ActiveMQ queue URLS",
        default=[],
    )


def apply_store_parameters(args):
    if args.metadatafile:
        try:
            parameters = _parameters_from_file(args.metadatafile.read())
        finally:
            args.metadatafile.close()
    else:
        parameters = dict()
    if args.parameters:
        parameters.update(_parse_parameter(s) for s in args.parameters)
    if args.sample:
        parameters["Sample_name"] = args.sample
    args.metadata = parameters

    if not args.metadata_urls:
        args.metadata_urls = defaults.METADATA_BROKERS


def _parse_parameter(parameter: str) -> Tuple[str, str]:
    name, _, value = parameter.partition("=")
    return name, _parse_value(value)


def _parse_value(value: str) -> Any:
    try:
        return json.loads(value)
    except Exception:
        return value


def _parameters_from_file(content: str) -> dict:
    config = RawConfigParser()
    config.read_string("[config]\n" + content)
    return {key: _parse_value(value) for key, value in config["config"].items()}


if __name__ == "__main__":
    sys.exit(main())
