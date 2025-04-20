from __future__ import annotations

import json
import traceback
import logging
from pydantic import ValidationError
from typing import Any
from flask_openapi3.models.validation_error import ValidationErrorModel

from bliss import _get_current_session
from bliss.common import jsonready
from bliss.config.static import ObjectNotFound

from .core import CoreBase, CoreResource, doc
from .models.call import (
    CallFunction,
    FunctionCallStatePath,
    CallFunctionResponse,
    CallFunctionAsyncState,
)
from .models.common import (
    ErrorResponse,
    ExceptionResponse,
    custom_description,
)
from ..core.async_tasks import TaskIdNotValid, Task, FuncTask
from ..core.tasks.scan_factory_task import ScanFactoryTask
from ..core.tasks.bliss_repl_stdout_task import BlissReplStdoutTask
from ..core.tasks.bliss_repl_task import RunInPromptTask
from ..core.tasks.bliss_repl_task import TerminalNotAvailable, TerminalAlreadyInUse
from ..core.tasks.app_session_stdout_task import AppSessionStdoutTask
from ..core.tasks.object_func_task import ObjectFuncTask


_logger = logging.getLogger(__name__)


class CallApi(CoreBase):
    _base_url = "call"
    _namespace = "call"

    def setup(self):
        self.register_route(_CallFunctionResourceV1, "")
        self.register_route(_CallFunctionStateResourceV1, "/<call_id>")


def _get_object_from_bliss_session(object_path: str) -> Any:
    read_from_env = True
    bliss_session = _get_current_session()

    def read_attr(obj: Any, attr_name: str) -> Any:
        nonlocal read_from_env
        if read_from_env:
            read_from_env = False
            return bliss_session.env_dict.get(attr_name)
        else:
            return getattr(obj, attr_name)

    obj = None
    try:
        for path in object_path.split("."):
            obj = read_attr(obj, path)
    except Exception:
        _logger.debug("Exception while getting object", exc_info=True)
        return None
    return obj


class _CallFunctionResourceV1(CoreResource[CallApi]):
    @doc(
        summary="Call a function in the session asynchronously",
        responses={
            "200": CallFunctionResponse,
            "400": custom_description(
                ErrorResponse,
                "'env_object' and 'object' arguments are mutual exclusive",
            ),
            "404": custom_description(
                ErrorResponse, "Function or object not available in session"
            ),
            "422": custom_description(
                ValidationErrorModel, "Function arguments are not valid"
            ),
            "429": custom_description(ErrorResponse, "Terminal already in use"),
            "500": custom_description(ExceptionResponse, "Could not parse arguments"),
        },
    )
    def post(self, body: CallFunction):
        """
        Call a function either directly in the `session` or on an object in the session asynchronously

        This allows for example, to execute scans, and interact with bliss objects in the context
        of the running session.

        If `has_scan_factory` is true, assume the function returns a scan and return the scan in the
        `progress` field of the call state.
        """
        function_name = body.function
        object_name = body.object
        env_object_path = body.env_object

        if object_name is not None and env_object_path is not None:
            return {
                "error": "'env_object' and 'object' arguments are mutual exclusive"
            }, 400

        task: Task

        if object_name is not None:
            obj = self.rest_service.object_store.get_object(object_name)
            if obj is None:
                return {"error": "No such object"}, 404

            if not obj.has_function(function_name):
                return {"error": "No such function"}, 404

            task = ObjectFuncTask(obj, function_name)

        else:
            env_func = None
            if env_object_path:
                description = f"{env_object_path}.{function_name}(...)"
                env_func = _get_object_from_bliss_session(
                    f"{env_object_path}.{function_name}"
                )
                if env_func is None or not callable(env_func):
                    return {
                        "error": f"Function '{function_name}' from object '{env_object_path}' not found in the session"
                    }, 404
            else:
                description = f"{function_name}(...)"
                env_func = _get_object_from_bliss_session(function_name)
                if env_func is None or not callable(env_func):
                    return {
                        "error": f"Function '{function_name}' not found in the session"
                    }, 404

            if body.has_scan_factory:
                """Wrap the function around logic to wait for the scan."""
                task = ScanFactoryTask(env_func, description)
            else:
                task = FuncTask(env_func, description)

        try:
            if body.in_terminal:
                if body.emit_stdout:
                    task = BlissReplStdoutTask(task, self.rest_service._socketio)
                task = RunInPromptTask(task)
            elif body.emit_stdout:
                task = AppSessionStdoutTask(task, self.rest_service._socketio)
        except TerminalNotAvailable:
            return {"error": "Terminal not available"}, 404
        except TerminalAlreadyInUse:
            return {"error": "Terminal already in use"}, 429

        try:
            args = [] if not body.args else jsonready.bliss_from_jsonready(body.args)
            kwargs = (
                {} if not body.kwargs else jsonready.bliss_from_jsonready(body.kwargs)
            )
        except ObjectNotFound as e:
            return {"error": str(e)}, 404
        except Exception as e:
            return {
                "exception": f"{e.__class__.__name__}: {e}",
                "traceback": "".join(traceback.format_tb(e.__traceback__)),
            }, 500

        try:
            task.validate(*args, **kwargs)
        except ValidationError as e:
            return e.errors(), 422

        async_tasks = self.rest_service.async_tasks
        call_id = async_tasks.spawn(task, args, kwargs)
        return {
            "call_id": call_id,
        }


class _CallFunctionStateResourceV1(CoreResource[CallApi]):
    @doc(
        summary="Get the state and response for an asynchronous call to a function in the session",
        responses={
            "200": CallFunctionAsyncState,
            "404": custom_description(
                ErrorResponse, "Could not find the requested function call"
            ),
            "500": custom_description(ExceptionResponse, "Could not call function"),
            "503": custom_description(
                ExceptionResponse, "Could not serialise response"
            ),
        },
    )
    def get(self, path: FunctionCallStatePath):
        """Get the state and response of an asynchronous function call in the session

        When the the function returns it will try to json serialise the response,
        if this is not possible an exception will be raised.

        Exceptions will be caught and returned along with the traceback.
        """
        async_tasks = self.rest_service.async_tasks
        try:
            state = async_tasks.get_state(path.call_id)
            # The greenlet might return an Exception without raising it
            # i.e. `GreenletExit` when killed
            if isinstance(state.result, BaseException):
                progress = jsonready.bliss_to_jsonready(state.progress)
                return {
                    "state": "killed",
                    **({"progress": progress} if progress else {}),
                }
        except TaskIdNotValid:
            return {"error": "Could not find specified function call"}, 404
        except BaseException as e:
            return {
                "exception": f"{e.__class__.__name__}: {e}",
                "traceback": "".join(traceback.format_tb(e.__traceback__)),
            }, 500

        try:
            progress = jsonready.bliss_to_jsonready(state.progress)
            if not state.terminated:
                return {
                    "state": "running",
                    **({"progress": progress} if progress else {}),
                }

            result = jsonready.bliss_to_jsonready(state.result)
            # Check if data can be serialised
            json.dumps(result)
            return {
                "state": "terminated",
                "return_value": result,
                **({"progress": progress} if progress else {}),
            }
        except TypeError:
            return {
                "error": f"Couldnt serialise function: `{path.call_id}` response: {str(result)}"
            }, 503

    @doc(
        summary="Kill an asynchronous call to a function in the session",
        responses={
            "204": None,
            "404": custom_description(
                ErrorResponse, "Could not find the requested function call"
            ),
            "400": custom_description(ErrorResponse, "Could not kill function"),
        },
    )
    def delete(self, path: FunctionCallStatePath):
        """Kill an asynchronous function call in the session

        Exceptions will be caught and returned along with the traceback.
        """
        try:
            async_tasks = self.rest_service.async_tasks
            async_tasks.kill(path.call_id)
        except TaskIdNotValid:
            return {"error": "Could not find specified function call"}, 404
        except BaseException as e:
            return {
                "exception": f"{e.__class__.__name__}: {e}",
                "traceback": "".join(traceback.format_tb(e.__traceback__)),
            }, 400
        return b"", 204
