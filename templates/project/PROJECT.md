# {{PROJECT_NAME}}

## Purpose

{{PROJECT_PURPOSE}}

## Goals

{{PROJECT_GOALS}}

## Non-goals

{{PROJECT_NON_GOALS}}

## Autonomous change scope

{{AUTONOMOUS_CHANGE_SCOPE}}

## Human-in-the-loop boundaries

{{HITL_BOUNDARIES}}

Changes outside the autonomous scope, or changes that cross a boundary above, require explicit human approval before implementation.

## Public contracts

{{PUBLIC_CONTRACTS}}

## Security boundaries

{{SECURITY_BOUNDARIES}}

## Persistence and state

{{PERSISTENCE_STATE}}

## Deployment and support policy

{{DEPLOYMENT_SUPPORT_POLICY}}

## Acceptance contract

The repository's deterministic verification entry point is `bash scripts/verify`. Exact mechanical checks are defined by that executable script and must also be used by CI.

{{PROJECT_ACCEPTANCE_NOTES}}
