import importlib.util
from pathlib import Path

import pytest


pd = pytest.importorskip("pandas")

_path = Path(__file__).parent.parent / "tools" / "gcp_get_network_speed.py"
_spec = importlib.util.spec_from_file_location("gcp_get_network_speed", _path)
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)


def test_default_egress_is_per_nic(monkeypatch):
    z4d = pd.DataFrame(
        {
            "Machine types": ["z4d-highmem-192-highlssd", "z4d-highmem-384-standardlssd"],
            "Number of NICs": [1, 2],
            "Default egress bandwidth (Gbps)": ["Up to 200", "Up to 400"],
        }
    )
    c4 = pd.DataFrame(
        {
            "Machine types": ["c4-standard-288-lssd"],
            "Default egress bandwidth (Gbps)": ["Up to 100"],
            "Tier_1 egress bandwidth (Gbps)": ["Up to 200"],
        }
    )
    monkeypatch.setattr(tool, "DOCS_URLS", ["docs"])
    monkeypatch.setattr(tool, "read_doc_tables", lambda _url: [z4d, c4])
    assert tool.scrape_docs_bandwidth() == {
        "z4d-highmem-192-highlssd": (200.0, None),
        "z4d-highmem-384-standardlssd": (200.0, None),
        "c4-standard-288-lssd": (100.0, 200.0),
    }
