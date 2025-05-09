from dagster import (
    DagsterInstance,
    DagsterRun,
    DagsterRunStatus,
    _check as check,
)
from dagster._core.launcher import WorkerStatus
from dagster._core.workspace.context import BaseWorkspaceRequestContext
from dagster_contrib_gcp.cloud_run import run_launcher
from google.cloud.run_v2 import (
    CancelExecutionRequest,
    GetExecutionRequest,
    RunJobRequest,
)
import datetime as dt

from unittest.mock import MagicMock, patch


def test_launch_run(
    instance: DagsterInstance,
    run: DagsterRun,
    workspace: BaseWorkspaceRequestContext,
    mock_jobs_client,
    mock_executions_client,
):
    instance.launch_run(run.run_id, workspace)
    run = check.not_none(instance.get_run_by_id(run.run_id))

    # Assert the correct tag is set
    assert run.tags["cloud_run_job_execution_id"] == "test_execution_id"

    # Check that mock was called with correct args
    mock_jobs_client.run_job.assert_called_once()
    args, _ = mock_jobs_client.run_job.call_args
    assert isinstance(args[0], RunJobRequest)
    assert (
        args[0].name == "projects/test_project/locations/test_region/jobs/test_job_name"
    )
    assert args[0].overrides.timeout == dt.timedelta(seconds=7200)


def test_launch_run_with_job_config(
    instance_with_job_configs: DagsterInstance,
    run_with_job_configs: DagsterRun,
    workspace_with_job_configs: BaseWorkspaceRequestContext,
    mock_jobs_client,
    mock_executions_client,
):
    instance_with_job_configs.launch_run(
        run_with_job_configs.run_id, workspace_with_job_configs
    )
    run = check.not_none(
        instance_with_job_configs.get_run_by_id(run_with_job_configs.run_id)
    )

    # Assert the correct tag is set
    assert run.tags["cloud_run_job_execution_id"] == "test_execution_id"

    # Check that mock was called with correct args
    mock_jobs_client.run_job.assert_called_once()
    args, _ = mock_jobs_client.run_job.call_args
    assert isinstance(args[0], RunJobRequest)
    assert (
        args[0].name
        == "projects/test_gcp-123/locations/other_test_region/jobs/test_job_with_config"
    )
    assert args[0].overrides.timeout == dt.timedelta(seconds=7200)


@patch.object(run_launcher.CloudRunRunLauncher, "resolve_secret")
def test_env_override_for_code_location(patched_resolve_secret):
    mock = MagicMock()
    mock.job_name_by_code_location = {
        "my-code-location": "my-cloud-run-job-1",
        "other-code-location": {
            "name": "my-cloud-run-job-2",
            "project_id": {"secret_name": "GCP_SECRET_NAME"},
            "region": {"env": "GCP_REGION"},
        },
        "final-code-location": {
            "name": "my-cloud-run-job-3",
            "project_id": "gcp_123",
            "region": "us-central1",
        },
    }
    assert (
        run_launcher.CloudRunRunLauncher.env_override_for_code_location(
            mock, "my-code-location"
        )
        is None
    )
    patched_resolve_secret.return_value = "gcp_secret_value"
    env_with_secrets = run_launcher.CloudRunRunLauncher.env_override_for_code_location(
        mock, "other-code-location"
    )
    assert env_with_secrets is not None and "project_id" in env_with_secrets
    assert env_with_secrets is not None and "region" in env_with_secrets

    explicit_env = run_launcher.CloudRunRunLauncher.env_override_for_code_location(
        mock, "final-code-location"
    )
    assert explicit_env is not None and "project_id" in explicit_env
    assert explicit_env is not None and "region" in explicit_env


def test_terminate(
    instance: DagsterInstance,
    run: DagsterRun,
    workspace: BaseWorkspaceRequestContext,
    mock_jobs_client,
    mock_executions_client,
):
    instance.launch_run(run.run_id, workspace)
    assert instance.run_launcher.terminate(run.run_id)

    # Check that a cancellation engine event was emitted
    run = check.not_none(instance.get_run_by_id(run.run_id))
    assert run.status == DagsterRunStatus.CANCELED

    # Check that the execution client was called with the correct args
    assert mock_executions_client.cancel_execution.call_count == 1
    _, kwargs = mock_executions_client.cancel_execution.call_args
    request = kwargs["request"]
    assert isinstance(request, CancelExecutionRequest)
    assert (
        request.name
        == "projects/test_project/locations/test_region/jobs/test_job_name/executions/test_execution_id"
    )


def test_check_run_worker_health(
    instance: DagsterInstance,
    run: DagsterRun,
    workspace: BaseWorkspaceRequestContext,
    mock_jobs_client,
    mock_executions_client,
):
    instance.launch_run(run.run_id, workspace)
    run = check.not_none(instance.get_run_by_id(run.run_id))
    result = instance.run_launcher.check_run_worker_health(run)
    assert result.status == WorkerStatus.RUNNING
    assert mock_executions_client.get_execution.call_count == 1
    _, kwargs = mock_executions_client.get_execution.call_args
    request = kwargs["request"]
    assert isinstance(request, GetExecutionRequest)
    assert (
        request.name
        == "projects/test_project/locations/test_region/jobs/test_job_name/executions/test_execution_id"
    )

    instance.run_launcher.terminate(run.run_id)
    result = instance.run_launcher.check_run_worker_health(run)
    assert result.status == WorkerStatus.FAILED
    assert mock_executions_client.get_execution.call_count == 2
    _, kwargs = mock_executions_client.get_execution.call_args
    request = kwargs["request"]
    assert isinstance(request, GetExecutionRequest)
    assert (
        request.name
        == "projects/test_project/locations/test_region/jobs/test_job_name/executions/test_execution_id"
    )
