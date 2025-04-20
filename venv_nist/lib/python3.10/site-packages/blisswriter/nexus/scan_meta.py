"""Register metadata generators for a configurable writer
"""


def register_all_metadata_generators():
    """Register all metadata generators in a bliss session for
    the scan writers (currently only one).
    """
    from bliss.scanning import scan_meta  # avoid bliss patching on import
    from . import publish  # Avoid the following circular import:

    #   blisswriter.bliss.metadata
    #    -> bliss.common.auto_filter
    #       -> bliss.scanning.scan
    #          -> bliss.scanning.writer

    user_scan_meta = scan_meta.get_user_scan_meta()
    publish.register_metadata_generators(user_scan_meta)
