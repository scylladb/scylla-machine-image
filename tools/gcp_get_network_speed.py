"""
Generate GCP network bandwidth entries in common/gcp_net_params.json.

Machine types are enumerated with the gcloud CLI, bandwidth figures (default
egress and Tier_1 egress, in Gbps) come from Google's machine family docs:
general-purpose and storage-optimized machines.
Existing entries are kept, enumerated ones are added or updated.

Usage:
    uv run --no-project --with pandas --with lxml tools/gcp_get_network_speed.py [--dry-run] [REGEX]

REGEX defaults to the C4/C4D local-SSD shapes, highmem and standard. For z4d:
    ... tools/gcp_get_network_speed.py '^z4d-'
"""

import argparse
import json
import re
import subprocess
import urllib.request
from io import StringIO
from pathlib import Path

import pandas as pd


DEFAULT_REGEX = "^(c4|c4d)-(standard|highmem)-.*-lssd"
DOCS_URLS = [
    "https://docs.cloud.google.com/compute/docs/general-purpose-machines",
    "https://docs.cloud.google.com/compute/docs/storage-optimized-machines",
]
OUTPUT_PATH = Path(__file__).parent.parent / "common" / "gcp_net_params.json"


def list_machine_types(regex):
    """Machine types matching regex in any zone, sorted so that c4-highmem-8 comes before c4-highmem-16.

    Same as list_machine_types in io_properties_measurements/get_scylla_io_properties_gcp.py.
    """
    out = subprocess.check_output(
        ["gcloud", "compute", "machine-types", "list", f"--filter=name~'{regex}'", "--format=json"], text=True
    )
    return sorted(
        {mt["name"] for mt in json.loads(out)}, key=lambda n: [int(p) if p.isdigit() else p for p in n.split("-")]
    )


def extract_gbps(val):
    """'Up to 23' -> 23.0, 'N/A' / NaN -> None."""
    if pd.isna(val):
        return None
    match = re.search(r"(\d+(\.\d+)?)", str(val).replace(",", ""))
    return float(match.group(1)) if match else None


def read_doc_tables(url):
    """The page's tables, without footnote markers: 'Up to 200<sup>3</sup>' would read as 'Up to 2003'."""
    with urllib.request.urlopen(url, timeout=60) as response:
        html = response.read().decode()
    return pd.read_html(StringIO(re.sub(r"<sup\b.*?</sup>", "", html, flags=re.S)))


def scrape_docs_bandwidth():
    """Return {machine_type: (default_gbps, tier1_gbps_or_None)} from every machine table in the docs.

    Tables without a Tier_1 column (z4d, z4m don't support it) give None for Tier_1.
    Where a table has a "Number of NICs" column (z4d, z4m), the default egress is divided by it: the
    figure for 2 physical NICs needs 2 vNICs, and Scylla images boot with 1 (until SMI-340).
    """
    data = {}
    for url in DOCS_URLS:
        for df in read_doc_tables(url):
            cols = [str(c).lower() for c in df.columns]
            default_col = next((i for i, c in enumerate(cols) if "default egress" in c), None)
            tier1_col = next((i for i, c in enumerate(cols) if "tier_1 egress" in c), None)
            nics_col = next((i for i, c in enumerate(cols) if "number of nics" in c), None)
            if not cols[0].startswith("machine type") or default_col is None:
                continue
            for row in df.itertuples(index=False):
                default_gbps = extract_gbps(row[default_col])
                if default_gbps:
                    if nics_col is not None:
                        nics = extract_gbps(row[nics_col])
                        if not nics:
                            raise ValueError(f"{row[0]}: can't read the number of NICs from {row[nics_col]!r}")
                        default_gbps /= nics
                    tier1_gbps = extract_gbps(row[tier1_col]) if tier1_col is not None else None
                    data[str(row[0]).strip()] = (default_gbps, tier1_gbps)
    return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("regex", nargs="?", default=DEFAULT_REGEX, help="gcloud machine type name regex")
    parser.add_argument("--dry-run", action="store_true", help="print the table instead of writing the file")
    args = parser.parse_args()

    machine_types = list_machine_types(args.regex)
    docs = scrape_docs_bandwidth()

    missing = [mt for mt in machine_types if mt not in docs]
    found = {mt: docs[mt] for mt in machine_types if mt in docs}

    print(f"{'machine type':<30} {'default Gbps':>12} {'Tier_1 Gbps':>12}")
    for mt, (default_gbps, tier1_gbps) in found.items():
        print(f"{mt:<30} {default_gbps:>12} {tier1_gbps if tier1_gbps else 'null':>12}")
    if missing:
        print(f"\nListed by gcloud but not in the docs (skipped): {', '.join(missing)}")

    if not args.dry_run:
        entries = {e[0]: e for e in json.loads(OUTPUT_PATH.read_text())}
        entries.update({mt: [mt, default_gbps, tier1_gbps] for mt, (default_gbps, tier1_gbps) in found.items()})
        OUTPUT_PATH.write_text(json.dumps(sorted(entries.values()), indent=2) + "\n")
        print(f"\nWrote {len(found)} entries to {OUTPUT_PATH}")
