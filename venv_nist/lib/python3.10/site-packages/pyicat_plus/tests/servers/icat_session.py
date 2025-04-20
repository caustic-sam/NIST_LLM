import os
import json
import logging
from uuid import uuid4
from typing import Optional


logger = logging.getLogger("ICATPLUS SERVER")


class IcatSession:
    def __init__(self, root_dir: Optional[str] = None):
        if root_dir is None:
            root_dir = "."
        if root_dir:
            os.makedirs(root_dir, exist_ok=True)
        self._root_dir = root_dir
        self._session_file = os.path.join(root_dir, "session.json")

    def login(self, credentials: dict) -> dict:
        if credentials["password"] != "correct":
            raise PermissionError("Password incorrect")
        session_id = str(uuid4())
        session_data = {"sessionId": session_id}
        with open(self._session_file, "w") as f:
            json.dump(session_data, f)
        logger.info("Login session id = %s", session_id)
        return session_data

    @property
    def session_data(self) -> Optional[dict]:
        if os.path.exists(self._session_file):
            with open(self._session_file, "r") as f:
                return json.load(f)

    @property
    def session_id(self) -> Optional[str]:
        data = self.session_data
        if data:
            session_id = data["sessionId"]
        else:
            session_id = None
        logger.info("Current session id = %s", session_id)
        return session_id

    def is_allowed(self, session_id: str) -> bool:
        logger.info("Validate session id = %s", session_id)
        return session_id == self.session_id
