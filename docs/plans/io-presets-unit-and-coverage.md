# I/O Presets: One Unit and Full Size Coverage

## 1. Problem Statement

The cloud backends store I/O presets in different units.
`AwsIoSetup.generate` multiplies each `aws_io_params.yaml` entry by the local disk count, so AWS entries are per disk.
GCP, Azure and OCI copy their entries without change, so their entries are whole-instance values.
Only the AWS file states its unit.

This mismatch let whole-instance values enter the AWS file for i7i, i7ie, i8g and i8ge (SMI-324, found by CUSTOMER-776).
First boot multiplied them by the disk count a second time.
The i8ge.12xlarge nodes got 48.8 GB/s of read bandwidth. The correct value is 12.2 GB/s.
Seastar then never throttles, and the device queue becomes the only limit.
PR #1033 sets per-disk values for these four families, but the AWS unit stays different from the other backends.

Also, many supported instance sizes have no preset.
A size without a preset runs iotune on first boot, so the node joins the cluster a few minutes later.
The iotune result also changes from run to run.
ScyllaDB Cloud is the main user: it must scale out fast, with correct presets.

The presets have no documented way to rebuild them.
When SCT finds that disk performance drifted, nobody can easily make new values.

Ticket: SMI-326. Related: SMI-324, SCT-1274 (artifacts-test threshold).

## 2. Current State

### First boot

- `common/scylla_image_setup` calls `cloud_instance.io_setup()` when `/var/lib/scylla` is mounted and `is_supported_instance_class()` is true.
- `io_setup()` runs `common/scylla_cloud_io_setup`. It selects `AwsIoSetup`, `GcpIoSetup`, `AzureIoSetup` or `OciIoSetup`.
- `CloudIoSetup.save` writes `/etc/scylla.d/io_properties.yaml` with one `disks` entry for the RAID0 array. Scylla reads this file without change.
- When a backend finds no preset, it runs `scylla_io_setup`, which runs iotune.
- The file stays on the node. No later boot and no image upgrade rewrites it.

### Preset lookup per backend

| Backend | File | Lookup | Value applied |
|---|---|---|---|
| AWS | `common/aws_io_params.yaml` (181 keys) | instance type, then `<class>.ALL` | entry × `len(get_local_disks())` |
| GCP | `common/gcp_io_params.yaml` | `measured_local_ssd[type][nr_disks]`, then `type`, then `local_ssd_nvme[nr_disks]` for `LOCAL_SSD_TABLE_CLASSES` with enough vCPUs | entry unchanged |
| Azure | `common/azure_io_params.yaml` (43 keys) | instance type, `disks[0]` | entry unchanged |
| OCI | `common/oci_io_params.yaml` (6 keys) | `<shape>-<ocpus>` for Flex, then shape | entry unchanged |

- `i2.ALL`, `i3.ALL` and `i3en.ALL` apply to sizes with different disk counts.

### Supported instance lists (`lib/scylla_cloud.py`)

- AWS `AwsInstance.is_supported_instance_class`: i2, i3, i3en, c5d, m5d, m5ad, r5d, z1d, c6gd, m6gd, r6gd, x2gd, im4gn, is4gen, r8gd, i4i, i4g, i7i, i7ie, i8g, i8ge, m8gd, m9gd.
- GCP `GcpInstance.is_supported_instance_class`: n1, n2, n2d, c2, z3, c4, c4a, c4d, and `m1-megamem-96`.
- Azure `AzureInstance.supported_classes`: Lsv2, Lsv3, Lasv3, Lsv4, Lasv4, Laosv4, Dpdsv6 (regular expressions).
- OCI `OciInstance.is_supported_instance_class`: VM.Standard, VM.DenseIO, BM.DenseIO, VM.Optimized. VM.Standard and VM.Optimized have no local NVMe.
- These are class lists. No code lists the supported sizes.

### Preset gaps on `next`

- AWS: 204 supported sizes (from `aws ec2 describe-instance-types`). 12 have no preset: `48xlarge`, `metal-24xl` and `metal-48xl` of i7i, i7ie, i8g and i8ge.
- Azure: the regular expressions accept Lsv2, and the yaml has no `_v2` entry. Needs Investigation: the full Laosv4 size list.
- GCP: n2, n2d and c2 sizes are an instance type × local SSD count. The z3, c4, c4a and c4d `-lssd` types have entries.
- OCI: the yaml has `VM.DenseIO.E5.Flex` at 8 to 48 OCPUs only. Needs Investigation: the other shapes with local NVMe.

### Measurements in Argus

The SCT check `IOTuneValidator` (`sdcm/utils/validators/iotune.py` in scylla-cluster-tests) reads the installed file, runs `scylla_io_setup`, and compares the two.
Argus stores both rows. The `measured` row is a fresh whole-instance iotune result.
The deviation limits per backend are `IO_DEVIATION_LIMIT_PCT = {"aws": 25, "gce": 20, "oci": 30, "azure": 35}`.

An inventory of artifacts runs on 2025.1, 2026.1, 2026.3 and master gives these results:

- Argus has measurements for 47 AWS sizes, 12 Azure sizes, 15 GCE types and 1 OCI shape.
- 2026.1, 2026.3 and master agree within 2% on every size that more than one of them measured.
- 2025.1 measures 9 to 15% higher read bandwidth and IOPS on AWS single-disk sizes.
- Measured write IOPS on i4i went up by 3 to 8 times between 2025.1.11 and 2025.1.12 (March 2026). Measurements from before April 2026 are not valid.
- Some sizes are noisy. Azure Lsv3 read IOPS are bimodal (about 14k or about 690k to 890k). i3en.24xlarge and i3en.metal spread 54 to 114% in one release.
- Master runs only the default instance types. The size matrices run on release branches.
- The default GCE artifacts test runs on n1-standard-2.

### Measuring tools (`tools/io_properties_measurements/`)

- `get_scylla_io_properties.py` (AWS), `get_scylla_io_properties_azure.py`, `get_scylla_io_properties_oci.py` and `get_scylla_io_properties_gcp.py` launch an instance and read `/etc/scylla.d/io_properties.yaml`.
- With `--override`, the AWS, Azure and OCI tools remove the file and run iotune. Without it, they can read back a value that a preset wrote.
- The AWS tool divides by the EC2 disk count before it writes the yaml.
- The GCP tool records `iotune_ran` and writes only values that iotune made.
- No tool reads the Argus measurements.

### Tests

- `tests/test_aws_instance.py` asserts the multiplication (`200800 * 2` and similar values). `TestAwsIoParamsPerDisk` checks per-disk bandwidth.
- `TestGcpIoSetup`, `TestAzureIoSetup` and `TestOciIoSetup` test the lookup: a known type writes its entry, an unknown type runs `scylla_io_setup`, and an unsupported class raises an error.

### Running nodes

- Some nodes of the i7i, i7ie, i8g and i8ge multi-disk sizes have values that are too high by the disk count. These nodes booted from an image without PR #1033.

## 3. Goals

- All four preset files use the whole-instance unit, and `scylla_cloud_io_setup` copies values without change.
- Each size in the agreed support list has a measured preset, or is on a documented skip list with a reason.
- A documented way exists to build the presets of a family again.
- Existing nodes with wrong values have a documented fix.
- The changes go to `next`, `branch-2026.1` and `branch-2026.3`.

### Decisions

- **Unit: whole instance.** `io_properties.yaml` and iotune describe the RAID0 array of all local disks, and the Argus `measured` row is the same value. A preset is then a copy of a measurement. GCP, Azure and OCI already use this unit.
- **Backport targets:** `branch-2026.1` and `branch-2026.3`.

### Decisions to make

1. **What does ScyllaDB Cloud support on each backend?** This is the main use case. Remove the classes and sizes that Cloud does not support from the support lists. n1 is a likely candidate.
2. **Do we check presets against the published cloud limits?** An example rule: AWS IOPS at most 1.1 times the published value. The check can be in unit tests and in the measuring tools.
3. **Which sizes do we skip?** Candidates: metal sizes, which vary much, and very large sizes, which can have no capacity. A skipped size runs iotune on first boot.
4. **How do we aggregate several runs?** Median or lowest value. `gcp_io_params.yaml` uses the lowest value today.
5. **Which GCP combinations of type and local SSD count get a preset?** Recommendation: the `-lssd` and z3 types, plus n2 and n2d types with at least 24 vCPUs at each local SSD count that Google allows.
6. **Backport to 2025.1?** Its iotune measures 9 to 15% higher on AWS, so it needs measurements on a 2025.1 image.
7. **Measure Azure Dpdsv5 on 2026.1, or remove it from the 2026.1 list?** No branch has a `Standard_D*pds_v5` preset.
8. **Do we add an upgrade flag that updates `io_properties.yaml` on existing nodes?** With the flag, an upgrade writes the new preset. Without it, existing nodes need a manual runbook. Phase 3 follows this decision.

## 4. Implementation Phases

### Phase 1: Whole-instance AWS presets

One PR. The yaml and the code change together.

- [ ] Multiply each AWS entry by its local disk count. Replace each `.ALL` entry with one entry per size, because the sizes of a class have different disk counts.
- [ ] Remove the `.ALL` lookup and the multiplication from `AwsIoSetup.generate`.
- [ ] Remove the division from `get_scylla_io_properties.py`.
- [ ] Make the AWS lookup tests the same as the tests of the other backends.

Definition of Done:

- For every remaining AWS size, the installed value equals the value before the PR. The PR description has the output of a one-off check script.
- Unit tests pass.

Dependencies: none.

### Phase 2: Support lists and measured presets

One PR for the support lists and the build method, then one PR per backend for the values.

- [ ] Update the support lists in `lib/scylla_cloud.py` from the answer to decision 1.
- [ ] Select a way to build the presets of a family:
  - Preferred: a script in this repository that measures the sizes with iotune and writes the preset yaml. The per-cloud tools in `tools/io_properties_measurements/` are the base. Make them always run iotune.
  - If a script is difficult for a backend: run SCT artifacts jobs with the size as `instance_type`. SCT sends the measurements to Argus, and the preset values come from the Argus `measured` rows.
- [ ] Take values from existing Argus results where 2026.x runs exist. Measure the other sizes.
- [ ] List the skipped sizes and the reasons in `tools/io_properties_measurements/README.md`.
- [ ] Document the rebuild procedure in the same README. SCT reports a deviation, someone builds the values of the family again, and a PR updates the yaml.

Measurement rules:

- Use 2026.x images.
- Run at least three times on sizes with a spread above 5%.
- Discard known bad modes, such as the low read IOPS mode of Azure Lsv3 (about 14k IOPS with normal bandwidth).

Definition of Done:

- Each size in the agreed support list has a preset or is on the skip list.
- Each value PR shows the source of each value (tool output or Argus run IDs) and the number of runs.
- The README has the rebuild procedure.

Dependencies: Phase 1, decisions 1, 3, 4 and 5.

### Phase 3: Existing nodes

The scope follows decision 8.

- [ ] List the nodes with wrong values. Compare each `/etc/scylla.d/io_properties.yaml` to the fixed yaml entry.
- [ ] With the upgrade flag: add the flag, and make an upgrade with the flag write the preset of the instance type to `io_properties.yaml`.
- [ ] Without the upgrade flag: write a runbook in `docs/`. Check the cluster capacity, remove the file on one node, run `scylla_cloud_io_setup`, restart `scylla-server`, then do the next node.

Definition of Done:

- Support and cloud operations reviewed the flag or the runbook.
- The affected clusters from CUSTOMER-776 have a rollout plan.

Dependencies: Phase 1, decision 8.

Needs Investigation: which upgrade path can run the flag. `scylla_image_setup` runs the I/O setup only on first boot (`/etc/scylla/machine_image_configured`).

## 5. Testing Requirements

Unit tests:

- AWS tests in the same form as `TestGcpIoSetup`, `TestAzureIoSetup` and `TestOciIoSetup`: a known size writes its yaml entry, an unknown size runs `scylla_io_setup`, and an unsupported class raises an error.
- Tests for the parsing and the aggregation of the Phase 2 script, if the backend uses one.
- Tests for the upgrade flag, if decision 8 adds it: the flag writes the preset, and an upgrade without the flag keeps the file.

Check script (Phase 1):

- Compare the installed AWS values before and after the PR for every remaining key, including the expanded `i3en.ALL` sizes.

Integration tests:

- SCT artifacts runs on release images compare the installed values to a fresh iotune run. Run them on a single-disk and a multi-disk size per family.

Performance tests:

- A load test must show that the presets do not throttle Scylla below what the disks allow. PR #1034 (backport of PR #1033 to 2026.3) waits for such a test.
- The current performance tests are not designed for disk stress. Needs Investigation: a test design that saturates the local disks, and the sizes to run it on.

## 6. Success Criteria

- All four preset files use whole-instance values, and `scylla_cloud_io_setup` copies them without change.
- Each size in the agreed support list has a preset or is on the skip list with a reason.
- Artifacts runs on single-disk and multi-disk sizes of each backend stay within the SCT-1274 threshold.
- The presets of a family can be built again by following the README.
- The changes are on `next`, `branch-2026.1` and `branch-2026.3`.
- Existing nodes with wrong values have the fix from decision 8: the upgrade flag or the runbook.

## 7. Risk Mitigation

- **Wrong conversion in Phase 1.** The check script catches every changed installed value before merge.
- **Lower presets throttle clusters.** Correct values are lower than the installed values on affected nodes. Run a capacity check before remediation, and roll out node by node.
- **Mixed values in a cluster.** New presets apply only to new nodes. A node keeps its file, so nodes of one cluster can have different values. State this in the release notes.
- **iotune results change between versions.** Measured i4i write IOPS changed by 3 to 8 times in March 2026. SCT finds such drift. The Phase 2 build method and README make a rebuild fast.
- **Noisy sizes.** A single run can give a wrong preset. The measurement rules require several runs and a discard rule for known bad modes.
- **Removed classes.** New images give no I/O setup to the removed classes. State the removal in the release notes. Move the default GCE artifacts test off n1 before n1 is removed.
- **Cloud capacity and cost.** Large and metal sizes can have no capacity. Decision 3 sets which sizes we skip.
- **Other repositories.** Missing measurements need artifacts runs in scylla-cluster-tests and Argus. Agree on who triggers them.
- **Rollback.** Revert the PR of the phase. Existing nodes keep their files, so a revert changes only new nodes.
