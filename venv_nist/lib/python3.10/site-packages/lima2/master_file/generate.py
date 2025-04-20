# This file is part of the Lima2 project
#
# Copyright (c) 2020-2024 Beamline Control Unit, ESRF
# Distributed under the MIT licence. See LICENSE for more info.

"""Master file generation utilities.

Calling enable_generation() on the current pipeline between prepare_acq() and
start_acq() causes the master-file generation procedure to be called when all
processing pipelines finish.
"""

import glob
import logging
import os

import h5py
import numpy as np

from lima2.client.pipeline import FrameType, Pipeline

logger = logging.getLogger(__name__)


def enable_generation(
    pipeline: Pipeline, num_frames: int, proc_params: dict, rr_offset=0
) -> None:
    """Enable the generation of a master file for a given pipeline.

    Must be called between Client.prepare_acq() and Client.start_acq(), so that the
    generate_master_file() callback can be registered by the pipeline.

    The processing parameters passed to prepare_acq() are required to be able to
    retrieve data at the save path.

    Args:
        pipeline (Pipeline): current pipeline, before the call to start_acq().
        num_frames (int): total number of frames acquired
        proc_params (dict): processing parameters passed to prepare_acq().
        rr_offset (int): expected receiver of frame 0 in the round robin frame dispatching
    """

    if rr_offset >= len(pipeline._devs):
        raise ValueError(
            f"Round robin offset cannot exceed the last receiver index ({len(pipeline._devs) - 1})."
        )

    generate_params: list[dict] = []
    """Params passed to generate_master_file for each enabled saving channel"""

    for name, source in pipeline.FRAME_SOURCES.items():
        # Skip non-dense frame sources
        if source.frame_type != FrameType.DENSE:
            continue
        # Skip non-persistent sources
        if source.saving_channel is None:
            continue
        # Skip source if channel is disabled
        if not proc_params[source.saving_channel]["enabled"]:
            continue

        base_path = proc_params[source.saving_channel]["base_path"]
        filename_prefix = proc_params[source.saving_channel]["filename_prefix"]
        file_exists_policy = proc_params[source.saving_channel]["file_exists_policy"]

        master_file_path = f"{base_path}/{filename_prefix}_master.h5"
        if os.path.exists(master_file_path) and file_exists_policy != "overwrite":
            raise ValueError(
                f"Master file (channel={source.saving_channel}) exists at {master_file_path} "
                f"but {file_exists_policy=}. Cannot enable master file generation."
            )

        logger.info(
            f"Master file (channel={source.saving_channel}) will be generated "
            f"at {master_file_path}..."
        )

        frame_info = pipeline.channels[name]

        # Calling register_on_finished here with a lambda doesn't work as expected
        # because some parameters will be evaluated at the time of calling, and their value
        # will have changed in subsequent loop iterations.
        # One solution is to store the parameters in a dict and call register_on_finished
        # outside of the loop.
        generate_params.append(
            dict(
                num_frames=num_frames,
                frame_shape=frame_info["shape"],
                pixel_dtype=frame_info["dtype"],
                num_receivers=len(pipeline._devs),
                saving_params=proc_params[source.saving_channel],
                rr_offset=rr_offset,
            )
        )

    if len(generate_params) == 0:
        logger.warning(
            "Master file generation not enabled: no dense frames saving channel enabled."
        )
    else:
        pipeline.register_on_finished(
            on_finished=lambda: [
                generate_master_file(**params) for params in generate_params
            ]
        )


def generate_master_file(
    num_frames: int,
    frame_shape: tuple[int, int, int],
    pixel_dtype: np.dtype,
    num_receivers: int,
    saving_params: dict,
    rr_offset: int,
) -> None:
    """Generate the master file containing the virtual dataset of ordered frames.

    Assumes a strict round-robin dispatching of frames across receivers.
    """

    base_path = saving_params["base_path"]
    filename_prefix = saving_params["filename_prefix"]
    nx_entry_name = saving_params["nx_entry_name"]
    nx_instrument_name = saving_params["nx_instrument_name"]
    nx_detector_name = saving_params["nx_detector_name"]
    nb_frames_per_file = saving_params["nb_frames_per_file"]

    master_file_path = f"{base_path}/{filename_prefix}_master.h5"

    logger.info(f"Generating master file for dataset at {base_path}...")

    logger.debug(
        f"Creating virtual layout with shape={(num_frames, *frame_shape)}, "
        f"dtype={pixel_dtype}"
    )
    layout = h5py.VirtualLayout(
        shape=(num_frames, *frame_shape),
        dtype=pixel_dtype,
    )

    data_files = glob.glob(pathname=f"{base_path}/*.h5")
    assert len(data_files) > 0, f"No .h5 files found at {base_path}"

    # Frames are dispatched to each receiver in turn.
    # If num_frames is not a multiple of num_receivers, some receivers
    # will get one more frame than the rest.
    nframes_in_rcv = [num_frames // num_receivers] * num_receivers
    for i in range(num_frames % num_receivers):
        nframes_in_rcv[(i + rr_offset) % num_receivers] += 1
    logger.debug(f"{nframes_in_rcv=}")

    # From the number of frames dispatched to each receiver,
    # we can calculate the number of files created by each one.
    nfiles_of_rcv = [
        (nframes + nb_frames_per_file - 1) // nb_frames_per_file
        for nframes in nframes_in_rcv
    ]
    logger.debug(f"{nfiles_of_rcv=}")

    for file_idx in range(max(nfiles_of_rcv)):
        for rank, nframes in enumerate(nframes_in_rcv):
            if file_idx >= nfiles_of_rcv[rank]:
                # This receiver saved fewer files because it got fewer frames
                continue

            filepath = f"{base_path}/{filename_prefix}_{rank}_{file_idx:05d}.h5"
            if not os.path.exists(filepath):
                logger.warning(
                    f"Expected file at {filepath} to have been created by receiver {rank}. "
                    "Master file will point to invalid location."
                )

            # Actual number of frames in this file
            nframes_in_file = min(
                nframes - file_idx * nb_frames_per_file,
                nb_frames_per_file,
            )

            # Assign the virtual source to the virtual layout using the obtained frame indices
            vsource = h5py.VirtualSource(
                filepath,
                f"{nx_entry_name}/{nx_instrument_name}/{nx_detector_name}/data",
                shape=(
                    nframes_in_file,
                    *frame_shape,
                ),
            )

            offset_rank = (rank - rr_offset) % num_receivers
            start_idx = offset_rank + file_idx * nb_frames_per_file * num_receivers
            end_idx = start_idx + nframes_in_file * num_receivers
            logger.debug(
                f"Setting vds slice {start_idx}:{end_idx}:{num_receivers} to file {filepath}"
            )
            layout[start_idx:end_idx:num_receivers] = vsource

    # Write master file
    with h5py.File(name=master_file_path, mode="w") as f:
        first_file_path = f"{base_path}/{filename_prefix}_{rr_offset}_00000.h5"
        if not os.path.exists(first_file_path):
            raise ValueError(f"First file not found at {first_file_path}")

        logger.debug(f"Inheriting metadata from {first_file_path}")
        copy_metadata(src_path=first_file_path, dst=f)

        data_path = f"{nx_entry_name}/{nx_instrument_name}/{nx_detector_name}/data"
        measurement_path = f"{nx_entry_name}/measurement/data"
        plot_path = f"{nx_entry_name}/{nx_instrument_name}/{nx_detector_name}/plot/data"

        # Create VDS
        dataset = f.create_virtual_dataset(data_path, layout=layout)
        dataset.attrs["interpretation"] = "image"

        # Create links
        f[measurement_path] = f[data_path]
        f[plot_path] = f[data_path]

    logger.info(f"Master file written at {master_file_path}")


def copy_group(src_node: h5py.Group, dst_node: h5py.Group):
    """Recursively copy items of a group, skipping frame datasets."""

    for attr_key, attr_value in src_node.attrs.items():
        logger.debug(f"{dst_node.name}: copying attribute {attr_key} ({attr_value})")
        dst_node.attrs[attr_key] = attr_value

    for key, value in src_node.items():
        if key == "data" and isinstance(value, h5py.Dataset):
            # Skip frame datasets
            logger.debug(f"Skipping {value.name}")
            continue
        elif isinstance(value, h5py.Group):
            # Conservative group copy
            logger.debug(f"Creating group {value.name}")
            grp = dst_node.create_group(name=key)
            copy_group(src_node=value, dst_node=grp)
        else:
            # Plain copy
            logger.debug(f"Copying object {key} into {dst_node.name}")
            src_node.copy(value, dst_node)


def copy_metadata(src_path: str, dst: h5py.File):
    """Copy everything but frame data from one hdf5 file to another."""
    with h5py.File(name=src_path, mode="r") as src:
        copy_group(src_node=src, dst_node=dst)
