from bliss.shell.dialog.helpers import dialog
from bliss.shell.dialog.core import show_dialog
from bliss.shell.cli.user_dialog import UserChoice, Container
from bliss.common.utils import grouped_with_tail


@dialog("Multiplexer", "set")
def multiplexer_dialog(mux_controller):
    status = mux_controller.getGlobalStat()
    values = mux_controller.getAllPossibleValues()

    widgets = list()
    for name in status.keys():
        defval = values[name].index(status[name])
        vals = [(key, key) for key in values[name]]
        choice = UserChoice(name=name, values=vals, defval=defval)
        widgets.append(Container([choice], title=name))

    dialogs = list(grouped_with_tail(widgets, 2))
    ans = show_dialog(dialogs, title=f"Multiplexer [{mux_controller.name}]")
    if ans:
        for key in status.keys():
            if ans[key] != status[key]:
                print(f"Switching {key} to {ans[key]}")
                mux_controller.switch(key, ans[key])
