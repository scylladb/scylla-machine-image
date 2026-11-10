#
# Copyright 2026 ScyllaDB
#
# SPDX-License-Identifier: Apache-2.0
"""Keep /etc/scylla.d/io_properties.yaml in step with the shipped I/O presets.

The file is derived from (preset table, cloud, instance type, disk count). This module
decides whether a file on a node is ours to update, and updates it, backs it up, rolls
it back or pins it. See docs/plans/io-properties-upgrade-refresh.md (SMI-218).
"""

import contextlib
import datetime
import fcntl
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml


LOGGER = logging.getLogger(__name__)

PARAM_KEYS = ("read_iops", "read_bandwidth", "write_iops", "write_bandwidth")
MOUNTPOINT = "/var/lib/scylla"

IO_YAML = Path("/etc/scylla.d/io_properties.yaml")
IO_CONF = Path("/etc/scylla.d/io.conf")
PROVENANCE = Path("/etc/scylla/io_properties_provenance.yaml")
NOTICE = Path("/etc/scylla/io_properties_notice")
POLICY = Path("/etc/scylla/io_properties_refresh_policy")
CONFIGURED_MARKER = Path("/etc/scylla/machine_image_configured")
LOCK = Path("/run/lock/scylla-io-properties.lock")
HISTORY_DIR = Path("/opt/scylladb/scylla-machine-image")

POLICIES = ("auto", "notify", "never")
DEFAULT_POLICY = "auto"
REFRESH_COMMAND = "sudo /opt/scylladb/scylla-machine-image/scylla_cloud_io_setup --refresh --force"


def io_conf_line():
    return f'SEASTAR_IO="--io-properties-file={IO_YAML}"\n'


def values_of(params):
    return tuple(params[k] for k in PARAM_KEYS)


def render(params):
    disk = {"mountpoint": MOUNTPOINT, **{k: params[k] for k in PARAM_KEYS}}
    return yaml.dump({"disks": [disk]}, default_flow_style=False)


def parse_ours(text):
    """The four values of a file shaped exactly like the ones we write, else None.

    Anything else is the user's: comments (yaml.dump never writes any, and parsing
    would drop them), extra disks or keys, another mount point, or unparseable YAML.
    """
    if any(line.lstrip().startswith("#") for line in text.splitlines()):
        return None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict) or list(data) != ["disks"]:
        return None
    disks = data["disks"]
    if not isinstance(disks, list) or len(disks) != 1 or not isinstance(disks[0], dict):
        return None
    disk = disks[0]
    if set(disk) != {"mountpoint", *PARAM_KEYS} or disk["mountpoint"] != MOUNTPOINT:
        return None
    return values_of(disk)


class History:
    """common/<cloud>_io_params_history.yaml, see tools/gen_io_params_history.py."""

    def __init__(self, data=None):
        data = data or {}
        self.published = data.get("published") or {}
        self.first = data.get("first_published") or {}

    @classmethod
    def load(cls, cloud):
        path = HISTORY_DIR / f"{cloud}_io_params_history.yaml"
        try:
            return cls(yaml.safe_load(path.read_text()))
        except FileNotFoundError:
            LOGGER.warning("%s not found, only files with recorded provenance can be updated", path)
            return cls()

    def knows(self, keys, values):
        return any(list(values) in self.published.get(k, []) for k in keys)

    def first_published(self, keys):
        """Date of the most specific key that has one: keys run from specific to wildcard,
        and a wildcard (GCP/Azure `*/<disks>`) dates the rule, not this instance type."""
        return next((str(self.first[k]) for k in keys if k in self.first), None)


@dataclass
class Decision:
    action: str  # apply | adopt | current | notify | skip
    reason: str


def classify(*, preset, yaml_text, conf_text, provenance, history, keys, first_boot, policy, force=False):
    """Decide what to do with the io_properties.yaml / io.conf pair. Pure, no I/O.

    preset: the four values this package's tables give the node, or None.
    first_boot: date (YYYY-MM-DD) the node was first configured, or None.
    """
    provenance = provenance or {}
    if preset is None:
        return Decision("skip", "no preset for this instance type and disk count")
    if force:
        return Decision("apply", "forced")
    if policy == "never":
        return Decision("skip", "refresh policy is 'never'")
    if provenance.get("pinned"):
        return Decision("skip", "pinned, run --unpin to resume refresh")
    if conf_text is not None and conf_text != io_conf_line():
        return Decision("notify", "io.conf was changed by hand")
    if yaml_text is None:
        return Decision("apply", "no io_properties.yaml")
    values = parse_ours(yaml_text)
    if values is None:
        return Decision("notify", "io_properties.yaml was changed by hand (layout, keys or comments)")
    if values == tuple(preset):
        return Decision("current" if provenance.get("values") else "adopt", "already up to date")

    update = "apply" if policy == "auto" else "notify"
    recorded = provenance.get("values")
    if recorded:
        if tuple(recorded) != values:
            return Decision("notify", "io_properties.yaml was edited after we wrote it")
        if not history.knows(keys, values):
            return Decision("notify", "written by a newer package (downgrade)")
        return Decision(update, "we wrote it and it is unchanged")
    if history.knows(keys, values):
        return Decision(update, "matches a preset we published before")

    first_published = history.first_published(keys)
    if first_boot and first_published and first_boot < first_published:
        return Decision("notify", "predates our preset for this instance type, likely iotune output")
    return Decision("notify", "values match no preset we published")


def read_text(path):
    try:
        return path.read_text()
    except FileNotFoundError:
        return None


def read_provenance():
    text = read_text(PROVENANCE)
    return (yaml.safe_load(text) or {}) if text else {}


def write_provenance(data):
    atomic_write(PROVENANCE, yaml.dump(data, default_flow_style=False))


def read_policy():
    policy = (read_text(POLICY) or DEFAULT_POLICY).strip()
    if policy not in POLICIES:
        LOGGER.warning("Unknown refresh policy %r in %s, using %r", policy, POLICY, DEFAULT_POLICY)
        return DEFAULT_POLICY
    return policy


def first_boot_date():
    try:
        mtime = CONFIGURED_MARKER.stat().st_mtime
        return datetime.datetime.fromtimestamp(mtime, tz=datetime.timezone.utc).date().isoformat()
    except FileNotFoundError:
        return None


def atomic_write(path, text):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def backup_path(path):
    return path.with_name(path.name + ".bak")


def candidate_path(path):
    return path.with_name(path.name + ".new")


@contextlib.contextmanager
def locked():
    """One refresh, rollback or pin at a time, so none overwrites another's .bak."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("w") as fd:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield


def save(params, *, cloud, keys):
    """Write the pair and record that we did. Also used on first boot."""
    atomic_write(IO_YAML, render(params))
    atomic_write(IO_CONF, io_conf_line())
    write_provenance(
        {
            "cloud": cloud,
            "keys": list(keys),
            "values": list(values_of(params)),
            "written_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "pinned": False,
        }
    )
    clear_candidate()


def clear_candidate():
    candidate_path(IO_YAML).unlink(missing_ok=True)
    NOTICE.unlink(missing_ok=True)


def backup():
    for path in (IO_YAML, IO_CONF):
        if path.exists():
            shutil.copy2(path, backup_path(path))


def notify(params, reason):
    atomic_write(candidate_path(IO_YAML), render(params))
    atomic_write(
        NOTICE,
        f"I/O properties for this instance differ from the current preset ({reason}).\n"
        f"Kept {IO_YAML}, the preset is in {candidate_path(IO_YAML)}.\n"
        f"To use it (takes effect when scylla-server restarts): {REFRESH_COMMAND}\n"
        "To keep your file and hide this notice: "
        "sudo /opt/scylladb/scylla-machine-image/scylla_cloud_io_setup --pin\n",
    )


def refresh(*, preset, cloud, keys, force=False, dry_run=False):
    """Bring the pair in line with `preset` (a params dict, or None) when it is ours to change."""
    with locked():
        provenance = read_provenance()
        decision = classify(
            preset=values_of(preset) if preset else None,
            yaml_text=read_text(IO_YAML),
            conf_text=read_text(IO_CONF),
            provenance=provenance,
            history=History.load(cloud),
            keys=keys,
            first_boot=first_boot_date(),
            policy=read_policy(),
            force=force,
        )
        LOGGER.info("io_properties refresh: %s (%s)", decision.action, decision.reason)
        if dry_run:
            return decision
        if decision.action == "apply":
            backup()
            save(preset, cloud=cloud, keys=keys)
            LOGGER.warning(
                "Updated %s to the current preset; it takes effect when scylla-server next starts. "
                "Revert with: sudo /opt/scylladb/scylla-machine-image/scylla_cloud_io_setup --rollback",
                IO_YAML,
            )
        elif decision.action == "adopt":
            save(preset, cloud=cloud, keys=keys)
        elif decision.action == "current":
            clear_candidate()
        elif decision.action == "notify":
            notify(preset, decision.reason)
        return decision


def set_pinned(pinned):
    with locked():
        provenance = read_provenance()
        provenance["pinned"] = pinned
        write_provenance(provenance)
        if pinned:
            clear_candidate()


def rollback():
    """Restore the pair from .bak and pin, so the next boot doesn't re-apply what was reverted."""
    with locked():
        backups = [(backup_path(p), p) for p in (IO_YAML, IO_CONF) if backup_path(p).exists()]
        if not backups:
            raise FileNotFoundError(f"no {backup_path(IO_YAML)} to roll back to")
        for bak, path in backups:
            shutil.copy2(bak, path)
        provenance = read_provenance()
        provenance["pinned"] = True
        write_provenance(provenance)
        clear_candidate()
