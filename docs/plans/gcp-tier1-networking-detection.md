# GCP Tier 1 Networking Detection from Within a Running VM

## 1. Problem Statement

PR #933 adds GCP `stream_io_throughput_mb_per_sec` configuration based on instance type, but conservatively uses only **default** (non-Tier 1) bandwidth values. The GCP network params JSON includes Tier 1 values (50–100 Gbps for N2/N2D ≥48 vCPU, 200 Gbps for Z3), but they go unused at runtime.

**Why this matters**: Scylla's streaming bandwidth estimation is 75% of network capacity. If a VM has Tier 1 enabled (e.g., 100 Gbps instead of 32 Gbps), using the default value under-provisions streaming by 3x, leaving significant performance on the table.

**Current blocker**: The PR description states "networkPerformanceConfig.totalEgressBandwidthTier is not reliably exposed via the GCP metadata server." The review comment (fruch) suggests adding a cloud-init configuration value as a workaround.

**This plan**: Research and validate **multiple detection methods** from within a single running VM (no cross-VM access), prove them on real GCP hardware, and implement the best approach combining auto-detection with explicit override.

## 2. Current State

### Files involved (PR #933 branch: `copilot/create-gcp-net-params-json`)

- **`lib/param_estimation.py`** (lines 53–66) — GCP branch uses `instance_info[0][1]` (default bandwidth) and ignores `instance_info[0][2]` (Tier 1 bandwidth):
  ```python
  elif is_gce():
      instance_type = cloud_instance.instancetype
      with open("/opt/scylladb/scylla-machine-image/gcp_net_params.json") as f:
          netinfo = json.load(f)
          instance_info = [info for info in netinfo if info[0] == instance_type]
          if instance_info:
              net_bw_gbps = instance_info[0][1]  # default bandwidth only
              net_bw = int(net_bw_gbps * 1000 * 1000 * 1000)
  ```

- **`common/gcp_net_params.json`** — Format: `[instance_type, default_bw_gbps, tier1_bw_gbps]`
  - Example: `["n2-standard-48", 32.0, 50.0]` — Tier 1 data exists but is unused
  - `null` for Tier 1 means the instance doesn't support Tier 1

- **`lib/scylla_cloud.py`** — `GcpInstance` class provides `instancetype` via metadata server

- **`common/scylla_configure.py`** (lines 138–140) — Integration point where `estimate_streaming_bandwidth()` feeds into `scylla.yaml`

### How other clouds handle bandwidth detection

| Cloud | Method | File |
|-------|--------|------|
| AWS | Lookup instance type in JSON → fixed baseline Gbps | `aws_net_params.json` |
| Azure | Lookup instance type in JSON → `Network_Limit_Gbps` | `azure_net_params.json` |
| OCI | Lookup shape + OCPU scaling for flex shapes | `oci_net_params.json` |
| GCP (current) | Lookup instance type → default bandwidth only | `gcp_net_params.json` |

**Key observation**: None of the other clouds have a "network tier" concept. They all have a single definitive bandwidth value per instance type. GCP is unique in having two possible bandwidths per instance.

## 3. Goals

1. **Validate** at least 5 detection methods on real GCP hardware (Tier 1 enabled and disabled)
2. **Determine** which method is reliable, requires no special IAM scopes, and works from a freshly launched VM
3. **Implement** a detection strategy that:
   - Auto-detects Tier 1 when possible (no user intervention)
   - Falls back to explicit configuration when auto-detection is unavailable
   - Defaults to conservative (non-Tier 1) bandwidth when uncertain
4. **Document** findings with evidence from real VMs

### Success metrics
- [x] At least one method reliably distinguishes Tier 1 from default on real hardware
- [x] Detection works without `compute.readonly` scope (ideal) or gracefully falls back
- [x] `ethtool` speed comparison validated against known bandwidth values
- [ ] Implementation merged into PR #933

## 4. Implementation Phases

### Phase 1: Spin Up Test VMs and Validate Detection Methods

**Description**: Create GCP VMs (one with Tier 1, one without) and run each detection method to gather empirical evidence.

**Deliverables**:
- Shell script to provision test VMs
- Evidence output from each detection method on both VMs
- Decision matrix: which methods work, which don't

**Steps**:

```bash
# 1. Create standard networking VM (N2, 48 vCPUs, NO Tier 1)
gcloud compute instances create tier1-test-standard \
  --zone=us-central1-a \
  --machine-type=n2-standard-48 \
  --network-interface=nic-type=GVNIC \
  --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud \
  --scopes=compute-ro \
  --metadata=startup-script='#!/bin/bash
    apt-get update && apt-get install -y ethtool jq'

# 2. Create Tier 1 networking VM (N2, 48 vCPUs, WITH Tier 1)
gcloud compute instances create tier1-test-enabled \
  --zone=us-central1-a \
  --machine-type=n2-standard-48 \
  --network-interface=nic-type=GVNIC \
  --network-performance-configs=total-egress-bandwidth-tier=TIER_1 \
  --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud \
  --scopes=compute-ro \
  --metadata=startup-script='#!/bin/bash
    apt-get update && apt-get install -y ethtool jq'
```

**Detection tests to run on BOTH VMs** (via `gcloud compute ssh`):

```bash
#!/bin/bash
echo "=== TEST 1: ethtool link speed ==="
ethtool eth0 | grep Speed

echo "=== TEST 2: Compute Engine API self-query ==="
TOKEN=$(curl -s "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token" \
  -H "Metadata-Flavor: Google" | jq -r '.access_token')
PROJECT=$(curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/project/project-id")
ZONE=$(curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/zone" | rev | cut -d/ -f1 | rev)
INSTANCE=$(curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/name")
curl -s "https://compute.googleapis.com/compute/v1/projects/$PROJECT/zones/$ZONE/instances/$INSTANCE?fields=networkPerformanceConfig" \
  -H "Authorization: Bearer $TOKEN" | jq .

echo "=== TEST 3: NIC type from metadata ==="
curl -s -H "Metadata-Flavor: Google" \
  "http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/nic-type"

echo "=== TEST 4: gVNIC driver info ==="
ethtool -i eth0
modinfo gve 2>/dev/null | grep -E "^version|^description"

echo "=== TEST 5: Full network interface metadata (recursive) ==="
curl -s -H "Metadata-Flavor: Google" \
  "http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/?recursive=true" | jq .

echo "=== TEST 6: Check for undocumented bandwidth-tier endpoint ==="
curl -sv -H "Metadata-Flavor: Google" \
  "http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/bandwidth-tier" 2>&1

echo "=== TEST 7: Machine type + vCPU count ==="
curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/machine-type"
nproc

echo "=== TEST 8: Custom metadata check ==="
curl -s -H "Metadata-Flavor: Google" \
  "http://metadata.google.internal/computeMetadata/v1/instance/attributes/" 2>/dev/null
```

**Cleanup**:
```bash
gcloud compute instances delete tier1-test-standard tier1-test-enabled \
  --zone=us-central1-a --quiet
```

**Definition of Done**:
- [x] Both VMs provisioned and accessible
- [x] All 8 tests executed on both VMs
- [x] Results captured as text files in `docs/plans/evidence/`
- [x] Clear differentiation (or lack thereof) documented per method

**Dependencies**: GCP project with billing, compute API enabled, quota for N2 48-vCPU instances

---

### Phase 2: Additional Test Without compute-ro Scope

**Description**: Re-run tests with a VM that has NO `compute-ro` scope to verify which methods work without API access.

```bash
gcloud compute instances create tier1-test-no-scope \
  --zone=us-central1-a \
  --machine-type=n2-standard-48 \
  --network-interface=nic-type=GVNIC \
  --network-performance-configs=total-egress-bandwidth-tier=TIER_1 \
  --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud \
  --no-scopes \
  --metadata=startup-script='#!/bin/bash
    apt-get update && apt-get install -y ethtool jq'
```

**Definition of Done**:
- [x] Confirmed which methods work with zero API scopes
- [x] `ethtool` speed validated as scope-independent
- [x] Documented which methods fail gracefully vs hard-fail

**Dependencies**: Phase 1 completed

---

### Phase 3: Implement Detection Logic

**Description**: Based on Phase 1–2 evidence, implement Tier 1 detection in `lib/param_estimation.py`.

**Proposed implementation strategy** (pending validation):

```python
elif is_gce():
    instance_type = cloud_instance.instancetype
    with open("/opt/scylladb/scylla-machine-image/gcp_net_params.json") as f:
        netinfo = json.load(f)
        instance_info = [info for info in netinfo if info[0] == instance_type]
        if instance_info:
            default_bw_gbps = instance_info[0][1]
            tier1_bw_gbps = instance_info[0][2]  # may be None

            # Determine if Tier 1 is active
            use_tier1 = _detect_gcp_tier1(default_bw_gbps, tier1_bw_gbps)

            net_bw_gbps = tier1_bw_gbps if (use_tier1 and tier1_bw_gbps) else default_bw_gbps
            net_bw = int(net_bw_gbps * 1000 * 1000 * 1000)
```

**Detection function (tiered fallback)**:

```python
def _detect_gcp_tier1(default_bw_gbps: float, tier1_bw_gbps: float | None) -> bool:
    """Detect Tier 1 networking. Conservative: returns False if uncertain."""
    if tier1_bw_gbps is None:
        return False  # Instance type doesn't support Tier 1

    # Method A: Check explicit user override (cloud-init / instance metadata)
    tier1_override = _check_tier1_metadata_override()
    if tier1_override is not None:
        return tier1_override

    # Method B: ethtool reported speed vs known default
    reported_speed_gbps = _get_ethtool_speed()
    if reported_speed_gbps and reported_speed_gbps > default_bw_gbps:
        return True

    # Method C: Compute Engine API (may fail if no scope)
    api_tier = _query_compute_api_tier()
    if api_tier == "TIER_1":
        return True

    return False  # Conservative default
```

**Definition of Done**:
- [x] Detection function implemented with tiered fallback
- [x] Cloud-init/user-data override supported (explicit `tier1_networking: true`)
- [x] Graceful fallback to default bandwidth on any detection failure
- [x] Unit tests cover all detection paths

**Dependencies**: Phase 1–2 evidence confirms detection method reliability

---

### Phase 4: Add User-Data Override Support

**Description**: Allow users to explicitly declare Tier 1 networking via cloud-init user-data or instance metadata (addresses fruch's review comment).

**User-data format**:
```yaml
scylla_machine_image:
  tier1_networking: true
```

Or via GCP instance metadata:
```bash
gcloud compute instances create ... \
  --metadata=scylla_tier1_networking=true
```

**Integration point**: `common/scylla_configure.py` — read user-data, pass to `param_estimation.py`

**Definition of Done**:
- [x] User-data key `tier1_networking` recognized and respected
- [x] Instance metadata `scylla_tier1_networking` as fallback
- [x] Override always wins over auto-detection
- [x] Documented in user-facing docs

**Dependencies**: Phase 3 completed

---

### Phase 5: End-to-End Validation

**Description**: Build the full image with detection logic, launch with and without Tier 1, verify `scylla.yaml` has correct `stream_io_throughput_mb_per_sec` value.

```bash
# Build image with changes
packer/build_image.sh --target gce --repo <repo-url> --arch x86_64

# Launch from image WITH Tier 1
gcloud compute instances create scylla-tier1-e2e \
  --zone=us-central1-a \
  --machine-type=n2-standard-48 \
  --network-interface=nic-type=GVNIC \
  --network-performance-configs=total-egress-bandwidth-tier=TIER_1 \
  --image=<built-image-name> \
  --image-project=<project>

# Verify scylla.yaml
gcloud compute ssh scylla-tier1-e2e -- \
  "grep stream_io_throughput_mb_per_sec /etc/scylla/scylla.yaml"
# Expected: stream_io_throughput_mb_per_sec: 4470 (for 50 Gbps x 0.75 / 8 / 1.048576)
```

**Definition of Done**:
- [ ] Image built with detection logic
- [ ] Tier 1 VM shows higher `stream_io_throughput_mb_per_sec` than default VM
- [ ] Values match expected calculations from `gcp_net_params.json`
- [ ] No regressions: non-Tier 1 VMs still get correct default values

**Dependencies**: Phase 3–4 completed, GCE image build pipeline

## 5. Testing Requirements

### Unit Tests (Phase 3)
- Mock `subprocess.check_output` for ethtool returns
- Mock metadata server responses
- Mock Compute Engine API responses (success and failure)
- Test: Tier 1 detected → uses `tier1_bw_gbps`
- Test: Tier 1 not detected → uses `default_bw_gbps`
- Test: Detection fails gracefully → uses `default_bw_gbps`
- Test: User override `true` → forces Tier 1 regardless of detection
- Test: User override `false` → forces default regardless of detection
- Test: Instance type with `null` Tier 1 → always default

### Integration Tests (Phase 5)
- Real GCP VM with Tier 1: verify `scylla.yaml` value
- Real GCP VM without Tier 1: verify `scylla.yaml` value
- VM with no scopes: verify detection degrades gracefully
- VM with explicit override: verify override wins

### Manual Validation (Phase 1–2)
- Capture raw output from all 8 detection tests
- Document exact output differences between standard and Tier 1 VMs
- Save evidence to `docs/plans/evidence/tier1-standard-output.txt` and `docs/plans/evidence/tier1-enabled-output.txt`

## 6. Success Criteria

- [x] **Primary**: `ethtool` link speed reliably distinguishes Tier 1 from default on real hardware
- [x] **Secondary**: At least one backup method (API or metadata) confirmed working
- [x] **Implementation**: Auto-detection works on fresh VM without special configuration
- [x] **Override**: Users can force Tier 1 bandwidth via cloud-init user-data
- [x] **Conservative**: Any detection failure defaults to non-Tier 1 (never over-provisions)
- [x] **Tests**: All unit tests pass, including edge cases and failure modes
- [x] **Backward compatible**: Non-Tier 1 instances behave identically to current PR code

## 7. Risk Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| `ethtool` speed doesn't differ between Tier 1 and default | High — primary detection method fails | Phase 1 validates before implementation; fall back to API method |
| Compute Engine API requires scope not available on all VMs | Medium — API method unreliable | Tiered fallback: ethtool first, API second, override always works |
| GCP changes hypervisor behavior in future | Low — ethtool reports wrong speed | Conservative default + user override ensures correctness |
| Test VMs cost money | Low | Use preemptible instances, delete immediately after tests |
| N2 48-vCPU quota unavailable | Medium — can't run Phase 1 | Use alternative Tier 1-eligible types (C2, C3) or request quota increase |
| `ethtool` not installed on minimal images | Low | Package already included in Scylla images; add as dependency in spec/rules |
| Metadata `bandwidth-tier` endpoint doesn't exist | Expected — unconfirmed | Test validates; not relied upon in implementation |

### Rollback Strategy
If detection proves unreliable, the fallback is already implemented: use `default_bw_gbps` (the current PR behavior). The explicit override via user-data (Phase 4) provides a guaranteed-correct path that doesn't depend on any detection.

### Open Questions
- **Open Question**: Does GCP expose link speed via ethtool as the *allocated* bandwidth or the *maximum possible* bandwidth for the NIC? Phase 1 will resolve this.
- **Open Question**: Is there a difference in ethtool output between a Tier 1-eligible VM that has Tier 1 enabled vs one that doesn't? Phase 1 will resolve this.
- **Open Question**: Does the undocumented `/instance/network-interfaces/0/bandwidth-tier` metadata endpoint exist? Phase 1 Test 6 will confirm.
