from bliss.shell.dialog.helpers import dialog
from bliss.shell.dialog.core import show_dialog
from bliss.shell.cli.user_dialog import UserCheckBoxList, UserMsg
from bliss.common.measurementgroup import MeasurementGroup


@dialog("MeasurementGroup", "selection")
def measurement_group_selection(mg: MeasurementGroup, *args, **kwargs):

    values = []
    selection = []
    for fullname in mg.available:
        label = fullname
        values.append((fullname, label))
        if fullname in mg.enabled:
            selection.append(fullname)

    if len(values) == 0:
        msg = UserMsg(label="No available counters")
        show_dialog(msg, title=f"MeasurementGroup {mg.name}")
        return

    widget = UserCheckBoxList(label="Counters", values=values, defval=selection)
    result = show_dialog(
        [[widget]],
        title=f"MeasurementGroup {mg.name} Counter selection",
    )

    if result:
        selection = set(result[widget])
        for fullname, label in values:
            enabled = fullname in selection
            if enabled:
                mg.enable(fullname)
            else:
                mg.disable(fullname)
