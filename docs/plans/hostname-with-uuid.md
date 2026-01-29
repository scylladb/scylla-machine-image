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
- `/home/runner/work/scylla-machine-image/scylla-machine-image/common/scylla-image-setup.service` - Systemd service that runs **before** scylla-server.service
- `/home/runner/work/scylla-machine-image/scylla-machine-image/common/scylla-image-post-start.service` - Systemd service that runs **after** scylla-server.service for post-start tasks
- `/home/runner/work/scylla-machine-image/scylla-machine-image/common/scylla_post_start.py` - Python script executed by post-start service (currently only runs user-provided post_start_script)
- `/home/runner/work/scylla-machine-image/scylla-machine-image/dist/redhat/scylla-machine-image.spec` - RPM package specification
- `/home/runner/work/scylla-machine-image/scylla-machine-image/dist/debian/debian/` - DEB package configuration

**Current Behavior:**
1. `scylla-image-setup.service` runs before ScyllaDB starts and configures the instance
2. ScyllaDB server starts and generates/loads a server UUID
3. `scylla-image-post-start.service` waits 30 seconds then runs `scylla_post_start.py`
4. Currently, `scylla_post_start.py` only handles user-provided post-start scripts
5. Hostnames remain as initially set (typically IP-based or cloud provider defaults)

**What Needs to Change:**
- Add functionality to retrieve the server UUID from ScyllaDB after it starts
- Update the system hostname to include the UUID if not already present
- Package the new service/script in both RPM and DEB formats

**Needs Investigation:**
- Exact ScyllaDB REST API endpoint or method to retrieve server UUID/host ID
- Whether to use REST API (`localhost:10000`), `nodetool`, `cqlsh`, or read from a file
- Hostname format: `<original-hostname>-<uuid>` vs `<uuid>-<original-hostname>` vs other
- Whether to update cloud provider instance tags/metadata with the new hostname
- Handling of hostname length limits (63 chars for DNS labels, 255 for FQDN)

## 3. Goals

1. **Create hostname update mechanism:** Implement a systemd service and script that updates the instance hostname to include the server UUID
2. **Zero manual intervention:** The hostname update should be completely automatic on first boot
3. **Idempotent operation:** Running the service multiple times should not corrupt the hostname or create duplicate UUIDs
4. **Multi-cloud support:** Work consistently across AWS, GCE, Azure, and OCI
5. **Package integration:** Include the new service in both RPM and DEB packages

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
- Should we use ScyllaDB REST API (`curl http://localhost:10000/storage_service/hostid/local`)?
- Should we query `system.local` table via `cqlsh -e "SELECT host_id FROM system.local"`?
- Should we use `nodetool info` and parse the output?
- What hostname format balances readability with DNS compliance?
- Should the full UUID be used or a shortened version (first 8 chars)?

### Phase 2: Core Script Implementation
**Description:** Create the Python script that retrieves the UUID and updates the hostname.

**Definition of Done:**
- [ ] Create `scylla_update_hostname.py` script in `common/` directory
- [ ] Script retrieves server UUID from ScyllaDB using chosen method
- [ ] Script checks if hostname already contains UUID (idempotent check)
- [ ] Script updates hostname using `hostnamectl set-hostname` if needed
- [ ] Script handles errors gracefully (ScyllaDB not ready, API failures, etc.)
- [ ] Script logs all operations for debugging
- [ ] Script exits cleanly after completing its task

**Dependencies:** Phase 1

**Expected Deliverables:**
- `/home/runner/work/scylla-machine-image/scylla-machine-image/common/scylla_update_hostname.py`
- Inline documentation and error handling

### Phase 3: Systemd Service Creation
**Description:** Create the systemd service unit that runs the hostname update script.

**Definition of Done:**
- [ ] Create `scylla-update-hostname.service` systemd unit file
- [ ] Service is ordered `After=scylla-server.service`
- [ ] Service type is `oneshot` (runs once and terminates)
- [ ] Service has appropriate timeout (60-120 seconds)
- [ ] Service waits for ScyllaDB to be fully ready (may need delay or retry logic)
- [ ] Service is enabled to run on first boot only (use `ConditionPathExists` flag)
- [ ] Service does not block system shutdown

**Dependencies:** Phase 2

**Expected Deliverables:**
- `/home/runner/work/scylla-machine-image/scylla-machine-image/common/scylla-update-hostname.service`

### Phase 4: Package Integration
**Description:** Integrate the new service and script into RPM and DEB packages.

**Definition of Done:**
- [ ] Update `dist/redhat/scylla-machine-image.spec` to install the new service and script
- [ ] Update `dist/debian/debian/scylla-machine-image.install` to include new files
- [ ] Update `dist/debian/debian/scylla-update-hostname.service` symlink/file
- [ ] Add systemd enable/disable hooks in package post-install scripts
- [ ] Verify package builds successfully for both RPM and DEB
- [ ] Test installation and uninstallation of packages

**Dependencies:** Phase 3

**Expected Deliverables:**
- Updated RPM spec file
- Updated DEB control files
- Successful package builds

### Phase 5: Testing and Validation
**Description:** Test the implementation across different cloud providers and scenarios.

**Definition of Done:**
- [ ] Test on AWS with new instance launch
- [ ] Test on GCE with new instance launch
- [ ] Test on Azure with new instance launch
- [ ] Test on OCI with new instance launch
- [ ] Verify hostname persists after reboot
- [ ] Verify service doesn't run on subsequent boots (idempotent)
- [ ] Verify service handles ScyllaDB startup delays gracefully
- [ ] Verify no performance impact on instance startup time
- [ ] Test upgrade scenario from older machine image version

**Dependencies:** Phase 4

**Expected Deliverables:**
- Test report with results from all cloud providers
- Documentation of any edge cases or issues found

## 5. Testing Requirements

### Unit Testing
- Mock ScyllaDB UUID retrieval and test hostname formatting logic
- Test idempotent behavior (hostname already has UUID)
- Test error handling (ScyllaDB not available, network errors, etc.)
- Test hostname length validation and truncation if needed

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
- **Mitigation:** Test with longest possible hostname + UUID combination
- **Mitigation:** Consider using shortened UUID (first 8 characters) if needed
- **Mitigation:** Document hostname length constraints in the code

**Risk:** Cloud provider metadata may cache old hostname
- **Mitigation:** Test hostname propagation to cloud provider dashboards
- **Mitigation:** Document any cloud-specific limitations
- **Mitigation:** Consider updating cloud provider tags/metadata if API available

**Risk:** Hostname change may affect networking or SSL certificates
- **Mitigation:** Test with TLS-enabled ScyllaDB clusters
- **Mitigation:** Document that hostname change occurs before cluster joins
- **Mitigation:** Verify ScyllaDB uses IP addresses, not hostnames, for inter-node communication by default

### Rollback Strategies

1. **Service Failure:** If service fails, hostname remains unchanged - no impact
2. **Package Rollback:** Downgrade package, remove service unit, revert hostname manually if needed
3. **Disable Feature:** Add configuration flag to skip hostname update if problems arise
4. **ConditionPathExists Flag:** Use flag file to ensure service only runs once, preventing repeated issues

### Dependencies on External Systems

- **ScyllaDB Server:** Must be running and responsive for UUID retrieval
- **systemd:** Required for service orchestration and dependency management
- **hostnamectl:** Used for hostname updates (available on all supported distros)
- **Python 3:** Required for script execution (already a dependency)

### Compatibility Concerns

**Operating Systems:**
- Must work on Ubuntu, Debian, RHEL, Rocky Linux, and other supported distros
- `hostnamectl` availability and behavior may vary slightly across distros

**ScyllaDB Versions:**
- Verify UUID retrieval method works across ScyllaDB versions (OSS and Enterprise)
- REST API endpoints may differ between versions - needs testing

**Cloud Providers:**
- AWS, GCE, Azure, OCI may have different hostname initialization behaviors
- Some clouds may override hostname changes through cloud-init - needs testing
- Consider cloud-init integration to make changes persistent

**Cluster Topology:**
- Single-node vs multi-node clusters
- Seed nodes vs regular nodes (should work the same)
- Impact on node identity if hostname changes after cluster join (should be minimal as ScyllaDB uses IPs)
