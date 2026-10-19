"""
Measure Scylla IO properties on GCP machine types.

For each machine type: create a VM from the given Scylla image (gcloud CLI), wait for
the image setup to write /etc/scylla.d/io_properties.yaml (machine types with no entry
in gcp_io_params.yaml run iotune at boot), collect it and delete the VM.

Results of every run are appended to --results (JSON lines), so nothing is lost if a
later machine type fails. --update-gcp-params writes flat `<machine-type>:` entries into
common/gcp_io_params.yaml, like the existing z3-* ones.

Usage:
    uv run --no-project --with pyyaml tools/io_properties_measurements/get_scylla_io_properties_gcp.py \\
        --image 1234567890 --key-path ~/.ssh/id_ed25519 --parallel 4 \\
        c4-highmem-8-lssd c4d-highmem-16-lssd
    # or every machine type matching a regex, as listed by gcloud:
    ... --family '^(c4|c4d)-highmem-.*-lssd(-metal)?$'
"""

import argparse
import json
import re
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml


IO_PROPERTIES = "/etc/scylla.d/io_properties.yaml"
IO_KEYS = ["read_iops", "read_bandwidth", "write_iops", "write_bandwidth"]
GCP_PARAMS_PATH = Path(__file__).parent.parent.parent / "common" / "gcp_io_params.yaml"
# no capacity, a regional quota other VMs of the run are using up, or no such machine type in the zone
RETRY_IN_NEXT_ZONE = ("stockout", "ZONE_RESOURCE_POOL_EXHAUSTED", "Quota", "does not exist in zone")
SSH_OPTS = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=30"]


def gcloud(*args):
    return subprocess.run(["gcloud", *args, "--format=json"], check=True, capture_output=True, text=True).stdout


def list_machine_types(regex, zone):
    out = gcloud("compute", "machine-types", "list", f"--filter=name~'{regex}' AND zone={zone}")
    return sorted(
        {mt["name"] for mt in json.loads(out)}, key=lambda n: [int(p) if p.isdigit() else p for p in n.split("-")]
    )


def image_url(image):
    return image if "/" in image else f"projects/scylla-images/global/images/{image}"


def create_vm(name, machine_type, zone, args):
    pub_key = Path(args.key_path).with_suffix(".pub").read_text().strip()
    out = gcloud(
        "compute", "instances", "create", name,
        f"--zone={zone}",
        f"--machine-type={machine_type}",
        f"--image={image_url(args.image)}",
        "--boot-disk-type=hyperdisk-balanced",  # c4 and c4d reject every pd-* boot disk
        "--boot-disk-size=50GB",
        f"--network={args.network}",
        "--maintenance-policy=TERMINATE",
        f"--metadata=ssh-keys={args.username}:{pub_key},block-project-ssh-keys=true",
        "--labels=keep=4,keep_action=terminate,purpose=io-properties-measurement",
    )  # fmt: skip
    return json.loads(out)[0]["networkInterfaces"][0]["accessConfigs"][0]["natIP"]


def delete_vm(name, zone):
    """Returns gcloud's error, or None once the VM is gone."""
    proc = subprocess.run(
        ["gcloud", "compute", "instances", "delete", name, f"--zone={zone}", "--quiet"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stderr.strip() if proc.returncode else None


def ssh(ip, args, command):
    return subprocess.run(
        ["ssh", *SSH_OPTS, "-i", args.key_path, f"{args.username}@{ip}", command],
        capture_output=True,
        text=True,
        check=False,
        timeout=900,
    )


def measure(machine_type, args):
    name = f"io-props-{machine_type}-{uuid.uuid4().hex[:6]}"[:63].rstrip("-")
    result = {"machine_type": machine_type, "image": args.image, "vm": name, "zone": None}
    try:
        for zone in args.zones:
            print(f"[{machine_type}] creating {name} in {zone}")
            try:
                ip = create_vm(name, machine_type, zone, args)
                result["zone"] = zone
                break
            except subprocess.CalledProcessError as e:
                if not any(reason in e.stderr for reason in RETRY_IN_NEXT_ZONE):
                    raise
                print(
                    f"[{machine_type}] can't create in {zone}: {next(r for r in RETRY_IN_NEXT_ZONE if r in e.stderr)}"
                )
        else:
            raise RuntimeError(f"could not create it in any of {', '.join(args.zones)}")

        deadline = time.time() + args.wait_minutes * 60
        while ssh(ip, args, "true").returncode != 0:
            if time.time() > deadline:
                raise TimeoutError("no SSH")
            time.sleep(15)

        while ssh(ip, args, f"test -s {IO_PROPERTIES}").returncode != 0:
            if time.time() > deadline:
                raise TimeoutError(f"{IO_PROPERTIES} not written after {args.wait_minutes} minutes")
            time.sleep(15)

        disk = yaml.safe_load(ssh(ip, args, f"cat {IO_PROPERTIES}").stdout)["disks"][0]
        result.update({k: disk[k] for k in IO_KEYS})
        # includes the boot disk, which is NVMe too on c4/c4d
        result["nvme_devices"] = int(ssh(ip, args, "ls /dev/ | grep -cE '^nvme[0-9]+n[0-9]+$'").stdout.strip() or 0)
        result["cpus"] = int(ssh(ip, args, "nproc").stdout.strip())
        # tells whether the value came from iotune or from a preset in gcp_io_params.yaml
        result["iotune_ran"] = "scylla_io_setup will be run" in ssh(ip, args, "sudo journalctl -b --no-pager").stdout
        print(f"[{machine_type}] {', '.join(f'{k}={result[k]}' for k in IO_KEYS)}")
    except Exception as e:  # noqa: BLE001
        result["error"] = str(e) if not isinstance(e, subprocess.CalledProcessError) else e.stderr.strip()
        print(f"[{machine_type}] FAILED: {result['error']}")
    finally:
        if result["zone"] and not args.keep and (delete_error := delete_vm(name, result["zone"])):
            # a VM left behind keeps billing (a 288 vCPU one costs ~$23/h), so make it impossible to miss
            result["delete_error"] = delete_error
            print(f"[{machine_type}] VM {name} in {result['zone']} was NOT deleted, delete it by hand: {delete_error}")
    return result


def update_gcp_params(results, path=GCP_PARAMS_PATH):
    """Add or replace flat `<machine-type>:` entries, keeping the rest of the file (and its comments) as is."""
    text = path.read_text()
    for r in results:
        if "error" in r:
            continue
        block = f"{r['machine_type']}:\n" + "".join(f"  {k}: {r[k]}\n" for k in IO_KEYS)
        existing = re.compile(rf"^{re.escape(r['machine_type'])}:\n(?:  .*\n)+", re.M)
        if existing.search(text):
            text = existing.sub(block, text)
        else:
            # new flat entries go right before Google's per-disk-count table and its comment
            marker = "# Local SSD (NVMe) performance limits"
            text = text.replace(marker, block + "\n" + marker, 1)
    path.write_text(text)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("machine_types", nargs="*", help="GCP machine types to measure")
    parser.add_argument("--family", help="regex of machine types to measure, as listed by gcloud in --zone")
    parser.add_argument("--image", required=True, help="Scylla GCE image id, name or URL (project scylla-images)")
    parser.add_argument("--key-path", required=True, help="SSH private key, with the public key next to it (.pub)")
    parser.add_argument("--username", default="scyllaadm")
    parser.add_argument(
        "--zones",
        type=lambda z: z.split(","),
        default=["us-central1-a", "us-central1-b", "us-central1-c", "us-central1-f"],
        help="comma separated, tried in order when a zone has no capacity",
    )
    parser.add_argument("--network", default="default")
    parser.add_argument("--parallel", type=int, default=1)
    parser.add_argument("--wait-minutes", type=int, default=30, help="per VM, for SSH plus io_properties.yaml")
    parser.add_argument("--keep", action="store_true", help="don't delete the VMs")
    parser.add_argument("--results", default="gcp_io_results.jsonl", help="JSON lines file results are appended to")
    parser.add_argument("--update-gcp-params", action="store_true", help=f"write the results into {GCP_PARAMS_PATH}")
    parser.add_argument("--dry-run", action="store_true", help="only print the machine types that would be measured")
    args = parser.parse_args()

    machine_types = args.machine_types + (list_machine_types(args.family, args.zones[0]) if args.family else [])
    if not machine_types:
        parser.error("no machine types, pass some or --family")
    print(f"Measuring {len(machine_types)} machine types in {','.join(args.zones)}: {' '.join(machine_types)}")
    if args.dry_run:
        raise SystemExit(0)

    results = []
    with ThreadPoolExecutor(max_workers=args.parallel) as pool, open(args.results, "a") as results_file:
        for future in as_completed([pool.submit(measure, mt, args) for mt in machine_types]):
            results.append(future.result())
            results_file.write(json.dumps(results[-1]) + "\n")
            results_file.flush()

    print(f"\n{'machine type':<28} {'cpus':>5} {"nvme":>5} {'iotune':>6} " + " ".join(f"{k:>16}" for k in IO_KEYS))
    for r in sorted(results, key=lambda r: machine_types.index(r["machine_type"])):
        if "error" in r:
            print(f"{r['machine_type']:<28} ERROR: {r['error']}")
        else:
            print(
                f"{r['machine_type']:<28} {r['cpus']:>5} {r["nvme_devices"]:>5} {str(r['iotune_ran']):>6} "
                + " ".join(f"{r[k]:>16}" for k in IO_KEYS)
            )

    if left_behind := [f"{r['vm']} ({r['zone']})" for r in results if "delete_error" in r]:
        print(f"\nVMs NOT deleted, delete them by hand: {', '.join(left_behind)}")

    if args.update_gcp_params:
        update_gcp_params(results)
        print(f"\nUpdated {GCP_PARAMS_PATH}")
