import json
import re
from pathlib import Path

import pandas as pd


# 1. Define L-Series Families
FAMILY_URLS = {
    "Lsv2-series": "https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/storage-optimized/lsv2-series",
    "Lsv3-series": "https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/storage-optimized/lsv3-series",
    "Lasv3-series": "https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/storage-optimized/lasv3-series",
    "Lsv4-series": "https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/storage-optimized/lsv4-series",
    "Lasv4-series": "https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/storage-optimized/lasv4-series",
    "Laosv4-series": "https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/storage-optimized/laosv4-series",
}


def extract_gbps(val):
    """
    Cleans raw table cell data (e.g., '3200', '40000 Mbps', 'Up to 10000')
    and returns a float in Gbps.
    """
    if pd.isna(val):
        return None

    # Convert to string and remove footnotes like [1]
    s = re.sub(r"\[.*?\]", "", str(val)).replace(",", "").strip()

    # Extract the first number found
    match = re.search(r"(\d+(\.\d+)?)", s)
    if not match:
        return None

    num = float(match.group(1))

    # Heuristic: If number > 150, it is definitely Mbps. Convert to Gbps.
    # (Azure's lowest max bandwidth is usually ~500-1000 Mbps)
    if num > 150:
        return num / 1000.0
    return num


def scrape_l_series_pandas():
    all_vm_data = {}
    print(f"Starting Pandas scrape for {len(FAMILY_URLS)} families...")

    for family, url in FAMILY_URLS.items():
        print(f"Reading: {family}...")
        try:
            # pandas.read_html fetches the URL and returns a list of DataFrames for every <table> found
            tables = pd.read_html(url)

            for df in tables:
                # Normalize column names to lowercase for easier searching
                df.columns = df.columns.astype(str).str.lower()

                # Identify key columns dynamically
                # We look for columns containing 'size' and ('bandwidth' or 'network')
                size_col = next((c for c in df.columns if "size" in c), None)
                net_col = next(
                    (c for c in df.columns if "network" in c and ("bandwidth" in c or "throughput" in c)), None
                )

                # Only process this table if it looks like a VM spec table
                if size_col and net_col:
                    # Clean the data
                    for _, row in df.iterrows():
                        raw_size = row[size_col]
                        raw_bw = row[net_col]

                        # Clean size name (remove footnotes)
                        size_name = re.sub(r"\[.*?\]", "", str(raw_size)).strip()

                        gbps = extract_gbps(raw_bw)

                        if gbps:
                            all_vm_data[size_name] = {
                                "Family": family,
                                "Network_Limit_Gbps": gbps,
                                "Source_Raw": str(raw_bw),
                            }

        except Exception as e:
            print(f"Error processing {family}: {e}")

    return all_vm_data


if __name__ == "__main__":
    data = scrape_l_series_pandas()

    # Write to common/azure_net_params.json relative to the repo root
    script_dir = Path(__file__).parent
    repo_root = script_dir.parent
    output_path = repo_root / "common" / "azure_net_params.json"

    with open(output_path, "w") as f:
        json.dump(data, f, indent=4)

    print(f"\nSuccess. Extracted {len(data)} VM sizes.")
    print(f"Saved to {output_path}")
