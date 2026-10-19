import importlib.util
import subprocess
from pathlib import Path

import yaml


_path = Path(__file__).parent.parent / "tools" / "io_properties_measurements" / "get_scylla_io_properties_gcp.py"
_spec = importlib.util.spec_from_file_location("get_scylla_io_properties_gcp", _path)
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)

MEASURED = {"read_iops": 1, "read_bandwidth": 2, "write_iops": 3, "write_bandwidth": 4}

GCP_IO_PARAMS = """\
z3-highmem-8-highlssd:
  read_iops: 750000
  read_bandwidth: 3221225472
  write_iops: 500000
  write_bandwidth: 2684354560

# Local SSD (NVMe) performance limits published by Google
local_ssd_nvme:
  1:
    read_iops: 170000
    read_bandwidth: 692060160
    write_iops: 90000
    write_bandwidth: 367001600
"""


def test_update_gcp_params_adds_and_replaces_flat_entries(tmp_path):
    params = tmp_path / "gcp_io_params.yaml"
    params.write_text(GCP_IO_PARAMS)

    tool.update_gcp_params(
        [
            {"machine_type": "c4-highmem-8-lssd", **MEASURED},
            {"machine_type": "z3-highmem-8-highlssd", **MEASURED},
            {"machine_type": "c4d-highmem-8-lssd", "error": "boom"},
        ],
        path=params,
    )

    text = params.read_text()
    after = yaml.safe_load(text)
    assert after["c4-highmem-8-lssd"] == MEASURED
    assert after["z3-highmem-8-highlssd"] == MEASURED
    assert "c4d-highmem-8-lssd" not in after
    assert after["local_ssd_nvme"] == yaml.safe_load(GCP_IO_PARAMS)["local_ssd_nvme"]
    # new flat entries go above Google's table and keep its comment
    assert text.index("c4-highmem-8-lssd:") < text.index("# Local SSD (NVMe) performance limits")


def test_delete_vm_reports_a_failed_delete(monkeypatch):
    def run(cmd, **_):
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="permission denied\n")

    monkeypatch.setattr(tool.subprocess, "run", run)
    assert tool.delete_vm("vm", "us-east1-b") == "permission denied"

    monkeypatch.setattr(tool.subprocess, "run", lambda cmd, **_: subprocess.CompletedProcess(cmd, 0, "", ""))
    assert tool.delete_vm("vm", "us-east1-b") is None
