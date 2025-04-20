import os
from typing import Mapping, Optional, List, Dict


def scan_filename(scan_info: Mapping, raw: bool = False) -> Optional[str]:
    """Filename which contains bliss scans."""
    scan_filename = scan_info.get("filename")
    if not scan_filename or not raw:
        return scan_filename
    writer_options = scan_info.get("writer_options")
    if not writer_options:
        return scan_filename
    if not writer_options.get("separate_scan_files"):
        return scan_filename
    images_path = scan_info.get("images_path")
    if not images_path:
        return scan_filename
    root_dir, scan_basename = os.path.split(scan_filename)
    _, ext = os.path.splitext(scan_basename)
    master_base = "bliss_master" + ext
    subdir = os.path.relpath(images_path, root_dir)
    parts = list()
    for s in subdir.split(os.path.sep):
        if "{" in s:
            break
        if s:
            parts.append(s)
    return os.path.join(root_dir, *parts, master_base)


def scan_filenames(scan_info: Mapping, config: bool = True) -> Dict[str, str]:
    """Names of the files that contain the scan data (raw or as links)"""
    filenames = dict(scan_master_filenames(scan_info, config=config))
    filename = scan_filename(scan_info, raw=False)
    if filename:
        filenames["dataset"] = filename
    filename = scan_filename(scan_info, raw=True)
    if filename:
        filenames["scan"] = filename  # can be the same as dataset
    return filenames


def scan_master_filenames(scan_info: Mapping, config: bool = True) -> Dict[str, str]:
    """Names of the files that contain links to the scan data"""
    if not config:
        return dict()
    info = scan_info.get("nexuswriter", dict())
    return info.get("masterfiles", dict())


def scan_urls(scan_info: Mapping, subscan: int = None, raw: bool = False) -> List[str]:
    """URL of all subscans of a scan"""
    if subscan is None:
        nsubscans = len(scan_info["acquisition_chain"])
        subscans = range(1, nsubscans + 1)
    else:
        subscans = [subscan]
    return list(
        filter(
            None,
            [subscan_url(scan_info, subscan=subscan, raw=raw) for subscan in subscans],
        )
    )


def subscan_url(
    scan_info: Mapping, subscan: int = 1, raw: bool = False
) -> Optional[str]:
    """URL of one subscan of a scan"""
    filename = scan_filename(scan_info, raw=raw)
    if not filename:
        return None
    name = scan_name(scan_info, subscan)
    if not name:
        return None
    return f"{filename}::/{name}"


def scan_name(scan_info: Mapping, subscan: int = 1) -> Optional[str]:
    """Name of the scan in the file tree structure"""
    scan_nb = scan_info["scan_nb"]
    if scan_nb > 0:
        return f"{scan_nb}.{subscan}"
