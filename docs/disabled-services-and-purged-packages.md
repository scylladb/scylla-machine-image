# Disabled Services and Purged Packages in Scylla Images

This document explains the rationale behind masking systemd services/timers and
purging packages during the Scylla machine image build process. The goal is to
eliminate unnecessary background activity that could interfere with Scylla's
latency-sensitive workload, consume CPU/IO/memory, or introduce
unpredictability in a production database appliance.

## Purged Packages

These packages are removed via `apt-get purge` during image build.

| Package | Reason |
|---|---|
| `accountsservice` | D-Bus service for querying/manipulating user accounts. Not needed on a headless database server; removes an unnecessary D-Bus listener. |
| `acpid` | Handles ACPI events (lid close, power button). Cloud instances handle power management via the hypervisor; this daemon is unnecessary overhead. |
| `amd64-microcode` | CPU microcode updates. Cloud providers manage host CPU microcode at the hypervisor level; shipping microcode in guest images is unnecessary. |
| `apport` / `python3-apport` | Ubuntu crash reporting daemon. Scylla has its own crash handling and coredump setup; Apport would compete for coredump resources and send reports to Canonical rather than ScyllaDB. |
| `fuse` | Filesystem in Userspace support. Scylla uses XFS directly; FUSE is not needed and removing it reduces attack surface. |
| `fwupd-signed` | Firmware update daemon (signed component). Cloud providers manage firmware; guest-level firmware updates are not applicable. |
| `modemmanager` | Manages mobile broadband (3G/4G) devices. Has no purpose on a cloud database server; can cause delays during boot by probing serial ports. |
| `motd-news-config` | Fetches news from Canonical to display in the MOTD. Makes outbound HTTP requests on login; unnecessary network traffic and potential latency on SSH login. |
| `open-vm-tools` | VMware guest tools. Only relevant for VMware environments; purged because Scylla cloud images target AWS/GCE/Azure/OCI hypervisors. |
| `snapd` | Snap package manager daemon. Runs background refresh checks, consumes memory and disk I/O, and creates loopback mounts. Entirely unnecessary for a purpose-built database appliance. |
| `udisks2` | Disk management daemon (D-Bus based). Scylla manages its own RAID and disk setup via `scylla_setup`; udisks2 can interfere by automounting or running disk probes. |
| `unattended-upgrades` | Automatic package updates. Uncontrolled upgrades on a production database server risk introducing regressions, reboots, or I/O spikes during critical workloads. Image updates are managed via new image releases. |
| `update-notifier-common` | Checks for available package updates. Unnecessary on a database appliance where updates are delivered via new images. |
| `linux-*headers*` | Kernel header packages. Only needed for compiling kernel modules during build; not needed at runtime and wastes disk space. |
| `rsyslog` (GCE only) | System log daemon, purged on GCE to align with other cloud images. Scylla uses its own logging, and cloud-specific logging agents (e.g., Google Cloud Logging) handle system log collection. |

## Masked Services and Timers

These systemd units are masked (symlinked to `/dev/null`) so they cannot be
started, even by dependencies.

### Cloud Provider Agents

| Service | Cloud | Reason |
|---|---|---|
| `amazon-ssm-agent` | AWS | AWS Systems Manager agent. Can execute arbitrary commands on the instance, consume CPU, and interfere with Scylla's performance isolation. Not needed for database operation. |
| `google-osconfig-agent` | GCE | Google OS Config agent for patch management and inventory. Can trigger background package operations that compete with Scylla for I/O. |
| `walinuxagent` | Azure | Windows Azure Linux Agent. Handles provisioning and reporting but can consume resources and interfere with Scylla's direct disk management (RAID, IO setup). |
| `oracle-cloud-agent` | OCI | OCI monitoring and management agent. Consumes CPU/memory for metrics collection that is redundant alongside Scylla's own monitoring. |
| `oracle-cloud-agent-updater` | OCI | Auto-updater for the OCI agent. Can trigger background downloads and restarts. |

### APT and Package Management Timers

| Timer/Service | Reason |
|---|---|
| `apt-daily.timer` | Triggers daily `apt-get update` to refresh package lists. Causes network I/O and disk writes that can introduce latency spikes. |
| `apt-daily-upgrade.timer` | Triggers daily unattended package upgrades. Risk of unexpected restarts, library changes, or I/O storms during upgrade operations. |
| `apt-news.service` | Fetches APT news from Canonical. Unnecessary outbound HTTP request. |
| `dpkg-db-backup.timer` | Periodic backup of the dpkg database. The disk I/O from copying the package database can cause latency on storage-sensitive workloads. |
| `unattended-upgrades.service` | The service component of unattended upgrades. Masked in addition to purging the package as a defense-in-depth measure in case the package gets reinstalled as a dependency. |

### System Maintenance Timers

| Timer/Service | Reason |
|---|---|
| `motd-news.timer` | Periodically fetches MOTD news from the internet. Unnecessary network activity. |
| `esm-cache.service` | Caches Ubuntu Extended Security Maintenance status. Makes HTTP calls to Canonical's API; not relevant for a database appliance. |
| `dailyaidecheck.timer` | Runs daily AIDE (Advanced Intrusion Detection Environment) integrity checks. Full filesystem scans cause significant I/O load that can impact Scylla's performance. |
| `etckeeper.service` | Tracks `/etc` changes in git. The etckeeper package ships installed on the image, but its automation is disabled — its stock service would conflict with Siren's own `/etc` management. |
| `etckeeper.timer` | Periodic trigger for `etckeeper.service`. Masked for the same reason as the service. |
| `fwupd-refresh.timer` | Periodically checks for firmware updates. Cloud guests do not manage their own firmware; these checks are wasted network and CPU cycles. |
| `man-db.timer` | Rebuilds the `mandb` cache (man page index). CPU-intensive indexing operation with no benefit on a production database server where man pages are rarely consulted. |
| `scylla-manager-check-for-updates.timer` | Periodically checks for Scylla Manager updates. Outbound HTTP request to check for new versions; updates should be performed deliberately, not automatically. |
| `update-notifier-download.timer` | Downloads package update data for the desktop notification system. Unnecessary network I/O on a headless server. |
| `update-notifier-motd.timer` | Updates the MOTD with available package update counts. Redundant on a database appliance where updates are managed via image releases. |

### Filesystem Timers

| Timer/Service | Reason |
|---|---|
| `fstrim.timer` / `fstrim.service` | Periodic SSD TRIM operations. Scylla images use XFS with online discard (`discard` mount option), which handles TRIM inline. Running `fstrim` on top of that is redundant and causes I/O pauses. (Ref: SMI-249) |

## General Principles

1. **Latency sensitivity**: Scylla is designed for low-latency, high-throughput database operations. Any background process that causes I/O, CPU, or memory pressure can introduce tail latency spikes (P99/P999).

2. **Appliance model**: Scylla images are purpose-built appliances, not general-purpose servers. Packages and services needed for desktop, multi-user, or general server administration are unnecessary.

3. **Controlled updates**: Package updates on a production database should be deliberate and tested, never automatic. Image updates are delivered via new image releases that go through QA.

4. **Defense in depth**: Some services are both purged (package removed) and masked (systemd unit disabled). This guards against the package being pulled back in as a dependency.

5. **Cloud provider management**: Cloud providers manage hardware-level concerns (firmware, CPU microcode, power management) at the hypervisor layer. Guest-level agents for these functions are redundant.
