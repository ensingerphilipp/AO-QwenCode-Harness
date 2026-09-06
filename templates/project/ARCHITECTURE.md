# {{PROJECT_NAME}} Architecture

## System overview

{{SYSTEM_OVERVIEW}}

## Component boundaries

{{COMPONENT_BOUNDARIES}}

## Primary flows

{{PRIMARY_FLOWS}}

## State and persistence

{{ARCHITECTURE_STATE_AND_PERSISTENCE}}

## External systems

{{EXTERNAL_SYSTEMS}}

## Runtime dependencies

{{RUNTIME_DEPENDENCIES}}

## Build and release

{{BUILD_AND_RELEASE}}

## CI and verification

The authoritative deterministic verification entry point is `bash scripts/verify`. CI must invoke the same entry point; this document records rationale and constraints, not a duplicate command list.

{{CI_VERIFICATION_NOTES}}

## Approved technical decisions

{{APPROVED_TECHNICAL_DECISIONS}}

## Known risks and constraints

{{KNOWN_ARCHITECTURE_RISKS}}
