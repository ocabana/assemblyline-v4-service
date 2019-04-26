import json
import logging
import os
import shutil
import tempfile

import yaml

from alv4_service.common.request import ServiceRequest
from alv4_service.common.task import Task
from assemblyline.common import exceptions
from assemblyline.common import log as al_log
from assemblyline.common.dict_utils import recursive_update
from assemblyline.odm.models.service import Service


class ServiceBase(object):
    def __init__(self, config=None):
        # Get the default service configuration
        self.service = self._get_default_service_config()

        # Start with default service config and override with anything provided
        self.config = self.service.config
        if config:
            self.config.update(config)

        # Initialize logging for the service
        al_log.init_logging(f"{self.service.name}", log_level=logging.INFO)

        # Initialize non-trivial members in start_service rather than __init__
        self.log = logging.getLogger(f"assemblyline.service.{self.service.name.lower()}")
        self._working_directory = None

    def _cleanup_working_directory(self):
        try:
            if self._working_directory:
                shutil.rmtree(self._working_directory)
        except Exception:
            self.log.warning(f"Could not remove working directory: {self._working_directory}")
        self._working_directory = None

    @staticmethod
    def _get_default_service_config(yml_config=None):
        if yml_config is None:
            yml_config = os.path.join(os.path.dirname(__file__), "service_config.yml")

        # Initialize a default service
        service = Service().as_primitives()

        # Load modifiers from the yaml config
        if os.path.exists(yml_config):
            with open(yml_config) as yml_fh:
                yml_data = yaml.safe_load(yml_fh.read())
                if yml_data:
                    service = recursive_update(service, yml_data)

        return Service(service)

    def _handle_execute_failure(self, task, exception, stack_info):
        # Clear the result, in case it caused the problem
        task.result = None

        # Clear the extracted and supplementary files
        task.clear_extracted()
        task.clear_supplementary()

        if isinstance(exception, exceptions.RecoverableError):
            self.log.info(f"Recoverable Service Error ({task.sid}/{task.sha256}) {exception}: {stack_info}")
            self._save_error(task, 'FAIL_RECOVERABLE', stack_info)
        else:
            self.log.error(f"Nonrecoverable Service Error ({task.sid}/{task.sha256}) {exception}: {stack_info}")
            self._save_error(task, 'FAIL_NONRECOVERABLE', stack_info)

    def _save_error(self, task, error_status, stack_info):
        task.error_message = stack_info
        task.error_status = error_status

        error = task.as_service_error(self.service)
        error_path = os.path.join(self.working_directory, 'result.json')
        with open(error_path, 'wb') as f:
            json.dump(error.as_primitives(), f)
        self.log.info(f"Saving error to: {error_path}")

    def _save_result(self, task):
        result = task.as_service_result()
        result_path = os.path.join(self.working_directory, 'result.json')
        with open(result_path, 'wb') as f:
            json.dump(result.as_primitives(), f)
        self.log.info(f"Saving result to: {result_path}")

    def _success(self, task):
        task.success()
        self._save_result(task)

    def execute(self, request: ServiceRequest) -> None:
        raise NotImplementedError("execute() function not implemented")

    def get_tool_version(self):
        return ''

    def handle_task(self, task: Task):
        self.log.info(f"Starting task: {task.sid}/{task.sha256} ({task.type})")

        try:
            task.start(self.service)
            request = ServiceRequest(self, task)
            result = self.execute(request)
            task.set_result(result)
            task.done()
            self._success(task)
        except Exception as ex:
            self._handle_execute_failure()
        finally:
            self._cleanup_working_directory()

    def start(self):
        """
        Called at worker start.

        :return:
        """
        pass

    def start_service(self):
        self.log.info(f"Starting service: {self.service.name}")

    def stop(self):
        """
        Called at worker stop.

        :return:
        """
        pass

    def stop_service(self):
        # Perform common stop routines and then invoke the child's stop().
        self.log.info(f"Stopping service: {self.service.name}")
        self.stop()

    @property
    def working_directory(self):
        temp_dir = os.path.join(tempfile.gettempdir(), self.service.name.lower(), 'completed', self.task_hash)
        if not os.path.isdir(temp_dir):
            os.makedirs(temp_dir)
        if self._working_directory is None:
            self._working_directory = temp_dir
        return self._working_directory
