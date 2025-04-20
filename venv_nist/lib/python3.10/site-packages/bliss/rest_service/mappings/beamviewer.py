import logging


from bliss.rest_service.mappingdef import (
    ObjectMapping,
    HardwareProperty,
)
from ..types.beamviewer import BeamviewerType

logger = logging.getLogger(__name__)


class Beamviewer(ObjectMapping):
    TYPE = BeamviewerType

    def _get_state(self):
        return self._object._bpm._cam_proxy.state()

    PROPERTY_MAP = {
        "led": HardwareProperty("led_status"),
        "foil": HardwareProperty("foil_status"),
        "screen": HardwareProperty("screen_status"),
        "diode_range": HardwareProperty("diode_range"),
        "diode_ranges": HardwareProperty("diode_range_available"),
        "state": HardwareProperty("state", getter=_get_state),
    }

    def _call_led(self, value, **kwargs):
        if value:
            self._object.led_on()
        else:
            self._object.led_off()

    def _call_foil(self, value, **kwargs):
        if value:
            self._object.foil_in()
        else:
            self._object.foil_out()

    def _call_screen(self, value, **kwargs):
        if value:
            self._object.screen_in()
        else:
            self._object.screen_out()

    def _call_current(self, value, **kwargs):
        return self._object.current


Default = Beamviewer
