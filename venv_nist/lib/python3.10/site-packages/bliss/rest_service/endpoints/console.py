from __future__ import annotations
from flask_socketio import join_room

from .core import CoreBase, CoreResource, doc
from .models.common import MessageResponse
from .models.console import TerminalSizeBody
from ..bliss_server import repls, get_cmd_line


class Console(CoreBase):
    _base_url = "console"
    _namespace = "console"

    def setup(self):
        self.register_route(TerminalSizeResourceV0, "/term_size")

        self.on("attach")(self.xterm_connected)
        self.on("terminal_input")(self.xterm_receive_terminal_input)

    def xterm_connected(self, data):
        session_name = data["session_name"]
        join_room(session_name)
        cmd_line_i = get_cmd_line(data["session_name"], self.emit)
        cmd_line_i.terminal_size_changed(data["w"], data["h"])
        self.emit("ready", to=session_name)

    def xterm_receive_terminal_input(self, data):
        cmd_line_i = get_cmd_line(data["session_name"], self.emit)
        cmd_line_i.send_text(data["input"])
        if data["input"] == "\x03":
            cmd_line_i.kill_current_eval()


class TerminalSizeResourceV0(CoreResource[Console]):
    @doc(summary="Set terminal size", responses={"200": MessageResponse})
    def post(self, body: TerminalSizeBody):
        """Set the terminal size for `session_name` in `cols` and `rows`"""
        try:
            cmd_line_i = repls[body.session_name].cmd_line_i
        except KeyError:
            pass
        else:
            cmd_line_i.terminal_size_changed(body.w, body.h)

        return {"message": "success"}
