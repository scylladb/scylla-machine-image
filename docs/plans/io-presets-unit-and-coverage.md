# I/O Presets: One Unit and Full Size Coverage

## 1. Problem Statement

The cloud backends store I/O presets in different units.
`AwsIoSetup.generate` multiplies each `aws_io_params.yaml` entry by the local disk count, so AWS entries are per disk.
GCP, Azure and OCI copy their entries without change, so their entries are whole-instance values.

This mismatch let whole-instance values enter the AWS file for i7i, i7ie, i8g and i8ge (SMI-324, found by CUSTOMER-776).
First boot multiplied them by the disk count a second time.
The i8ge.12xlarge nodes got 48.8 GB/s of read bandwidth. The correct value is 12.2 GB/s.
PR #1033 sets per-disk values for these four families, but the AWS unit stays different from the other backends.

Also, many supported instance sizes have no preset.
A size without a preset runs iotune on first boot, so the node joins the cluster a few minutes later.

Ticket: SMI-326. Related: SMI-324, SCT-1274 (artifacts-test threshold).

## 2. Current State

### First boot

- `common/scylla_image_setup` calls `cloud_instance.io_setup()` when `/var/lib/scylla` is mounted and `is_supported_instance_class()` is true.
- `common/scylla_cloud_io_setup` selects `AwsIoSetup`, `GcpIoSetup`, `AzureIoSetup` or `OciIoSetup`.
- `CloudIoSetup.save` writes `/etc/scylla.d/io_properties.yaml` with one `disks` entry for the RAID0 array.
- When a backend finds no preset, it runs `scylla_io_setup`, which runs iotune.

### Preset lookup per backend

| Backend | File | Lookup | Value applied |
|---|---|---|---|
| AWS | `common/aws_io_params.yaml` | instance type, then `<class>.ALL` | entry × `len(get_local_disks())` |
| GCP | `common/gcp_io_params.yaml` | `measured_local_ssd[type][nr_disks]`, then `type`, then `local_ssd_nvme[nr_disks]` for `LOCAL_SSD_TABLE_CLASSES` with enough vCPUs | entry unchanged |
| Azure | `common/azure_io_params.yaml` | instance type, `disks[0]` | entry unchanged |
| OCI | `common/oci_io_params.yaml` | `<shape>-<ocpus>` for Flex, then shape | entry unchanged |

`i2.ALL`, `i3.ALL` and `i3en.ALL` apply to sizes with different disk counts.

### Preset gaps on `next`

- AWS: 204 supported sizes (from `aws ec2 describe-instance-types`). 12 have no preset: `48xlarge`, `metal-24xl` and `metal-48xl` of i7i, i7ie, i8g and i8ge.
- Azure: the support list accepts Lsv2, and the yaml has no `_v2` entry.
- GCP: n2, n2d and c2 take a variable number of local SSDs. `measured_local_ssd` has some types and disk counts (PR #1010). The `-lssd` and z3 types have a fixed disk count and an entry each.
- OCI: the yaml has `VM.DenseIO.E5.Flex` at 8 to 48 OCPUs only.

### Measuring tools

- `tools/io_properties_measurements/` has one tool per backend. Each tool launches an instance, runs iotune (`--override` on AWS, Azure and OCI), and writes the preset yaml.
- The AWS tool divides by the EC2 disk count before it writes the yaml.
- SCT artifacts runs compare the installed values to a fresh iotune run and send both to Argus. Recent runs show that 2026.1, 2026.3 and master measure within 2% of each other.

### Tests

- `tests/test_aws_instance.py` asserts the multiplication. `TestAwsIoParamsPerDisk` checks per-disk bandwidth.
- `TestGcpIoSetup`, `TestAzureIoSetup` and `TestOciIoSetup` test the lookup: a known type writes its entry, an unknown type runs `scylla_io_setup`, and an unsupported class raises an error.

## 3. Goals

- All four preset files use whole-instance values, and `scylla_cloud_io_setup` copies them without change.
- Each backend supports the current-generation types with fast local NVMe disks, also types that ScyllaDB Cloud does not offer.
- The support lists in `lib/scylla_cloud.py` match the supported instance list.
- Every size on the supported instance list has a measured preset.
- The README of the measuring tools tells how to measure a backend again.
- The changes go to `next`, `branch-2026.1` and `branch-2026.3`.

### Decisions

- **Unit: whole instance.** `io_properties.yaml` and iotune describe the RAID0 array of all local disks. A preset is then a copy of a measurement. GCP, Azure and OCI already use this unit.
- **Support list: current-generation types with fast local NVMe.** Remove previous-generation types and types with no local NVMe. Add types with fast local NVMe, also when ScyllaDB Cloud does not offer them.
- **Measure the sizes on the supported instance list only.** One run takes a few minutes.
- **Official figures are a reference.** Each measurement PR compares the values to the official cloud figures and to the sibling sizes, as the c4, c4a and c4d PRs do. No unit test checks them. A large deviation gets a re-run and a decision for that size.
- **One run per size.** Run again only on a large deviation.
- **GCP:** the `-lssd`, z3, z4d and z4m types need one measurement each. n2, n2d and c2 types with 24 or more vCPUs are measured per local SSD count, in `measured_local_ssd`.

### Open questions

- **Metal sizes and sizes that cannot run**, for example because of no capacity: do they get a preset from the official figures, or no preset, so that `scylla_io_setup` measures the disks on the node?

### Non-goals

- New I/O values on existing nodes. SMI-218 (PR #1017) covers it.
- `branch-2025.1`.
- Azure support-list changes on `branch-2026.1`.

## 4. Implementation Phases

### Phase 1: Supported instance list

- [ ] Build the list for each backend: the current-generation types with fast local NVMe, and the types to remove.
- [ ] Make the support lists in `lib/scylla_cloud.py` match the list. Also update the AWS ENA list in `get_en_interface_type` and `LOCAL_SSD_TABLE_CLASSES` in `scylla_cloud_io_setup`.
- [ ] Remove the preset entries of the removed types.

**Proposal for review.** Sources: `aws ec2 describe-instance-types` (NVMe instance storage, current generation), [GCP Local SSD](https://docs.cloud.google.com/compute/docs/disks/local-ssd), [GCP machine generations](https://docs.cloud.google.com/compute/docs/machine-resource), [GCP storage-optimized](https://docs.cloud.google.com/compute/docs/storage-optimized-machines), [Azure sizes](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/overview), [Azure End of Life sizes](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/lifecycle/end-of-life-sizes-list), [OCI shapes](https://docs.oracle.com/en-us/iaas/Content/Compute/References/computeshapes.htm).
The lists leave out HDD types and accelerator types.

| Backend | Remove | Keep (supported today) | Add |
|---|---|---|---|
| AWS | i2 (previous generation, no NVMe) | i3, i3en, c5d, m5d, m5ad, r5d, z1d, c6gd, m6gd, r6gd, x2gd, im4gn, is4gen, i4i, i4g, i7i, i7ie, i8g, i8ge, r8gd, m8gd, m9gd | c6id, m6id, r6id, m6idn, r6idn, x2idn, x2iedn, c7gd, m7gd, c8gd, c8id, m8id, r8id, m8idn, r8idn, m8idb, r8idb, c9gd, r9gd |
| GCP | n1, m1-megamem-96 (older than 2nd generation) | n2, n2d, c2, z3, c4, c4a, c4d | c3, c3d, c4n and h4d (`-lssd` types), z4d, z4m |
| Azure | Lsv2 (End of Life) | Lsv3, Lasv3, Lsv4, Lasv4, Laosv4, Dpdsv6 | Lasv5, Laosv5, Dadsv6, Ddsv7, Dadsv7, Eadsv6, Edsv7, Eadsv7, Epdsv6 |
| OCI | VM.Standard and VM.Optimized3.Flex (block storage only), DenseIO1 and DenseIO2 (previous generation) | VM.DenseIO.E4.Flex, VM.DenseIO.E5.Flex, VM.DenseIO.E6.Ax.Flex, BM.DenseIO.E4.128, BM.DenseIO.E5.128, BM.DenseIO.E6.Ax.192, BM.Optimized3.36 | none |

The Azure v5 sizes with 160 vCPUs have an `i` in the name: `L160ias_v5`, `L160iaos_v5`.

Definition of Done:

- The list is in `tools/io_properties_measurements/README.md`.
- The support lists in `lib/scylla_cloud.py` match the list.

Dependencies: none.

### Phase 2: Whole-instance AWS presets

One PR. The yaml and the code change together.

- [ ] Multiply each AWS entry by its local disk count. Replace each `.ALL` entry with one entry per size, because the sizes of a class have different disk counts.
- [ ] Remove the `.ALL` lookup and the multiplication from `AwsIoSetup.generate`.
- [ ] Remove the division from `get_scylla_io_properties.py`.
- [ ] Make the AWS lookup tests the same as the tests of the other backends.

Definition of Done:

- For every AWS size, the installed value equals the value before the PR. The PR description has the output of a one-off check script.
- Unit tests pass.

Dependencies: Phase 1, so that the `.ALL` expansion covers only the classes on the list.

### Phase 3: Measure the supported sizes

One PR per backend. Use the tool of the backend and the newest image of `next`.
Measure only the sizes on the supported instance list.
Skip the sizes that have a measurement from the last months, for example c4, c4a and c4d (SMI-309).

- [ ] AWS: measure every size on the list. Start with the sizes that have no preset and the multi-disk sizes.
- [ ] GCP: measure the `-lssd`, z3, z4d and z4m types. Measure n2, n2d and c2 types with 24 or more vCPUs at each local SSD count.
- [ ] Azure: measure every size on the list. Run Lsv3 again when read IOPS are about 14k with normal bandwidth.
- [ ] OCI: measure every shape on the list at each OCPU count.
- [ ] Document the procedure for each backend in `tools/io_properties_measurements/README.md`.

Definition of Done:

- Every size on the list for the backend has a preset, or follows the answer to the metal question.
- The PR shows each measured value next to the official figure, and the decision for each large deviation.

Dependencies: Phase 1. Phase 2 for AWS.

## 5. Testing Requirements

Unit tests:

- AWS tests in the same form as `TestGcpIoSetup`, `TestAzureIoSetup` and `TestOciIoSetup`: a known size writes its yaml entry, an unknown size runs `scylla_io_setup`, and an unsupported class raises an error.

Check script (Phase 2):

- Compare the installed AWS values before and after the PR for every key, including the expanded `.ALL` sizes.

Integration tests:

- SCT artifacts runs on release images compare the installed values to a fresh iotune run. Run them on a single-disk and a multi-disk size per family.

Performance tests:

- A load test must show that the presets do not throttle Scylla below what the disks allow. PR #1034 (backport of PR #1033 to 2026.3) waits for such a test.
- The current performance tests are not designed for disk stress. Needs Investigation: a test design that saturates the local disks, and the sizes to run it on.

## 6. Success Criteria

- All four preset files use whole-instance values, and `scylla_cloud_io_setup` copies them without change.
- Every size on the supported instance list has a preset, or follows the answer to the metal question.
- Artifacts runs on single-disk and multi-disk sizes of each backend stay within the SCT-1274 threshold.
- The README tells how to measure each backend again.
- The changes are on `next`, `branch-2026.1` and `branch-2026.3`.

## 7. Risk Mitigation

- **Wrong conversion in Phase 2.** The check script catches every changed installed value before merge.
- **Mixed values in a cluster.** New presets apply only to new nodes. Existing nodes keep their file until SMI-218 updates it.
- **iotune results change between versions.** SCT artifacts runs find such drift. The README makes a new measurement fast.
- **Noisy sizes.** A single run can give a wrong value. The comparison to the official figures and to the sibling sizes finds it.
- **Cloud capacity.** Large and metal sizes can have no capacity. The open question on metal sizes covers them.
- **Removed types.** New nodes of a removed type get no preset, and `scylla_login` shows the type as not supported. The release notes list the removed types.
- **Rollback.** Revert the PR of the phase. Existing nodes keep their files, so a revert changes only new nodes.
