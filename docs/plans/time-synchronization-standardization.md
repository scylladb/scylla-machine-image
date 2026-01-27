# Time Synchronization Standardization Plan

## Summary

This plan addresses time synchronization consistency across all cloud providers (AWS, GCP, Azure, OCI) in scylla-machine-image.

**Key Issues:**
- **Uniformity**: Configuration drift exists where AWS uses `sources.d` (vendor default) while GCP uses `chrony.conf`. This plan enforces a single source of truth strategy by wiping vendor defaults and using a templated `chrony.conf` for all providers.
- **Startup Safety**: ScyllaDB requires time to be synchronized before starting. This plan strictly enforces this via `chrony-wait.service` and systemd overrides.
- **Cloud Coverage**: Covers AWS, GCP, Azure, and OCI with cloud-specific internal time sources for optimal performance.

## Objective

Align the time synchronization strategy across AWS, GCE, Azure, and OCI in scylla-machine-image by:

1. **Standardizing on Chrony** as the NTP daemon across all cloud platforms.
2. **Enforcing Uniformity**: Cleaning up vendor-default configs (e.g., Ubuntu's `sources.d` entries) to ensure a consistent configuration structure across all clouds.
3. **Configuring cloud-specific internal time sources** (Stratum 1/2) for low latency and high reliability.
4. **Enforcing a "Wait-for-Sync" boot sequence** to prevent ScyllaDB from starting before the clock is stabilized.

## Part 1: Cloud-Specific Chrony Configuration

For all clouds, the base image actions are:

1. Install chrony.
2. **CRITICAL**: Remove legacy/vendor config files to prevent drift:
   - `rm -f /etc/chrony/sources.d/*`
   - `rm -f /etc/chrony/conf.d/*`
3. Place the new, cloud-specific configuration at `/etc/chrony.conf` (or `/etc/chrony/chrony.conf` depending on distro).

### 1. AWS (Amazon Web Services)

AWS provides the Amazon Time Sync Service via a link-local IP address.

**Official Documentation:**
- [Set the time reference on your EC2 instance to use the local Amazon Time Sync Service](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/configure-ec2-ntp.html)
- [Precision clock and time synchronization on your EC2 instance](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/set-time.html)

**Config Directive:**

```conf
# Amazon Time Sync Service
# Official AWS Documentation: https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/configure-ec2-ntp.html
server 169.254.169.123 prefer iburst minpoll 4 maxpoll 4
```

**Notes:**
- The Amazon Time Sync Service is accessible from all EC2 instances via the link-local IPv4 address `169.254.169.123` (and `fd00:ec2::123` for IPv6 on Nitro instances).
- This service uses a fleet of satellite-connected and atomic reference clocks in each AWS region.
- No internet connectivity required; accessible even in isolated VPC subnets.
- Implements leap second smearing for predictable time changes.
- **Poll settings**: `minpoll 4 maxpoll 4` (16 seconds) is AWS's official recommendation for maintaining high accuracy with the Amazon Time Sync Service. This frequent polling is justified because you're using AWS's internal, highly-available service (not rate-limiting public NTP servers).

**Fallback:**
Keep default pools (e.g., `2.amazon.pool.ntp.org`) as secondary sources if desired, but the primary should be the Time Sync Service.

### 2. GCE (Google Compute Engine)

Google exposes a metadata server that acts as an NTP server.

**Official Documentation:**
- [Configure NTP on a VM | Compute Engine Documentation](https://cloud.google.com/compute/docs/instances/configure-ntp)
- [Frequently Asked Questions | Public NTP | Google for Developers](https://developers.google.com/time/faq)

**Config Directive:**

```conf
# Google internal metadata server
# Official GCP Documentation: https://cloud.google.com/compute/docs/instances/configure-ntp
server metadata.google.internal prefer iburst
```

**Notes:**
- All GCE VMs are preconfigured to use `metadata.google.internal` for NTP by default.
- Uses Google's leap smear approach, introducing leap seconds gradually over a 24-hour window.
- **IMPORTANT**: Google recommends **NOT** mixing this internal server with external pool NTP servers (e.g., `pool.ntp.org`) to avoid unpredictable time changes, especially during leap seconds.
- Ensures accurate, stable, and consistent timekeeping within Google Cloud.

### 3. Azure

Azure VMs use a precision clock device (PTP - Precision Time Protocol) mapped from the hypervisor.

**Official Documentation:**
- [Time sync for Linux VMs in Azure](https://learn.microsoft.com/en-us/azure/virtual-machines/linux/time-sync)

**Config Directive:**

```conf
# Azure Hyper-V PTP Source
# Official Azure Documentation: https://learn.microsoft.com/en-us/azure/virtual-machines/linux/time-sync
refclock PHC /dev/ptp_hyperv poll 3 dpoll -2 offset 0 stratum 2
```

**Fallback:**

```conf
# Public NTP fallback (Azure does NOT provide NTP at 169.254.169.254)
server time.windows.com iburst
```

**Notes:**
- Azure exposes a PTP hardware clock as `/dev/ptp_hyperv` (or `/dev/ptp0` on some systems) based on IEEE 1588 PTP standard.
- Provides higher accuracy than traditional NTP, synchronized to Microsoft's Stratum 1 time sources backed by GPS antennas.
- Recent Azure Linux images use `chronyd` with `/dev/ptp_hyperv` by default.
- **Poll settings**: `poll 3` (8 seconds) provides reliable polling without excessive load. `dpoll -2` enables sub-second polling for improved accuracy with virtualized clocks.
- **Stratum 2**: Marks the clock as authoritative but acknowledges it's not a physical stratum 0 device.
- **Fallback**: Azure does NOT provide NTP at 169.254.169.254 (that's the metadata service). Use `time.windows.com` or `pool.ntp.org` as fallback.
- **Udev/Systemd ordering**: Ensure `/dev/ptp_hyperv` exists before `chronyd` starts. May require systemd device dependencies to avoid startup failures.

### 4. OCI (Oracle Cloud Infrastructure)

OCI provides a link-local NTP server.

**Official Documentation:**
- [Configuring the Oracle Cloud Infrastructure NTP Service for an Instance](https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/configuringntpservice.htm)
- [Release Notes – NTP Server](https://docs.oracle.com/en-us/iaas/releasenotes/changes/cbc6876a-ac5f-4716-9ba2-fb3d693b5258/)

**Config Directive:**

```conf
# OCI Internal NTP
# Official OCI Documentation: https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/configuringntpservice.htm
server 169.254.169.254 prefer iburst
```

**Notes:**
- The Oracle NTP service uses `169.254.169.254` for time synchronization within OCI.
- Fully managed, secure, and highly available with redundant Stratum 1 devices in every availability domain.
- Recent Oracle Linux images are pre-configured with Chrony to use this service.
- For manual setup on older images, ensure firewall allows UDP port 123 to `169.254.169.254`.

### Testing for Part 1

**Manual Testing:**
- Verify chrony package is installed: `dpkg -l | grep chrony` (Debian/Ubuntu) or `rpm -qa | grep chrony` (RHEL/CentOS)
- Confirm vendor config directories are empty: `ls -la /etc/chrony/sources.d/` and `ls -la /etc/chrony/conf.d/` should show no files
- Verify cloud-specific chrony.conf exists: `test -f /etc/chrony/chrony.conf || test -f /etc/chrony.conf`
- Check chrony.conf contains correct cloud-specific server/refclock:
  - AWS: `grep "169.254.169.123" /etc/chrony/chrony.conf` or `/etc/chrony.conf`
  - GCP: `grep "metadata.google.internal" /etc/chrony/chrony.conf` or `/etc/chrony.conf`
  - Azure: `grep "ptp_hyperv" /etc/chrony/chrony.conf` or `/etc/chrony.conf`
  - OCI: `grep "169.254.169.254" /etc/chrony/chrony.conf` or `/etc/chrony.conf`
- Verify chrony service is enabled: `systemctl is-enabled chrony`
- Check chrony is running: `systemctl status chrony`
- Verify time synchronization is working: `chronyc sources -v` should show the configured source
- Confirm no configuration conflicts: `chronyc sources` should not show unexpected NTP servers

**Automated Testing (Unit Test Ideas):**
- **Test cloud detection**: Mock cloud metadata service and verify correct chrony template is selected
- **Test config file cleanup**: Create dummy files in `/etc/chrony/sources.d/` and `/etc/chrony/conf.d/`, run cleanup, verify they're removed
- **Test template rendering**: Given cloud type (aws/gce/azure/oci), verify generated chrony.conf contains correct server/refclock directives
- **Test config validation**: Parse generated chrony.conf and assert required directives are present (server/refclock, poll settings)
- **Test path detection**: Test logic that determines whether to use `/etc/chrony.conf` vs `/etc/chrony/chrony.conf` based on distro

**Integration Testing:**
- Boot instance in each cloud (AWS, GCP, Azure, OCI)
- Run `chronyc tracking` and verify:
  - Reference ID matches expected source (AWS Time Sync, metadata.google.internal, PTP, OCI NTP)
  - System time is synchronized (Leap status: Normal)
  - Stratum is 2 or 3 (depending on cloud)
- Measure time drift over 24 hours, ensure < 100ms offset

## Part 2: Universal Chrony Settings

The following settings are applied universally across all clouds to ensure rapid convergence and reliable timekeeping:

```conf
# Allow the system clock to be stepped in the first 3 updates
# if its offset is larger than 1 second.
makestep 1.0 3

# Record the rate at which the system clock gains/loses time.
driftfile /var/lib/chrony/drift

# Enable kernel synchronization of the real-time clock (RTC).
rtcsync
```

**Explanation:**
- **`makestep 1.0 3`**: Allows the system to "step" (jump) the clock if the offset is > 1 second during the first 3 updates. This ensures rapid synchronization on boot.
- **`driftfile`**: Records clock drift to improve accuracy after reboots.
- **`rtcsync`**: Keeps the hardware clock (RTC) synchronized with the system clock.

### Testing for Part 2

**Manual Testing:**
- Verify universal settings are in chrony.conf: `grep -E "makestep|driftfile|rtcsync" /etc/chrony/chrony.conf` (or `/etc/chrony.conf`)
- Check makestep setting is correct: `grep "makestep 1.0 3" /etc/chrony/chrony.conf` or `/etc/chrony.conf`
- Verify driftfile path: `grep "driftfile /var/lib/chrony/drift" /etc/chrony/chrony.conf` or `/etc/chrony.conf`
- Confirm rtcsync is enabled: `grep "rtcsync" /etc/chrony/chrony.conf` or `/etc/chrony.conf`
- Test makestep functionality: Set incorrect time, restart chrony, verify it steps the clock (offset > 1s corrected immediately)
- Verify driftfile exists after chrony runs: `test -f /var/lib/chrony/drift`
- Check RTC sync: `hwclock --show` and compare with `date` - should be within seconds

**Automated Testing (Unit Test Ideas):**
- **Test config parsing**: Parse chrony.conf and verify all three universal directives (makestep, driftfile, rtcsync) are present
- **Test makestep parameters**: Verify makestep directive has correct threshold (1.0) and update limit (3)
- **Test driftfile path**: Verify driftfile path is absolute and points to writable location
- **Mock time step scenario**: Simulate large time offset (>1s) and verify makestep would be triggered
- **Test config merge**: Verify universal settings are present in all cloud-specific templates

**Integration Testing:**
- Boot instance with intentionally wrong clock (offset > 1 second)
- Verify chrony steps the clock within first 3 updates (check `journalctl -u chrony` for step messages)
- After synchronization, verify driftfile is created and contains drift rate
- Reboot instance and verify clock uses driftfile for faster convergence
- Verify hardware clock (RTC) is updated: compare `hwclock --show` with system time

## Part 3: Service Enforcement (Startup Alignment)

We must ensure `scylla-server` waits for a synchronized clock before starting.

### Step 1: Enable `chrony-wait.service`

Enable the standard service that blocks until chrony is synced.

**Action:**

```bash
systemctl enable chrony-wait.service
```

**Purpose:**
- `chrony-wait.service` is a systemd service that waits until Chrony reports the clock is synchronized.
- This service is ordered before `time-sync.target`, ensuring that targets depending on time synchronization will wait appropriately.

### Step 2: Configure scylla-server Dependencies

Create a drop-in override to force Scylla to wait for the time sync target.

**File Path:** `/etc/systemd/system/scylla-server.service.d/10-time-sync.conf`

**Content:**

```ini
[Unit]
# Ensure Scylla starts after the system clock is synchronized
After=time-sync.target
# Strongly request that time-sync is active
Wants=time-sync.target
```

**Explanation:**
- **`After=time-sync.target`**: Ensures `scylla-server` starts only after `time-sync.target` is reached (which `chrony-wait.service` satisfies).
- **`Wants=time-sync.target`**: Declares a weak dependency, requesting that `time-sync.target` is activated.

### Step 3: Verify chrony-wait Configuration

Ensure the wait service timeout is sufficient (default ~60s is usually adequate for most environments).

**Verification:**

```bash
systemctl status chrony-wait.service
chronyc tracking
```

Check that:
- `chrony-wait.service` is enabled and will run on boot.
- `chronyc tracking` shows the system clock is synchronized (look for "Leap status: Normal").

### Testing for Part 3

**Manual Testing:**
- Verify chrony-wait.service is enabled: `systemctl is-enabled chrony-wait.service`
- Check systemd override exists: `test -f /etc/systemd/system/scylla-server.service.d/10-time-sync.conf`
- Verify override content: `cat /etc/systemd/system/scylla-server.service.d/10-time-sync.conf | grep -E "After=time-sync.target|Wants=time-sync.target"`
- Check scylla-server dependencies include time-sync.target: `systemctl show scylla-server.service | grep -E "After=.*time-sync.target"`
- Verify time-sync.target is reached before scylla-server starts: Check boot logs with `journalctl -b | grep -E "chrony-wait|time-sync.target|scylla-server"`
- Confirm chrony-wait timeout setting: `systemctl show chrony-wait.service | grep TimeoutStartUSec`
- Test manual time-sync.target trigger: `systemctl start time-sync.target` and verify it waits for chrony

**Automated Testing (Unit Test Ideas):**
- **Test systemd override creation**: Verify script creates `/etc/systemd/system/scylla-server.service.d/10-time-sync.conf` with correct content
- **Test override parsing**: Parse override file and assert it contains `After=time-sync.target` and `Wants=time-sync.target`
- **Test service enablement**: Mock systemctl and verify chrony-wait.service is enabled
- **Test dependency chain**: Parse systemd unit files and verify: chrony-wait → time-sync.target → scylla-server
- **Test override permissions**: Verify override file has correct permissions (644)

**Integration Testing:**
- Boot instance and capture boot timeline: `systemd-analyze critical-chain scylla-server.service`
- Verify chrony-wait.service completes before scylla-server starts:
  ```bash
  journalctl -u chrony-wait.service --no-pager | grep "Started\|Finished"
  journalctl -u scylla-server.service --no-pager | grep "Started"
  ```
  Compare timestamps to ensure chrony-wait finishes first
- Test failure scenario: Simulate chrony failure, verify scylla-server still attempts to start (Wants vs Requires)
- Verify time-sync.target is active when scylla-server starts: `systemctl is-active time-sync.target`
- Test with delayed time sync: Artificially delay chrony sync, verify scylla-server waits appropriately
- Check boot time impact: Measure time from boot to scylla-server start with and without time-sync enforcement

**Regression Testing:**
- Verify scylla-server still starts correctly on all clouds (AWS, GCP, Azure, OCI)
- Ensure no boot hangs or timeouts due to chrony-wait
- Confirm scylla-server behavior is unchanged when time is already synced
- Test upgrade scenario: Verify override persists across scylla-server package updates

## Summary of Implementation Steps

### 1. Repository Updates

Create cloud-specific chrony configuration templates in the `scylla-machine-image` repository:

**Suggested file locations:**
- `dist/common/chrony.conf.aws`
- `dist/common/chrony.conf.gce`
- `dist/common/chrony.conf.azure`
- `dist/common/chrony.conf.oci`

Each template should include:
- Cloud-specific NTP/PTP configuration (Part 1)
- Universal chrony settings (Part 2)

### 2. Build Script Updates

Update Packer/Image builder scripts (e.g., `packer/scylla_install_image`):

**Actions:**
1. Install chrony.
2. **Clean vendor configs** (fixes issue with drift):
   ```bash
   rm -f /etc/chrony/sources.d/*
   rm -f /etc/chrony/conf.d/*
   ```
3. Copy the correct cloud-specific `chrony.conf`:
   - For Ubuntu/Debian: `/etc/chrony/chrony.conf`
   - For some distributions: `/etc/chrony.conf`
   - Check which path exists on the target distro and copy accordingly.
4. Enable chrony services:
   ```bash
   systemctl enable chrony.service
   systemctl enable chrony-wait.service
   ```
5. Create the systemd override for `scylla-server`:
   ```bash
   mkdir -p /etc/systemd/system/scylla-server.service.d/
   cat > /etc/systemd/system/scylla-server.service.d/10-time-sync.conf <<EOF
   [Unit]
   After=time-sync.target
   Wants=time-sync.target
   EOF
   systemctl daemon-reload
   ```

### 3. Validation

After deploying images built with these changes, validate on each cloud provider:

**Boot an instance and verify:**

1. **Vendor configs are empty:**
   ```bash
   ls -la /etc/chrony/sources.d/
   ls -la /etc/chrony/conf.d/
   # Should be empty (directories may still exist but contain no files)
   ```

2. **Cloud-specific chrony.conf is in place:**
   ```bash
   # Check the appropriate path for your distribution:
   # Ubuntu/Debian: /etc/chrony/chrony.conf
   # Other distributions: /etc/chrony.conf
   cat /etc/chrony/chrony.conf || cat /etc/chrony.conf
   # Verify correct server/refclock for the cloud provider
   ```

3. **Chrony is synchronized:**
   ```bash
   chronyc tracking
   chronyc sources -v
   # Should show synchronization with the expected source
   ```

4. **scylla-server started after time sync:**
   ```bash
   systemctl show scylla-server.service | grep After
   # Should include time-sync.target
   
   journalctl -u chrony-wait.service
   journalctl -u scylla-server.service
   # Verify chronological order: chrony-wait completed before scylla-server started
   ```

5. **Time sync target is reached:**
   ```bash
   systemctl status time-sync.target
   # Should be active
   ```

## Benefits

1. **Consistency**: All cloud providers use the same configuration approach, reducing maintenance burden.
2. **Performance**: Using cloud-internal time sources reduces latency and improves accuracy.
3. **Reliability**: Eliminates conflicts between vendor defaults and custom configurations.
4. **Safety**: Ensures ScyllaDB never starts with an incorrect or unsynchronized clock, preventing data consistency issues.
5. **Compliance**: Aligns with each cloud provider's best practices and official recommendations.

## References

### AWS
- [Set the time reference on your EC2 instance](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/configure-ec2-ntp.html)
- [Precision clock and time synchronization](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/set-time.html)

### GCP
- [Configure NTP on a VM](https://cloud.google.com/compute/docs/instances/configure-ntp)
- [Public NTP FAQ](https://developers.google.com/time/faq)

### Azure
- [Time sync for Linux VMs in Azure](https://learn.microsoft.com/en-us/azure/virtual-machines/linux/time-sync)

### OCI
- [Configuring the OCI NTP Service](https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/configuringntpservice.htm)
- [NTP Server Release Notes](https://docs.oracle.com/en-us/iaas/releasenotes/changes/cbc6876a-ac5f-4716-9ba2-fb3d693b5258/)

### General
- [Chrony Documentation](https://chrony.tuxfamily.org/documentation.html)
- [systemd time-sync.target](https://www.freedesktop.org/software/systemd/man/systemd.special.html#time-sync.target)

## Related Issues

This plan addresses configuration drift and ensures uniform time synchronization practices across all supported cloud platforms, improving the reliability and consistency of ScyllaDB deployments.
