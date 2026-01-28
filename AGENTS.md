# GitHub Copilot Agent Instructions

This file contains instructions for GitHub Copilot agents working on the Scylla Machine Image project.

## Project Overview

This repository creates machine images with pre-installed Scylla for:
- AWS (AMI)
- GCE (Google Cloud)
- Azure
- OCI (Oracle Cloud Infrastructure)

Key components:
- OS packages (RPM/DEB) for first-boot configuration
- Packer scripts in `packer/`
- Cloud-specific configuration in `common/`
- Build scripts in `dist/`

## Implementation Plans

When asked to "generate an implementation plan" or "draft a plan":

**You MUST read `docs/plans/INSTRUCTIONS.md` and follow the structure defined there.**

This guideline applies ONLY to plan generation requests, not regular coding tasks.
