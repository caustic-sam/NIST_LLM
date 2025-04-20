from .nexus import NexusWriter


class ExternalNexusWriter(NexusWriter, name="external"):
    """NeXus writer in an external TANGO process."""

    def finalize(self, scan) -> None:
        pass

    def _prepare_scan(self, scan) -> None:
        pass

    @property
    def configurable(self) -> bool:
        return True  # this is an assumption, it depends on the writer start arguments
