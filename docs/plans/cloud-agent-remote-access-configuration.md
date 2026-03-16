# Cloud Provider Agent Remote Access Configuration

**Status:** In Progress  
**Owner:** Platform Team  
**Target Release:** TBD  
**Reference:** SMI-162

## 1. Problem Statement

Cloud provider agents (AWS SSM Agent, GCE Guest Agent, Azure WALinuxAgent, OCI Cloud Agent) currently provide remote access capabilities that bypass standard SSH authentication mechanisms. This creates potential security concerns as these agents can:

- Enable remote shell access through cloud provider management consoles
- Provision SSH keys automatically from instance metadata
- Execute arbitrary commands via cloud-specific APIs (Session Manager, Run Command, etc.)
- Deploy and run extensions that may enable unauthorized access

The current approach of completely masking/disabling these agents has limitations:
- **Incomplete coverage:** Some agents (e.g., GCE guest-agent) are not explicitly managed
- **Loss of functionality:** Masking entire agents prevents beneficial features like monitoring, logging, and legitimate platform integration
- **Operational issues:** Complete agent disablement may cause compatibility problems with cloud platform services

## 2. Current State

**File:** `packer/scylla_install_image` (lines 98-136)

### AWS (Lines 98-119)
- **Agent:** amazon-ssm-agent
- **Current behavior:** Agent is installed, enabled, then immediately masked via `systemctl mask`
- **Issue:** Masking is done correctly but lacks documentation explaining the security rationale

### GCE (Lines 120-125)
- **Agents:** google-osconfig-agent (managed), google-guest-agent (unmanaged)
- **Current behavior:** Only osconfig-agent is masked; rsyslog is purged
- **Issue:** The main guest-agent (responsible for SSH key provisioning from metadata) is NOT explicitly configured or disabled, leaving potential remote access vector open

### Azure (Lines 126-129)
- **Agent:** walinuxagent
- **Current behavior:** Agent is completely masked via `systemctl mask`
- **Issue:** Masking prevents ALL Azure platform integration, which may cause VM lifecycle issues, diagnostics failures, and extension management problems

### OCI (Lines 130-135)
- **Agents:** oracle-cloud-agent, oracle-cloud-agent-updater
- **Current behavior:** Both services are masked with fallback (`|| true`)
- **Issue:** Blanket masking prevents useful monitoring and telemetry plugins while attempting to block remote access plugins

**Configuration files:** Currently, no agent configuration files exist in `packer/files/` directory (only contains `.gitkeep`)

## 3. Goals

1. **Security:** Disable all remote access features that bypass SSH authentication across all cloud providers
2. **Functionality:** Preserve beneficial agent features (monitoring, logging, platform integration)
3. **Consistency:** Apply uniform security principles across all cloud platforms (AWS, GCE, Azure, OCI)
4. **Maintainability:** Use configuration-based approach rather than complete agent disablement
5. **Documentation:** Clearly document security decisions and trade-offs for each cloud provider

## 4. Implementation Phases

### Phase 1: AWS SSM Agent Documentation
**Scope:** Single PR - Documentation only

- Add inline comments explaining why SSM agent is masked
- Document security rationale in code
- No functional changes (keep current masking approach)

**DoD:**
- Comments added to scylla_install_image explaining SSM agent security model
- Rationale documented for why masking is preferred over configuration

**Dependencies:** None

**Deliverables:**
- Updated `packer/scylla_install_image` with documentation

### Phase 2: GCE Guest Agent Configuration
**Scope:** Single PR - Configuration file and deployment

- Create guest agent configuration file to disable accounts daemon
- Deploy configuration during GCE image build
- Keep osconfig-agent masking unchanged

**DoD:**
- Configuration file created in `packer/files/gce/`
- Configuration deployed to `/etc/default/instance_configs.cfg` during build
- SSH key auto-provisioning from metadata is disabled
- Manual SSH key management still works

**Dependencies:** None

**Deliverables:**
- `packer/files/gce/instance_configs.cfg`
- Updated `packer/scylla_install_image` to deploy GCE configuration

### Phase 3: Azure WALinuxAgent Configuration
**Scope:** Single PR - Replace masking with configuration

- Create waagent configuration to disable provisioning and extensions
- Replace systemctl mask command with configuration deployment
- Ensure basic Azure platform integration remains functional

**DoD:**
- Configuration file created with targeted settings
- Agent runs but cannot provision users or execute extensions
- Azure VM lifecycle operations work correctly
- Instance metadata access remains available

**Dependencies:** Requires testing on Azure platform to verify VM functionality

**Deliverables:**
- `packer/files/azure/waagent.conf` (partial override)
- Updated `packer/scylla_install_image` to deploy configuration and remove masking

### Phase 4: OCI Cloud Agent Plugin Configuration
**Scope:** Single PR - Replace masking with plugin-level controls

- Configure specific plugins to disable (Bastion, Run Command)
- Allow monitoring and logging plugins to function
- Replace or adjust masking commands

**DoD:**
- Bastion plugin disabled
- Compute Instance Run Command plugin disabled
- Monitoring/telemetry plugins remain enabled
- Agent runs without requiring masking

**Dependencies:** Requires OCI platform testing

**Deliverables:**
- Plugin configuration mechanism (config file or systemctl approach)
- Updated `packer/scylla_install_image` with OCI plugin controls

**Open Question:** Determine best method for OCI plugin configuration (config file vs runtime commands)

## 5. Testing Requirements

### Per-Phase Testing

**Phase 1 (AWS):**
- Review: Code review only (documentation changes)

**Phase 2 (GCE):**
- Unit: Verify configuration file syntax
- Integration: Build test GCE image
- Manual: Boot instance, verify SSH keys from metadata are NOT provisioned
- Manual: Verify manual SSH key configuration still works

**Phase 3 (Azure):**
- Unit: Verify waagent.conf syntax
- Integration: Build test Azure image
- Manual: Boot instance, verify agent status shows running
- Manual: Attempt to deploy extension (should fail)
- Manual: Verify Azure diagnostics and platform operations work
- Manual: Test VM start/stop lifecycle

**Phase 4 (OCI):**
- Unit: Verify plugin configuration
- Integration: Build test OCI image
- Manual: Verify Bastion service cannot connect
- Manual: Verify Run Command is unavailable
- Manual: Confirm monitoring data still flows to OCI console

### Cross-Platform Testing
- Verify all images build successfully
- Confirm cloud-init user provisioning works correctly
- Test scyllaadm user access on all platforms
- Validate Scylla cluster formation across providers

## 6. Success Criteria

1. **Remote access disabled:** Cloud provider remote access features (Session Manager, SSH key injection, Bastion, Run Command) are non-functional
2. **Essential features work:** Monitoring, logging, metadata access, and platform integration remain operational
3. **Image stability:** All cloud images build successfully and instances boot correctly
4. **User access intact:** Customer-managed SSH keys and scyllaadm user access work as expected
5. **Documentation complete:** Code includes clear explanations of security decisions
6. **No regressions:** Existing Scylla functionality (cluster formation, configuration, etc.) is unaffected

## 7. Risk Mitigation

### Potential Blockers

**Azure Integration Issues:**
- **Risk:** Disabling provisioning may break Azure VM lifecycle or diagnostics
- **Mitigation:** Incremental testing on Azure platform; keep fallback option to revert to masking if needed
- **Rollback:** Can quickly revert to systemctl mask approach if configuration causes issues

**OCI Plugin Dependencies:**
- **Risk:** Disabling specific plugins may have undocumented dependencies on other plugins
- **Mitigation:** Test thoroughly on OCI; document plugin interdependencies
- **Rollback:** Maintain masking approach as fallback

**GCE Compatibility:**
- **Risk:** Guest agent configuration format may change between versions
- **Mitigation:** Test on current Ubuntu 24.04 base image; monitor Google Cloud agent updates
- **Rollback:** Can remove configuration file and rely on osconfig-agent masking only

### Dependencies on External Systems
- Requires access to test environments for each cloud provider
- May need coordination with cloud provider support for undocumented behavior
- Google, Microsoft, and Oracle documentation may be incomplete or outdated

### Compatibility Concerns
- Ubuntu version compatibility (currently targeting 24.04)
- Cloud-init interaction with agent configurations
- Potential conflicts between cloud-init and agent user provisioning
- Future agent version updates may change configuration options

### Testing Limitations
- Cannot fully test all Azure VM sizes and configurations
- OCI plugin behavior may vary by region or tenancy settings
- Limited ability to test all cloud-provider-initiated remote access scenarios

