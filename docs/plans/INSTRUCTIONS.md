# Implementation Plans - Guidelines

Implementation plans document major features and refactorings for the Scylla Machine Image project.

## Purpose

- Document complex features before implementation
- Provide roadmap for incremental development
- Enable better code reviews
- Track progress across multiple PRs
- Identify dependencies and risks early

## Plan Structure

All implementation plans must follow this 7-section structure:

### 1. Problem Statement
- What needs to be implemented and why
- Business or technical need
- Pain points with current situation

### 2. Current State
- **Must reference specific files, classes, and methods**
- **Agent Note:** Use file listing/reading tools to inspect actual files before writing this section
- How things currently work
- What needs to change
- **Mark unknown information as "Needs Investigation"**

### 3. Goals
- Specific, measurable objectives
- Concrete outcomes
- Success metrics

### 4. Implementation Phases
- **Atomic phases scoped to single PRs where possible**
- **Sequential ordering:** Foundational refactoring before features
- Each phase must have:
  - Clear description
  - Definition of Done (DoD) criteria
  - Dependencies
  - Expected deliverables
- **Mark unclear requirements as "Open Question"**

### 5. Testing Requirements
- Unit, integration, and manual testing per phase
- Test coverage goals
- Performance testing (if applicable)

### 6. Success Criteria
- Measurable outcomes
- Acceptance criteria
- Validation steps

### 7. Risk Mitigation
- Potential blockers
- Rollback strategies
- Dependencies on external systems
- Compatibility concerns

## Guidelines

### File Management
- Store plans in `/docs/plans/` directory
- Use descriptive filenames (e.g., `cloud-init-enhancement.md`)
- Archive completed plans to `/docs/plans/archive/`

### Content Best Practices
- Include clear DoD for each phase
- Provide comprehensive testing requirements
- Document backend-specific details (AWS, GCE, Azure, OCI)
- Use bullet points for clarity
- Link to relevant issues, PRs, or documentation
- Keep as living documents - update during implementation
- Mark completed phases with checkboxes

## Rules for Agents

**Role:** Act as a **Senior System Architect** for this project.

**When asked to "generate an implementation plan" or "draft a plan":**
1. **Context Verification:** Use file access tools to read relevant code first
2. Read this file (`docs/plans/INSTRUCTIONS.md`) completely
3. Follow the 7-section structure exactly
4. Apply all guidelines
5. **No Filler:** Start response immediately with `# Plan Title`
6. Do NOT apply this format to regular coding questions or small changes
