# Roadmap

Goal: configurable infrastructure and application deployment across repositories, environments, clouds, and local targets, with authorization, notifications, and audit inside the delivery lifecycle.

| Phase | Complete outcome | Acceptance evidence |
|---|---|---|
| 1. Qualify the preview | Install, configure, deploy, promote, cancel, and recover locally on a clean machine | Linux and macOS results, Compose test, browser journey, no secrets in package |
| 2. Isolate and join workers | Independent infrastructure and app CI/CD joined by durable signed receipts | Planner can't mint writer credentials, build can't reach deployment identity, app-only release skips infra, stale receipt fails |
| 3. Complete AWS path | Separate infra and app repos provision and roll out to a disposable ECS environment | Real OIDC, remote state, image digest, exact approval, healthy service, denial case, cost record, cleanup approval |
| 4. Validate Azure and GCP | Same release contract, explicit provider-specific options | Real Container Apps and Cloud Run deployments, identity denial, failed revision, artifact promotion, controlled cleanup |
| 5. Broaden tooling | Native Pulumi, CloudFormation/CDK, Bicep, and GitOps integrations | Exact change-set/program/revision binding, reconciliation evidence, documented non-equivalences |
| 6. Operate environments | Preview environments, TTL cleanup, drift, health policy, canary or blue/green | Lifecycle and recovery, data protections, cost ceilings outside the agent, paging acknowledgement |
| 7. Improve onboarding | Reviewed catalog, form-driven config, existing-pipeline adoption, template generation | One new repository reaches a verified deployment |
| 8. Package and integrate | Desktop wrapper only when useful, optional Deep UI, self-hosted deployment | Same core and contracts, no duplicated domain logic |

## Not finished

- A signed deployment receipt linking infrastructure outputs, app artifact, workflow identity, policy version, provider revision, health evidence, and incident record. Keep it independently before trusting it across workers.
- Capability negotiation that shows unsupported operations before a run, such as weighted traffic or reversible database migrations.
- Leased preview environments with a cost envelope. Cleanup that would delete retained data must stop for separate approval.
- Policy and cost simulation before a change reaches the execution queue.
- A recorded cloud case study: deploy infrastructure and publish a receipt, release the app from it, release again without an infrastructure apply, and promote the same image to staging, with a manual approval, a bounded grant, a denied authority change, an expired lease, an interrupted operation blocked until reconciliation, and a rejected role assumption from an unregistered workflow. It needs disposable targets and explicit spending authority, and publishes no account identifiers, raw plans, tokens, or invented numbers. Repeat on Azure and GCP before claiming multi-cloud parity.
