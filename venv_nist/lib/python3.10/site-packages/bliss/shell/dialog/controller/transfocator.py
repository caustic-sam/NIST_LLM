from bliss.shell.dialog.helpers import dialog
from bliss.shell.dialog.core import show_dialog
from bliss.shell.cli.user_dialog import UserCheckBox
from bliss.common.utils import grouped_with_tail


@dialog("TransfocatorMockup", "selection")
@dialog("Transfocator", "selection")
def transfocator_menu(obj):
    """Transfocator Pinhole/Lens selection"""
    dialogs = []
    positions_list = obj.status_dict()
    for label, position in positions_list.items():
        type_ = "Pinhole" if label.startswith("P") else "Lens"
        num = label[1:]
        dialogs.append(UserCheckBox(label=f"{type_} n.{num}", defval=position))

    layout = []

    for gr in grouped_with_tail(dialogs, 6):
        layout.append(gr)

    choices = show_dialog(
        layout,
        title="Transfocator selection (checked is IN, unchecked is OUT)",
    )
    if choices:
        for n, position in enumerate(choices.values()):
            obj[n] = position
    return obj
