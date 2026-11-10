#!/usr/bin/env python3
#
# Copyright 2026 ScyllaDB
#
# SPDX-License-Identifier: Apache-2.0
"""Regenerate common/<cloud>_io_params_history.yaml from the git history of the I/O presets.

The history lists every io_properties.yaml value set a node may have received from
one of our presets, so `scylla_cloud_io_setup --refresh` can recognise a file we wrote
before provenance was recorded. Values are stored as the generator rendered them on the
node, keyed the way each cloud's history_keys() in scylla_cloud_io_setup looks them up.

Release branches carry preset values of their own, and nodes are launched from their
images, so the history covers HEAD and every fetched `branch-*` release branch, of this
repo and of scylla-enterprise-machine-image (Enterprise 2022.1-2024.2). Presets in the
YAML tables are read from every version of the tables; presets kept in code (all clouds
before their table) come from running every version of the generator, stubbed.

Run from the repository root, with full history and the release branches fetched, after
changing any *_io_params.yaml:

    git fetch upstream '+refs/heads/branch-*:refs/remotes/upstream/branch-*'
    git fetch git@github.com:scylladb/scylla-enterprise-machine-image.git \\
        '+refs/heads/branch-*:refs/remotes/enterprise/branch-*'
    ./tools/gen_io_params_history.py
"""

import io
import logging
import os
import subprocess
import sys
import types
from pathlib import Path

import yaml


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.io_refresh import PARAM_KEYS, values_of  # noqa: E402


CLOUDS = ("aws", "gcp", "azure", "oci")

# AwsIoSetup multiplied every entry by the local disk count until this commit, which
# made the table whole-instance values (SMI-326). Used here for the disk counts; whether
# a given commit multiplied is read from its own generator code, since release branches
# get the change as a cherry-pick with another hash.
AWS_WHOLE_INSTANCE_COMMIT = "32df899"
AWS_PER_DISK_LINE = "io_params[t][p] * nr_disks"
# Disk counts tried for the presets GCP and Azure once kept in code, keyed by disk count.
CODE_ERA_DISK_COUNTS = range(1, 25)


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def table_at(commit, cloud):
    return yaml.safe_load(git("show", f"{commit}:common/{cloud}_io_params.yaml")) or {}


def release_refs():
    """HEAD and every fetched release branch."""
    branches = git("for-each-ref", "--format=%(refname)", "refs/remotes/*/branch-*").split()
    if not branches:
        sys.exit("no release branches fetched, see the docstring")
    return ["HEAD", *branches]


def table_commits(cloud):
    """(commit, date) of every change to the table on HEAD or a release branch, oldest first."""
    out = git("log", "--reverse", "--format=%H %cs", *release_refs(), "--", f"common/{cloud}_io_params.yaml")
    return [line.split() for line in out.splitlines()]


def aws_per_disk(commit):
    return AWS_PER_DISK_LINE in git("show", f"{commit}:common/scylla_cloud_io_setup")


def file_at(commit, path):
    try:
        return git("show", f"{commit}:{path}")
    except subprocess.CalledProcessError:
        return None


class NoPresetError(Exception):
    """The generator fell back to iotune (scylla_io_setup)."""


class FakeInstance:
    """Answers every instance query the generators of 2022-2026 make."""

    def __init__(self, itype, nr_disks):
        self.instancetype, self.nvme_disk_count = itype, nr_disks
        self.cpu, self.ocpus = 96, None

    def instance(self):
        return self.instancetype

    def instance_class(self):
        return self.instancetype.split(".")[0] if "." in self.instancetype else self.instancetype.split("-")[0]

    def instance_size(self):
        return self.instancetype.split(".", 1)[-1]

    def ephemeral_disks(self):
        return [f"nvme{i}n1" for i in range(self.nvme_disk_count)]

    get_local_disks = ephemeral_disks

    def is_supported_instance_class(self):
        return True

    is_recommended_instance = is_supported_instance_class  # 2022 names
    nvmeDiskCount = property(lambda self: self.nvme_disk_count)  # noqa: N815 - the 2022 name

    def is_dev_instance_type(self):
        return False

    # 2022 generators ask the instance (or its class) which cloud it is on
    def is_aws_instance(self):
        return self.cloud == "aws"

    def is_gce_instance(self):
        return self.cloud == "gcp"

    def is_azure_instance(self):
        return self.cloud == "azure"


def run_generator(code, cloud, instance, files):
    """What the generator `code` writes into io_properties.yaml on `instance`, or None.

    It runs as __main__, the way the image runs it, with the cloud, the instance, the
    disks and every file access stubbed: nothing outside this process is touched, and
    the iotune fallback raises instead of running.
    """
    written = {}

    def dump(data, *args, **kwargs):
        written.setdefault("data", data)

    def fake_open(path, mode="r", *args, **kwargs):
        if "r" in mode and Path(path).name in files:
            return io.StringIO(files[Path(path).name])
        return io.StringIO()

    def no_iotune(*args, **kwargs):
        raise NoPresetError()

    instance.cloud = cloud

    def factory(*args, **kwargs):
        return instance

    factory.is_aws_instance = instance.is_aws_instance

    stubs = {
        "yaml": types.SimpleNamespace(safe_load=yaml.safe_load, YAMLError=yaml.YAMLError, dump=dump),
        "subprocess": types.SimpleNamespace(
            run=no_iotune,
            CalledProcessError=subprocess.CalledProcessError,
            TimeoutExpired=subprocess.TimeoutExpired,
        ),
        "os": types.SimpleNamespace(
            path=types.SimpleNamespace(ismount=lambda _: True, exists=os.path.exists, join=os.path.join),
            chmod=lambda *_: None,
        ),
        "lib": types.ModuleType("lib"),
        "lib.scylla_cloud": types.SimpleNamespace(
            get_cloud_instance=lambda: instance,
            aws_instance=factory,
            gcp_instance=factory,
            azure_instance=factory,
            is_ec2=lambda: cloud == "aws",
            is_gce=lambda: cloud == "gcp",
            is_azure=lambda: cloud == "azure",
            is_oci=lambda: cloud == "oci",
        ),
        "scylla_util": types.SimpleNamespace(
            is_developer_mode=lambda: False, etcdir=lambda: "/etc/scylla", datadir=lambda: "/var/lib/scylla"
        ),
    }
    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    logging.disable(logging.CRITICAL)  # their "scylla_io_setup will be run" warnings
    try:
        exec(code, {"__name__": "__main__", "open": fake_open})
    except (NoPresetError, SystemExit, Exception):
        pass  # an exception after the dump (e.g. chmod) still leaves what was written
    finally:
        logging.disable(logging.NOTSET)
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    disks = (written.get("data") or {}).get("disks") or [{}]
    params = disks[0]
    return {k: params[k] for k in PARAM_KEYS} if all(k in params for k in PARAM_KEYS) else None


def code_presets(commit, aws_disks):
    """{cloud: {history key: params}} for the presets the generator at `commit` kept in code."""
    source = file_at(commit, "common/scylla_cloud_io_setup")
    if source is None:
        return {}
    code = compile(source, f"{commit[:10]}:scylla_cloud_io_setup", "exec")
    tables = {f"{c}_io_params.yaml": file_at(commit, f"common/{c}_io_params.yaml") for c in CLOUDS}
    files = {name: text for name, text in tables.items() if text is not None}
    presets = {}
    if "aws_io_params.yaml" not in files:
        presets["aws"] = {t: run_generator(code, "aws", FakeInstance(t, n), files) for t, n in aws_disks.items()}
    if "local_ssd_nvme" not in files.get("gcp_io_params.yaml", ""):
        instances = {f"*/{n}": FakeInstance("n2-standard-96", n) for n in CODE_ERA_DISK_COUNTS}
        presets["gcp"] = {k: run_generator(code, "gcp", i, files) for k, i in instances.items()}
    if "azure_io_params.yaml" not in files:
        instances = {f"*/{n}": FakeInstance("Standard_L80s_v3", n) for n in CODE_ERA_DISK_COUNTS}
        presets["azure"] = {k: run_generator(code, "azure", i, files) for k, i in instances.items()}
    return {cloud: {k: p for k, p in found.items() if p} for cloud, found in presets.items()}


def generator_commits():
    """(commit, date) of every change to the generator on HEAD or a release branch."""
    out = git("log", "--reverse", "--format=%H %cs", *release_refs(), "--", "common/scylla_cloud_io_setup")
    return [line.split() for line in out.splitlines()]


def aws_disk_counts():
    """Local disk count per AWS type, from the commit that multiplied every entry by it."""
    before, after = table_at(f"{AWS_WHOLE_INSTANCE_COMMIT}^", "aws"), table_at(AWS_WHOLE_INSTANCE_COMMIT, "aws")
    counts = {}
    for itype, whole in after.items():
        per_disk = before.get(itype) or before.get(itype.split(".")[0] + ".ALL")
        if per_disk:
            counts[itype] = whole["read_iops"] // per_disk["read_iops"]
    return counts


def render(cloud, table, whole_instance=True, aws_disks=None):
    """{lookup key: params} as the generator of that table version wrote them on a node."""
    rendered = {}
    if cloud == "aws":
        # An entry that isn't a mapping (one backport on branch-2025.1 had a list) made the
        # generator crash instead of writing a file, so no node holds its values.
        table = {k: v for k, v in table.items() if isinstance(v, dict)}
        if whole_instance:
            return table
        for itype, n in aws_disks.items():
            per_disk = table.get(itype) or table.get(itype.split(".")[0] + ".ALL")
            if per_disk:
                rendered[itype] = {k: per_disk[k] * n for k in PARAM_KEYS}
        return rendered
    for key, params in table.items():
        if cloud == "gcp" and key == "local_ssd_nvme":
            rendered.update({f"*/{n}": p for n, p in params.items()})
        elif cloud == "gcp" and key == "measured_local_ssd":
            rendered.update({f"{itype}/{n}": p for itype, by_disks in params.items() for n, p in by_disks.items()})
        elif cloud == "azure":
            rendered[key] = params["disks"][0]
        else:
            rendered[key] = params
    return rendered


def add(published, first_published, key, params, date):
    values = list(values_of(params))
    if values not in published.setdefault(key, []):
        published[key].append(values)
    first_published[key] = min(first_published.get(key, date), date)


def build(cloud, code_era):
    published, first_published = {}, {}
    for date, presets in code_era:
        for key, params in presets.get(cloud, {}).items():
            add(published, first_published, key, params, date)
    aws_disks = aws_disk_counts() if cloud == "aws" else None
    for commit, date in table_commits(cloud):
        try:
            table = table_at(commit, cloud)
        except yaml.YAMLError:
            # e.g. a backport committed with conflict markers: no node ever loaded it
            print(f"skipping {commit[:10]}: {cloud}_io_params.yaml does not parse", file=sys.stderr)
            continue
        whole = cloud != "aws" or not aws_per_disk(commit)
        for key, params in render(cloud, table, whole, aws_disks).items():
            add(published, first_published, key, params, date)
    return published, first_published


def main():
    aws_disks = aws_disk_counts()
    code_era = [(date, code_presets(commit, aws_disks)) for commit, date in generator_commits()]
    for cloud in CLOUDS:
        path = Path(f"common/{cloud}_io_params_history.yaml")
        published, first_published = build(cloud, code_era)
        header = (
            f"# Every io_properties.yaml value set a {cloud} preset ever wrote, keyed as\n"
            "# lib/io_refresh.history_keys() looks them up. Values are\n"
            f"# [{', '.join(PARAM_KEYS)}].\n"
            "# Generated by tools/gen_io_params_history.py - do not edit by hand.\n"
        )
        text = yaml.dump({"first_published": dict(sorted(first_published.items()))}, default_flow_style=False)
        text += yaml.dump({"published": dict(sorted(published.items()))}, default_flow_style=None, width=200)
        path.write_text(header + text)
        print(f"{path}: {len(published)} keys")


if __name__ == "__main__":
    main()
