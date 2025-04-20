from .base_procedure import SessionProcedure
from bliss import current_session


class UserScriptProcedure(SessionProcedure):
    def __init__(self, name: str, config: dict):
        SessionProcedure.__init__(self, name, config)
        self.__script_name = config["script_name"]
        self.__function = config["function"]

    def _run(self):
        script = current_session.user_script_load(
            self.__script_name, export_global=False
        )
        if self.__function is not None:
            getattr(script, self.__function)()
