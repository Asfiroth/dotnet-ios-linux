# Documentation

## Start by audience

- **Operators:** begin with the root [README](../README.md), then use the
  [installation and device troubleshooting runbook](runbooks/installation-and-device-troubleshooting.md).
- **Maintainers:** read the [project specification](specification.md), the
  [architecture decision records](decisions/README.md), and the
  [compatibility matrix](status/compatibility-matrix.md).
- **Reviewers:** use the [evidence index](evidence/receipts/index.md),
  [receipt protocol](evidence/receipt-schema.md),
  [provenance policy](governance/provenance-and-redistribution.md), and
  [`toolchain.lock.json`](../toolchain.lock.json).

## Document roles

| Document | Purpose | Does not establish |
| --- | --- | --- |
| [Specification](specification.md) | Normative architecture, scope, milestones, and release criteria | That a gate has passed |
| [Compatibility matrix](status/compatibility-matrix.md) | Concise current support boundary and publication state | Independently auditable evidence when a receipt is unpublished |
| [Decision records](decisions/README.md) | Accepted architectural choices and their consequences | Runtime or consumer support |
| [Evidence](evidence/README.md) | Receipt format, publication index, and historical context | Permission to publish private artifacts |
| [Provenance policy](governance/provenance-and-redistribution.md) | Acquisition, licensing, and sensitive-material boundaries | Redistribution approval beyond its stated classification |
| [Runbooks](runbooks/installation-and-device-troubleshooting.md) | Operator troubleshooting for supported workflows | A substitute for status gates |

The repository root also contains the supported bootstrap (`install.sh`),
engineering tooling (`eng/`), implementation source (`src/`), and validation
fixtures (`tests/`). The root README links to their primary entry points;
durable design, governance, status, and runbook material belongs here.
