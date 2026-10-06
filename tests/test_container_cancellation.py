"""A cancelled Docker client must not leave a detached Python worker alive."""
import os
import selectors
import shlex
import signal
import subprocess
import time
import uuid

import pytest

from magnet.containers import ContainerSettings
from magnet.process_node import MagnetProcessNode


@pytest.mark.skipif(os.environ.get('MAGNET_TEST_DOCKER') != '1',
                    reason='requires MAGNET_TEST_DOCKER=1 and a local Python image')
def test_cancelled_client_stops_container_worker():
    docker = shlex.split(os.environ.get('MAGNET_TEST_DOCKER_COMMAND', 'docker'))
    image = os.environ.get('MAGNET_TEST_PYTHON_IMAGE', 'python:3.12-alpine')
    name = 'magnet-cancel-' + uuid.uuid4().hex[:10]
    node = MagnetProcessNode(name='cancel-fixture', executable='true')
    node.apply_container_settings(ContainerSettings.coerce(
        image=image, docker_args=f'--name {name}'))
    command = node.wrap_with_container(
        "python -u -c 'import time; print(\"ready\", flush=True); time.sleep(120)'"
    )
    # The command renderer supplies Docker's flags; a caller may need sudo.
    command = shlex.join(docker) + command.removeprefix('docker')
    process = subprocess.Popen(['bash', '-c', command], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True,
                               start_new_session=True)
    try:
        assert process.stdout is not None
        selector = selectors.DefaultSelector()
        try:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=30), 'worker did not start'
            assert process.stdout.readline().strip() == 'ready'
        finally:
            selector.close()
        os.killpg(process.pid, signal.SIGTERM)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            state = subprocess.run(
                docker + ['inspect', '--format', '{{.State.Running}}', name],
                capture_output=True, text=True)
            if state.returncode != 0 or state.stdout.strip() == 'false':
                break
            time.sleep(0.1)
        else:
            pytest.fail('worker survived cancellation of its Docker client')
        process.wait(timeout=5)
    finally:
        subprocess.run(docker + ['rm', '-f', name], capture_output=True)
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
