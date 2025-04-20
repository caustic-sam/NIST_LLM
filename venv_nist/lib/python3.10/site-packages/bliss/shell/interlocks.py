from bliss.common import tango
from bliss.common.interlocks import __find_wagos
from bliss.common.interlocks import interlock_state  # noqa: F401
from bliss.shell.standard import print_html


def interlock_show(*instances):
    """Displays interlocks configuration
    Made for Wagos, but intended to be used in future for other
    kind of interlocks

    Args: any number of interlock instances, if no one is given
          it will be shown for any known instance
    """
    from bliss.controllers.wago.interlocks import interlock_show as _interlock_show
    from bliss.controllers.wago.wago import MissingFirmware
    from bliss.controllers.wago.interlocks import (
        interlock_download as _interlock_download,
    )
    from bliss.controllers.wago.interlocks import (
        interlock_compare as _interlock_compare,
    )

    wagos = __find_wagos()

    if not len(instances):
        instances = ()
        instances += wagos
        # eventual other instances

    if len(instances) == 0:
        print("No interlock instance found")
        return

    names = [instance.name for instance in instances]
    print_html(
        f"Currently configured interlocks: <color1>{' '.join(names)}</color1>\n",
    )

    for instance in instances:
        # Print interlocks info for every Wago

        if instance in wagos:
            wago = instance
            on_plc, on_beacon = False, False

            print_html(
                f"Interlocks on <color1>{instance.name}</color1>\n",
            )
            try:
                interlocks_on_plc = _interlock_download(
                    wago.controller, wago.modules_config
                )
                on_plc = True
            except (MissingFirmware, tango.DevFailed):
                print("Interlock Firmware is not present in the PLC")

            try:
                wago._interlocks_on_beacon
                on_beacon = True
            except AttributeError:
                print("Interlock configuration is not present in Beacon")

            if on_beacon and on_plc:
                # if configuration is present on both beacon and plc
                are_equal, messages = _interlock_compare(
                    wago._interlocks_on_beacon, interlocks_on_plc
                )
                if are_equal:
                    print_html("<green>On PLC:</green>")
                    print(
                        _interlock_show(
                            wago.name, interlocks_on_plc, wago.modules_config
                        )
                    )
                else:
                    print_html("<green>On PLC:</green>")
                    print(
                        _interlock_show(
                            wago.name, interlocks_on_plc, wago.modules_config
                        )
                    )
                    print_html("\n<green>On Beacon:</green>")
                    print(
                        _interlock_show(
                            wago.name, wago._interlocks_on_beacon, wago.modules_config
                        )
                    )
                    print("There are configuration differences:")
                    for line in messages:
                        print(line)
            else:
                if on_plc:
                    print_html("<green>On PLC:</green>")
                    print(
                        _interlock_show(
                            wago.name, interlocks_on_plc, wago.modules_config
                        )
                    )
                if on_beacon:
                    print_html("\n<green>On Beacon:</green>")
                    print(
                        _interlock_show(
                            wago.name, wago._interlocks_on_beacon, wago.modules_config
                        )
                    )
