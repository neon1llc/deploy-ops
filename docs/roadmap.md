# Roadmap

The goal is configurable infrastructure and application deployment across repositories, environments, clouds, and local targets, with authorization, notifications, audit, and human or agent attribution inside the delivery lifecycle. This order avoids a pile of disconnected adapters.

| Phase | Complete outcome | Acceptance evidence |
|---|---|---|
| 1. Qualify the preview | Install, configure, deploy, promote, cancel, and recover locally on a clean machine | Linux and macOS results, Compose runtime test, browser journey, no secrets in package |
| 2. Isolate and join workers | Independent infrastructure and app CI/CD joined by durable signed receipts | Planner cannot mint writer credentials, build cannot reach deployment identity, app-only release skips infra, stale receipt fails |
| 3. Complete AWS path | Separate infra and app repos provision and roll out to a disposable ECS environment | Real OIDC, remote state, image build and digest, exact approval, healthy service, denial case, cost record, cleanup approval |
| 4. Validate Azure and GCP | Same release contract, explicit provider-specific options | Real Container Apps and Cloud Run deployments, identity denial, failed revision, artifact promotion, controlled cleanup |
| 5. Broaden tooling | Native Pulumi, CloudFormation/CDK, Bicep, and GitOps integrations | Exact change-set/program/revision binding, reconciliation evidence, documented non-equivalences |
| 6. Operate environments | Preview environments, TTL cleanup, drift, health policy, canary or blue/green | Lifecycle and failure recovery, data protections, cost ceilings outside the agent, paging acknowledgement |
| 7. Improve onboarding | Reviewed catalog, form-driven config, existing-pipeline adoption, template generation | One new repository reaches a verified deployment without hidden manual edits |
| 8. Package and integrate | Desktop wrapper only when useful, optional Deep UI, self-hosted deployment | Same owned core and contracts, no duplicated domain logic, measured operational value |

## Not finished

A deployment receipt could link infrastructure outputs, app artifact, workflow identity, policy version, provider revision, health evidence, and incident record, so a human or agent change can be explained later. Sign it and keep it independently before trusting it across workers.

Capability negotiation should expose unsupported operations before a run. An adapter might support rolling image updates but not weighted traffic or reversible database migrations. That beats a universal switch that fails late or silently changes meaning.

A preview environment should be leased, with a cost envelope and an explicit cleanup plan. Cleanup that would delete retained data must stop for separate approval.

Policy simulation could explain why a change needs approval before it reaches the execution queue. Cost simulation stays an estimate. Both should use the same policy and preview contracts as execution.

A case study should publish observed outcomes and failures, and shouldn't infer production value from passing tests, GitHub stars, or a diagram.
