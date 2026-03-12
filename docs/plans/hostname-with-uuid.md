# Plan: Add Server UUID to Instance Hostname

## 1. Problem Statement

ScyllaDB logs show server UUIDs for node identification, but the hostnames in the logs only contain IP addresses. This creates a disconnect that makes it harder to correlate log entries across different systems and identify specific nodes in the cluster.

**Business Need:**
- Improve operational visibility by making server UUIDs immediately visible in hostnames
- Simplify troubleshooting by having consistent node identifiers across logs and system tools
- Enable easier correlation between ScyllaDB logs (which show UUIDs) and system-level logs (which show hostnames)

**Pain Points:**
- Operators must manually map IP addresses to UUIDs when troubleshooting
- Hostnames don't provide any durable identifier when IP addresses change
- Difficult to quickly identify which node is which in monitoring dashboards that use hostnames

## 2. Current State

**Relevant Files:**
- `common/scylla-image-setup.service` - Systemd service that runs **before** scylla-server.service
- `common/scylla-image-post-start.service` - Systemd service that runs **after** scylla-server.service for post-start tasks
- `common/scylla_post_start.py` - Python script executed by post-start service (currently only runs user-provided post_start_script)
- `dist/redhat/scylla-machine-image.spec` - RPM package specification
- `dist/debian/debian/` - DEB package configuration

**Current Behavior:**
1. `scylla-image-setup.service` runs before ScyllaDB starts and configures the instance
2. ScyllaDB server starts and generates/loads a server UUID
3. `scylla-image-post-start.service` waits 30 seconds then runs `scylla_post_start.py`
4. Currently, `scylla_post_start.py` only handles user-provided post-start scripts
5. Hostnames remain as initially set (typically IP-based or cloud provider defaults)

**What Needs to Change:**
- Add hostname update functionality to the existing `scylla_post_start.py` script (avoids adding a new systemd unit)
- Retrieve the server UUID from ScyllaDB after it starts
- Update the system hostname to include the UUID, along with `/etc/hosts`
- Update RPM and DEB specs only if new files are added

**Needs Investigation:**
- Exact ScyllaDB REST API endpoint or method to retrieve server UUID/host ID
- Whether to use REST API (`localhost:10000`), `nodetool`, `cqlsh`, or read from a file
- Hostname format: `<original-hostname>-<uuid>` vs `<uuid>-<original-hostname>` vs other
- Whether to update cloud provider instance tags/metadata with the new hostname
- Handling of hostname length limits (63 chars for DNS labels, 255 for FQDN)
- Cloud-init hostname persistence strategy per provider (see Section 7)

## 3. Goals

1. **Create hostname update mechanism:** Extend existing `scylla_post_start.py` to update the instance hostname to include the server UUID
2. **Zero manual intervention:** The hostname update should be completely automatic on every boot
3. **Idempotent operation:** Running the script multiple times should be a no-op when the UUID matches, and should update when the UUID changes (node replacement)
4. **Multi-cloud support:** Work consistently across AWS, GCE, Azure, and OCI, including cloud-init hostname persistence
5. **Package integration:** No new package files needed — extend existing script already in RPM and DEB packages

**Success Metrics:**
- Hostnames on new instances include the server UUID within 60 seconds of ScyllaDB starting
- Existing instances with UUID-enhanced hostnames are not modified on subsequent boots
- No service failures or timeouts in the systemd service

## 4. Implementation Phases

### Phase 1: UUID Retrieval Research and Prototyping
**Description:** Determine the best method to retrieve the server UUID from ScyllaDB and design the hostname format.

**Definition of Done:**
- [ ] Identify the most reliable method to retrieve server UUID (REST API, nodetool, cqlsh, or file)
- [ ] Define hostname format convention (e.g., `ip-10-0-1-42-a1b2c3d4-e5f6-7890-abcd-ef1234567890`)
- [ ] Document hostname length handling strategy for long UUIDs
- [ ] Create proof-of-concept script that can retrieve UUID and format hostname

**Dependencies:** None

**Expected Deliverables:**
- Decision document on UUID retrieval method
- Hostname format specification
- Prototype script

**Open Questions:**
- What hostname format balances readability with DNS compliance?

**Hostname Format Options:**

The hostname should include the server UUID to enable correlation between logs. Here are the proposed options:

1. **UUID-only format**: `<uuid>`
   - Example: `a1b2c3d4-e5f6-7890-abcd-ef1234567890`
   - Pros: Simple, guaranteed unique, no length concerns
   - Cons: Not human-readable, loses cloud provider context (region, IP info)

2. **Original hostname + UUID**: `<original-hostname>-<uuid>`
   - Example: `ip-10-0-1-42-a1b2c3d4-e5f6-7890-abcd-ef1234567890`
   - Pros: Retains original context (IP address), easy to identify instance
   - Cons: Very long (may exceed 63-char DNS label limit), less readable

3. **UUID + short original identifier**: `<uuid>-<region>-<instance-type-hint>`
   - Example: `a1b2c3d4-e5f6-7890-abcd-ef1234567890-us-east-1`
   - Pros: UUID-first for log correlation, includes location context
   - Cons: Still quite long, requires parsing instance metadata

4. **Short prefix + UUID**: `scylla-<uuid>`
   - Example: `scylla-a1b2c3d4-e5f6-7890-abcd-ef1234567890`
   - Pros: Clear service identifier, UUID prominent
   - Cons: Loses instance-specific context

5. **Hybrid: IP-based prefix + UUID** (RECOMMENDED): `ip-<last-two-octets>-<uuid>`
   - Example: `ip-1-42-a1b2c3d4-e5f6-7890-abcd-ef1234567890` (for IP 10.0.1.42)
   - Pros: Compact, includes IP hint for quick identification, UUID for correlation
   - Cons: Still 49+ chars (within DNS limits)

**DNS Label Length Analysis (63-char limit):**
| Cloud | Typical hostname | + UUID suffix | Total | Within limit? |
|-------|-----------------|---------------|-------|---------------|
| AWS | `ip-10-0-1-42` (13) | `-<36-char UUID>` | 50 | Yes |
| GCE | `scylla-enterprise-2024-1-3-x86-64-0` (37) | `-<36-char UUID>` | 74 | **NO** |
| Azure | `scylla-node-0` (14) | `-<36-char UUID>` | 51 | Yes |
| OCI | `scylladb-node` (14) | `-<36-char UUID>` | 51 | Yes |

GCE hostnames can exceed the limit with Option 2. Option 5 (IP hint + UUID) avoids this by discarding the original hostname and constructing a short prefix: `ip-<octet>-<octet>-<uuid>` = max ~46 chars.

**Recommendation**: Option 5 (IP-based prefix + UUID) provides the best balance of:
- Human readability (IP hint for quick instance identification)
- Log correlation (full UUID)
- DNS compliance (under 63 chars even for GCE)
- Cloud-agnostic approach

**Decisions Made:**
- **Primary method:** Use ScyllaDB REST API (`curl http://localhost:10000/storage_service/hostid/local`)
- **Fallback method:** If REST API is unavailable, leave hostname unchanged (no binary file reading)
- **UUID format:** Use full UUID (not shortened) in hostname
- **Hostname format:** To be finalized based on review (recommendation: `ip-<last-octets>-<uuid>`)

### Phase 2: Core Implementation (extend existing post-start service)
**Description:** Add hostname update logic to the existing `scylla_post_start.py` script, which already runs after ScyllaDB starts via `scylla-image-post-start.service`. This avoids creating a new systemd unit and simplifies packaging.

**Rationale for extending existing service:** The `scylla-image-post-start.service` already:
- Runs after `scylla-server.service`
- Executes Python (`scylla_post_start.py`)
- Has retry/delay logic (30s wait)
- Is already packaged in RPM and DEB

Adding hostname update here avoids a new systemd unit, simplifies packaging, and reduces boot-time complexity.

**Definition of Done:**
- [ ] Add `update_hostname_with_uuid()` function to `common/scylla_post_start.py`
- [ ] Function retrieves server UUID from ScyllaDB REST API
- [ ] Function compares current UUID against UUID already in hostname (if any) — updates if UUID changed (handles node replacement)
- [ ] Function updates hostname using `hostnamectl set-hostname`
- [ ] Function updates `/etc/hosts` to map `127.0.0.1` to the new hostname (prevents `sudo` and resolution breakage)
- [ ] Function sets `preserve_hostname: true` in `/etc/cloud/cloud.cfg.d/` to prevent cloud-init from reverting the change on reboot
- [ ] Function handles errors gracefully (ScyllaDB not ready, API failures, etc.)
- [ ] Function logs all operations for debugging

**Dependencies:** Phase 1

**Expected Deliverables:**
- Updated `common/scylla_post_start.py` with hostname update logic

**Idempotency Strategy:**
- Do NOT use a `ConditionPathExists` flag file — this prevents recovery from hostname resets and blocks UUID updates on node replacement.
- Instead, make the script truly idempotent: on every boot, fetch the current UUID, compare with the UUID in the current hostname, and only call `hostnamectl` if they differ.
- This handles: first boot (no UUID in hostname), reboots (UUID matches, no-op), cloud-init resets (UUID missing, re-apply), and node replacement (different UUID, update).

**Node Replacement Handling:**
- When a node is replaced (same IP, new ScyllaDB UUID), the script detects that the current hostname contains a *different* UUID and updates it to the new one.
- The idempotent check uses regex to extract the UUID from the hostname and compares it to the live UUID from the REST API.

### Phase 3: Package Integration
**Description:** Since we extend the existing `scylla_post_start.py` (already packaged), no new systemd unit or packaging changes are required. This phase validates that the updated script works within existing packages.

**Definition of Done:**
- [ ] Verify updated `scylla_post_start.py` is picked up by existing RPM and DEB build processes
- [ ] Verify no new files need to be added to package specs
- [ ] Verify package builds successfully for both RPM and DEB
- [ ] Test installation and uninstallation of packages

**Dependencies:** Phase 2

**Expected Deliverables:**
- Successful package builds (no spec changes expected)

### Phase 4: Testing and Validation
**Description:** Test the implementation across different cloud providers and scenarios.

**Definition of Done:**
- [ ] Test on AWS with new instance launch
- [ ] Test on GCE with new instance launch
- [ ] Test on Azure with new instance launch
- [ ] Test on OCI with new instance launch
- [ ] Verify hostname persists after reboot (cloud-init doesn't revert it)
- [ ] Verify service is idempotent on subsequent boots (no-op when UUID matches)
- [ ] Verify service handles ScyllaDB startup delays gracefully
- [ ] Verify no performance impact on instance startup time
- [ ] Test upgrade scenario from older machine image version
- [ ] Test node replacement scenario (same IP, new UUID — hostname should update)
- [ ] Verify `/etc/hosts` is updated correctly and `sudo` works after hostname change

**Dependencies:** Phase 3

**Expected Deliverables:**
- Test report with results from all cloud providers
- Documentation of any edge cases or issues found

## 5. Testing Requirements

### Unit Testing
- Mock ScyllaDB UUID retrieval and test hostname formatting logic
- Test idempotent behavior (hostname already has matching UUID → no-op)
- Test node replacement behavior (hostname has different UUID → update)
- Test error handling (ScyllaDB not available, network errors, etc.)
- Test hostname length validation and fallback to UUID-only format
- Test `/etc/hosts` update logic (old hostname replaced, not duplicated)

### Integration Testing
- Launch instances on each cloud provider (AWS, GCE, Azure, OCI)
- Verify systemd service starts and completes successfully
- Check `hostnamectl` output shows updated hostname
- Verify hostname appears in logs correctly
- Test that subsequent reboots don't re-run the update

### Manual Testing
- SSH to instance and verify hostname with `hostname` command
- Check systemd journal: `journalctl -u scylla-update-hostname.service`
- Verify ScyllaDB logs show the updated hostname
- Check `/var/log/syslog` or `/var/log/messages` for hostname change events
- Confirm hostname survives instance reboot

### Performance Testing
- Measure time from ScyllaDB start to hostname update completion
- Ensure total time is under 60 seconds
- Verify no impact on overall instance boot time

## 6. Success Criteria

1. **Automatic Hostname Updates:**
   - New instances launched from updated machine images automatically have UUIDs in their hostnames
   - Update completes within 60 seconds of ScyllaDB becoming ready

2. **Idempotent Behavior:**
   - Running the service multiple times doesn't add duplicate UUIDs
   - Existing UUID-enhanced hostnames are preserved

3. **Multi-Cloud Compatibility:**
   - Feature works identically on AWS, GCE, Azure, and OCI
   - No cloud-specific failures or edge cases

4. **No Breaking Changes:**
   - Existing instances without UUID in hostname continue to work
   - Upgrade path from old to new package version is smooth
   - No disruption to ScyllaDB operation

5. **Observable and Debuggable:**
   - Service logs clearly indicate success or failure
   - Hostname format is human-readable and DNS-compliant
   - Operators can easily verify the hostname update occurred

**Validation Steps:**
1. Launch a new instance from the updated machine image
2. Wait for instance to complete first boot
3. Run `hostname` and verify UUID is present
4. Check `journalctl -u scylla-update-hostname.service` for success message
5. Reboot instance and verify hostname persists
6. Verify service doesn't attempt to run again after reboot

## 7. Risk Mitigation

### Potential Blockers

**Risk:** ScyllaDB may not be fully initialized when the service runs
- **Mitigation:** Add retry logic with exponential backoff (e.g., 5s, 10s, 15s)
- **Mitigation:** Check ScyllaDB health endpoint before attempting UUID retrieval
- **Mitigation:** Set appropriate systemd timeout (120 seconds)

**Risk:** Hostname length may exceed DNS limits (63 chars per label)
- **Mitigation:** Use Option 5 format (IP hint + UUID) which caps at ~46 chars — see DNS label analysis in Phase 1
- **Mitigation:** Validate at runtime and log a warning if the constructed hostname exceeds 63 chars, falling back to UUID-only format

**Risk:** Cloud-init reverts hostname on reboot
- **Mitigation:** Write `/etc/cloud/cloud.cfg.d/99-scylla-preserve-hostname.cfg` with `preserve_hostname: true` after updating hostname
- **Mitigation:** This is supported on AWS, GCE, Azure, and OCI (all use cloud-init)
- **Mitigation:** The script runs on every boot and re-applies if cloud-init does revert (belt-and-suspenders)

**Risk:** Hostname change breaks `sudo` and local resolution
- **Mitigation:** Update `/etc/hosts` to map `127.0.0.1` (and `127.0.1.1` on Ubuntu) to the new hostname
- **Mitigation:** Replace the old hostname entry rather than appending, to avoid stale entries

**Risk:** Cloud provider metadata may cache old hostname
- **Mitigation:** Test hostname propagation to cloud provider dashboards
- **Mitigation:** Document any cloud-specific limitations
- **Mitigation:** Consider updating cloud provider tags/metadata if API available

**Risk:** Hostname change may affect networking or SSL certificates
- **Mitigation:** Test with TLS-enabled ScyllaDB clusters
- **Mitigation:** Document that hostname change occurs before cluster joins
- **Mitigation:** Verify ScyllaDB uses IP addresses, not hostnames, for inter-node communication by default

### Rollback Strategies

1. **Script Failure:** If the hostname update function fails, hostname remains unchanged - no impact on ScyllaDB operation
2. **Package Rollback:** Downgrade package to version without hostname update logic in `scylla_post_start.py`
3. **Disable Feature:** Add configuration flag (e.g., in scylla-machine-image user-data) to skip hostname update if problems arise
4. **Revert Hostname:** Remove `/etc/cloud/cloud.cfg.d/99-scylla-preserve-hostname.cfg` and reboot to restore cloud-init default hostname

### Dependencies on External Systems

- **ScyllaDB Server:** Must be running and responsive for UUID retrieval
- **systemd:** Required for service orchestration and dependency management
- **hostnamectl:** Used for hostname updates (available on all supported distros)
- **Python 3:** Required for script execution (already a dependency)

### Compatibility Concerns

**Operating Systems:**
- Primary target is Ubuntu (used across all machine images)
- `hostnamectl` is available and consistent across Ubuntu versions

**ScyllaDB Versions:**
- Must work with both ScyllaDB Enterprise and OSS (the machine-image repo builds images for both)
- REST API endpoint `/storage_service/hostid/local` should be consistent across versions
- Fallback to scylla.yaml reading if REST API endpoint changes

**Cloud Providers:**
- AWS, GCE, Azure, OCI all use cloud-init which manages hostnames
- Cloud-init hostname reversion is handled by writing `preserve_hostname: true` to `/etc/cloud/cloud.cfg.d/`
- The script also re-applies on every boot as a fallback if cloud-init does revert

**Cluster Topology:**
- Single-node vs multi-node clusters
- Seed nodes vs regular nodes (should work the same)
- Impact on node identity if hostname changes after cluster join (should be minimal as ScyllaDB primarily uses IPs)
- **Testing Required:** Verify hostname changes don't break DNS-based configurations in SCT (Scylla Cluster Tests)
- ScyllaDB can be configured to use DNS addresses - must ensure hostname changes are compatible
