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
refclock PHC /dev/ptp_hyperv poll 3 dpoll -2 offset 0
```

**Fallback:**

```conf
# Azure internal NTP fallback
server 169.254.169.254 iburst
```

**Notes:**
- Azure exposes a PTP hardware clock as `/dev/ptp_hyperv` (or `/dev/ptp0` on some systems) based on IEEE 1588 PTP standard.
- Provides higher accuracy than traditional NTP, synchronized to Microsoft's Stratum 1 time sources backed by GPS antennas.
- Recent Azure Linux images use `chronyd` with `/dev/ptp_hyperv` by default.
- **Udev Rule**: Ensure `/dev/ptp_hyperv` exists and is accessible at startup. May require systemd ordering adjustments to ensure the device is available before `chronyd` starts.

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
   # Should be empty or non-existent
   ```

2. **Cloud-specific chrony.conf is in place:**
   ```bash
   cat /etc/chrony/chrony.conf
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
