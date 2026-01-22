# Health Check Optimization Plan

## Overview

This document outlines a phased approach to optimize health checks in the Scylla Machine Image. The goal is to reduce startup delays, improve verification accuracy, and streamline the sequence of dependency checks.

## Current State

The Scylla Machine Image currently performs several health and readiness checks:

1. **Pre-startup checks** (`scylla-image-setup.service`):
   - Runs before scylla-server starts
   - Configures swap, CPU scaling, network settings
   - Executes `scylla_ec2_check` for EC2 optimization verification
   - Total timeout: 900 seconds

2. **Post-startup checks** (`scylla-image-post-start.service`):
   - Runs after scylla-server starts
   - Waits 30 seconds before executing
   - Runs user-provided post-configuration scripts
   - Total timeout: 900 seconds

3. **EC2 optimization check** (`scylla_ec2_check`):
   - Verifies enhanced networking is enabled
   - Checks VPC configuration
   - Validates network driver
   - Runs on every boot

## Problems Identified

1. **Redundant checks**: EC2 optimization checks run on every boot, even though the results rarely change
2. **Fixed delays**: 30-second sleep in post-start service is inflexible
3. **No health check caching**: Results of one-time configuration checks are not cached
4. **Limited observability**: No metrics or structured logging for health check performance
5. **Sequential execution**: Some checks could be parallelized

## Optimization Phases

### Phase 1: Health Check Status Caching

**Goal**: Cache results of one-time configuration checks to avoid redundant verification on subsequent reboots.

**Changes**:
- Create a status cache file for EC2 optimization checks
- Store check results with timestamps and instance metadata
- Skip checks if cached results are valid and instance hasn't changed
- Add `--force-check` flag to bypass cache when needed
- Implement cache invalidation logic

**Benefits**:
- Faster boot times on subsequent reboots
- Reduced API calls to cloud provider metadata services
- More predictable startup behavior

**Files to modify**:
- `common/scylla_ec2_check`: Add caching logic
- Add tests for caching behavior

**Success criteria**:
- First boot: Full checks run, results cached
- Subsequent boots: Cached results used, checks skipped
- Cache invalidation works when instance changes
- `--force-check` flag bypasses cache correctly

### Phase 2: Dynamic Health Check Timeouts

**Goal**: Replace fixed sleep delays with actual health probes.

**Changes**:
- Replace 30-second sleep in `scylla-image-post-start.service` with health probe
- Implement a health check script that polls Scylla's readiness
- Add configurable timeout and retry logic
- Support both CQL and REST API health checks

**Benefits**:
- Faster post-start execution when Scylla is ready early
- More reliable post-start execution (won't run before Scylla is truly ready)
- Configurable timeouts based on instance size

### Phase 3: Parallel Health Checks

**Goal**: Execute independent health checks in parallel.

**Changes**:
- Identify independent checks in `scylla_image_setup`
- Refactor to run independent checks concurrently
- Aggregate results and fail fast on critical errors

**Benefits**:
- Reduced overall setup time
- Better resource utilization

### Phase 4: Health Check Metrics and Observability

**Goal**: Add metrics and structured logging for health checks.

**Changes**:
- Emit timing metrics for each health check phase
- Add structured logging (JSON format) for easier parsing
- Create health check status endpoint
- Add prometheus metrics export

**Benefits**:
- Better visibility into startup performance
- Easier debugging of startup issues
- Data-driven optimization opportunities

## Implementation Order

1. **Phase 1** (This Phase): Health Check Status Caching - Low risk, high impact
2. **Phase 2**: Dynamic Health Check Timeouts - Medium risk, high impact
3. **Phase 3**: Parallel Health Checks - Medium risk, medium impact
4. **Phase 4**: Health Check Metrics and Observability - Low risk, medium impact

## Rollback Plan

Each phase will:
- Be implemented behind feature flags where possible
- Include comprehensive tests
- Maintain backward compatibility
- Have clear rollback procedures documented

## Testing Strategy

- Unit tests for new caching logic
- Integration tests for health check flows
- Performance benchmarks for startup time
- Multi-boot scenarios to verify cache behavior
- Cloud provider-specific tests (AWS, GCP, Azure, OCI)
