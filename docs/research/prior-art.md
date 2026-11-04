# Prior art and reuse decisions

**Initial screen:** 2026-11-02. Native infrastructure sources refreshed on 2026-11-03.

## How this was screened

This is a curated set of relevant repositories, sorted by stars shown on their primary GitHub pages.
Rounded values are snapshots, not exact counts or proof of maintenance, security, or suitability.
Smaller focused projects remain useful. In particular, Towards the Cloud matches neon1's desired style
of complete, understandable cloud starters more closely than some much larger platforms.

Repository descriptions and license files support the ecosystem observations. The adoption decisions
below are DeployOps design judgements. No implementation claim follows from a project appearing here.
No source from these repositories was copied into this release. The machine-readable record is
[sources.json](sources.json).

| Repository | Observed stars | License screen | Useful capability | DeployOps decision |
|---|---:|---|---|---|
| [ansible/ansible](https://github.com/ansible/ansible) | 71.2k | GPL-3.0-or-later | Host configuration, idempotent tasks, and inventories | External executable integration, not copied core |
| [coollabsio/coolify](https://github.com/coollabsio/coolify) | 65.5k | Apache-2.0 | Self-hosted app lifecycle, environments, and service templates | Reproduce focused user paths, not a wholesale fork |
| [hashicorp/terraform](https://github.com/hashicorp/terraform) | 50.3k | BUSL-1.1 | Provider graph, saved plans, and remote state | External tool. Review version and use terms. Prefer OpenTofu for an OSS default |
| [aquasecurity/trivy](https://github.com/aquasecurity/trivy) | 38.9k | Apache-2.0 | Image, repository, IaC, and secret scanning | Optional build-stage executable. Enforcement is a later slice |
| [Dokploy/dokploy](https://github.com/Dokploy/dokploy) | 38.2k | Mixed Apache-2.0 and proprietary | Compose-oriented app operations and environment UX | Study patterns. No proprietary code copied |
| [backstage/backstage](https://github.com/backstage/backstage) | 34.8k | Apache-2.0 | Catalog, ownership, templates, and developer portal model | Keep an exportable catalog contract. Do not require a second portal now |
| [dokku/dokku](https://github.com/dokku/dokku) | 32.2k | MIT | Small self-hosted platform and plugin-oriented operations | Study narrow runtime adapters and operational simplicity |
| [opentofu/opentofu](https://github.com/opentofu/opentofu) | 30.7k | MPL-2.0 | Open infrastructure engine and state lifecycle | Use the maintained executable. Do not duplicate its provider engine |
| [pulumi/pulumi](https://github.com/pulumi/pulumi) | 25.8k | Apache-2.0 | Programmatic infrastructure and multi-language authoring | Future native adapter with actual plan and execution guarantees |
| [argoproj/argo-cd](https://github.com/argoproj/argo-cd) | 24.5k | Apache-2.0 | Desired-state application synchronization and health | Integrate revision plus reconciliation evidence, never bypass its reconciler |
| [getsops/sops](https://github.com/getsops/sops) | 23.6k | MPL-2.0 | Encrypted configuration across cloud key systems | Future explicit secret-resolution integration, never plaintext in workspace metadata |
| [caronc/apprise](https://github.com/caronc/apprise) | 17.7k | BSD-2-Clause | Many notification transports behind one interface | Prefer maintained dependency or service adapter over copying messenger clients |
| [argoproj/argo-workflows](https://github.com/argoproj/argo-workflows) | 17.1k | Apache-2.0 | Container workflow DAGs and durable orchestration | Possible execution backend after measured need, not a new mandatory cluster |
| [dagger/dagger](https://github.com/dagger/dagger) | 16.6k | Apache-2.0 | Portable container-based build pipelines | Candidate isolated build backend with the same artifact contract |
| [GoogleContainerTools/skaffold](https://github.com/GoogleContainerTools/skaffold) | 15.9k | Apache-2.0 | Repeatable Kubernetes build and development loop | Integrate the tool for suitable workloads rather than rewriting that loop |
| [caprover/caprover](https://github.com/caprover/caprover) | 15.2k | Custom modified Apache-derived terms | Simple app deployment and service catalog UX | Do not treat as plain permissive Apache. Research only |
| [open-policy-agent/opa](https://github.com/open-policy-agent/opa) | 12.4k | Apache-2.0 | Policy-as-code decisions independent of orchestration | Optional evaluator with a fail-closed decision schema, not yet integrated |
| [crossplane/crossplane](https://github.com/crossplane/crossplane) | 12.2k | Apache-2.0 | Cloud control planes and composite resource APIs | Optional reconciliation backend. Do not require Kubernetes for all users |
| [tilt-dev/tilt](https://github.com/tilt-dev/tilt) | 10.1k | Apache-2.0 | Local development feedback and microservice visibility | Study local feedback UX. Native Tilt integration is not implemented |
| [runatlantis/atlantis](https://github.com/runatlantis/atlantis) | 9.3k | Apache-2.0 | Pull-request infrastructure plans and coordination | Learn locking and review workflows. Keep exact artifact binding in owned core |
| [fluxcd/flux2](https://github.com/fluxcd/flux2) | 8.5k | Apache-2.0 | GitOps reconciliation and source-driven automation | Future desired-state receipt adapter |
| [GoogleCloudPlatform/cloud-foundation-fabric](https://github.com/GoogleCloudPlatform/cloud-foundation-fabric) | 2.1k | Apache-2.0 | Modular GCP foundations and landing-zone examples | Review coherent template sets for a future pinned catalog |
| [Azure/azure-dev](https://github.com/Azure/azure-dev) | 571 | MIT | Provision, build, deploy, and monitor workflow | Candidate Azure template and CLI backend, not another copied engine |
| [towardsthecloud/aws-cdk-starter-kit](https://github.com/towardsthecloud/aws-cdk-starter-kit) | 96 | Apache-2.0 | CDK TypeScript, projen, OIDC, and branch-based delivery | Reference for complete AWS starter ergonomics despite a smaller star count |
| [towardsthecloud/aws-toolbox](https://github.com/towardsthecloud/aws-toolbox) | 12 | MIT | Focused AWS operational commands | Prefer separate aws-clip ownership for routine operations. Avoid duplicating that project here |
| [towardsthecloud/aws-terraform-starter-kit](https://github.com/towardsthecloud/aws-terraform-starter-kit) | 8 | Apache-2.0 | Terraform, OIDC, CI/CD, and security checks | Reference for AWS starter conventions and explicit provider locks |

## What changed in the design

Coolify, Dokploy, and Dokku reinforce that users need an end-to-end app outcome, not just an approval card.
DeployOps now has a workspace, release graph, artifact preparation, local deployment, provider runtime
adapters, promotion, and a local console. It does not copy their entire hosting platform or claim their maturity.

Dagger suggests a portable build boundary. The current local builder has explicit inputs and outputs,
but it is not a sandbox. Cloud build isolation and artifact attestation are the next required increment.
Do not present a sanitized subprocess environment as Dagger-equivalent isolation.

Argo CD, Flux, Crossplane, and Argo Workflows suggest integration with existing reconcilers and schedulers.
A commit, a queued workflow, a provider operation, and a healthy application are different facts. The release
record should retain each separately. The current native adapters verify only the guarantees they actually expose.

Backstage suggests a catalog with explicit owners and templates. The shipped catalog records capabilities,
license screening, provider fit, and maturity. It does not install researched repositories, accept marketplace
terms, or pretend a GitHub URL is a ready-to-run integration.

Atlantis and Towards the Cloud reinforce protected reusable workflows, planning boundaries, and a complete
starter path. The retained split-job IaC path remains separate from unprivileged application builds.
Towards the Cloud's CDK and Terraform kits are candidate upstream template sources, not a reason to scatter
AWS-specific commands throughout the provider-neutral release core.

SOPS, Trivy, OPA, and Apprise are good examples of capabilities best supplied by maintained packages or
executables. Add a small integration contract when there is a complete use case. Do not copy their protocol
implementations merely to make every line of the repository look owned.

## Licensing decisions that affect reuse

The current Terraform repository identifies Business Source License 1.1. OpenTofu uses MPL-2.0 and is the
preferred OSS-default engine. Ansible is GPL-3.0-or-later. These terms differ from Apache-2.0 or MIT.
Invoking a tool, bundling its executable, and copying its implementation are different decisions.

Dokploy has mixed licensing. CapRover's [actual license file](https://github.com/caprover/caprover/blob/master/LICENSE)
contains additional terms. Neither should be treated as an unrestricted Apache-only code donor.
For a copied feature, inspect its exact file-level license and pin a source revision first. Preserve notices
and record modifications. These findings are screening notes, not a legal opinion.

## Native infrastructure decisions

The native adapters reuse installed provider tools for transport and provider semantics. DeployOps owns
the small remainder: frozen inputs, exact review, state checks, output selection, durable operation identity
and shared CLI/API/UI behavior. No upstream implementation source was copied for these adapters.

| Refreshed candidate | Observed stars | Inspected source and terms | Decision |
|---|---:|---|---|
| [aws/aws-cli](https://github.com/aws/aws-cli) | 17,340 | Current CloudFormation command references; external CLI v2 | Keep native change-set and stack operations behind the shared execution context |
| [Azure/azure-cli](https://github.com/Azure/azure-cli) | 4,665 | Resource deployment implementation at `azure-cli-2.76.0`, MIT | Use full what-if and incremental deployment commands rather than a second ARM engine |
| [Azure/bicep](https://github.com/Azure/bicep) | 3,660 | v0.41.2 compiler and MIT license; architecture icon terms are separate | Compile reviewed source separately and consume frozen JSON in the writer |
| [aws/aws-cdk-cli](https://github.com/aws/aws-cdk-cli) | 109 | Assembly/asset schemas at `d97e3123ebd59701c487e7aa04a83c5f24fd0426`, Apache-2.0; schema package 55.3.0 | Interpret a bounded assembly subset and share CloudFormation execution |
| [Azure/arm-deploy](https://github.com/Azure/arm-deploy) | 91 | Current repository metadata, MIT license screen | A GitHub Action is useful in existing pipelines but does not own this local shared lifecycle |

Star counts are a dated discovery snapshot, not a quality ranking. Azure CLI, Bicep and the action were
not archived when inspected. Invoking separately installed tools does not bundle or relicense their source.
The compiler trial establishes only the sample's local compilation. Native provider paths currently have
command fixtures, not live deployment qualification.

AWS's [create-change-set](https://docs.aws.amazon.com/cli/latest/reference/cloudformation/create-change-set.html),
[describe-change-set](https://docs.aws.amazon.com/cli/latest/reference/cloudformation/describe-change-set.html),
[execute-change-set](https://docs.aws.amazon.com/cli/latest/reference/cloudformation/execute-change-set.html)
and [delete-stack](https://docs.aws.amazon.com/cli/latest/reference/cloudformation/delete-stack.html) contracts
support keeping preview identity, execution and deletion distinct. The
[cloud assembly schema](https://docs.aws.amazon.com/cdk/api/v2/docs/cloud-assembly-schema-readme.html)
defines templates and assets as deployment instructions, which is why unsupported executable instructions
are refused instead of forwarded to a general `cdk deploy` command.

Azure's [deployment commands](https://learn.microsoft.com/en-us/cli/azure/deployment/group),
[what-if contract](https://learn.microsoft.com/en-us/azure/azure-resource-manager/templates/deploy-what-if)
and [Bicep build contract](https://learn.microsoft.com/en-us/azure/azure-resource-manager/bicep/bicep-cli#build)
support freezing compiler output while revalidating the prediction before submission. What-if is not
an immutable plan. Unknown analysis, secure inputs, cross-scope deployment and destruction remain
explicit limitations rather than inferred parity with Terraform or CloudFormation.

Revisit these boundaries when a concrete workload needs multi-stack dependencies, executable assets,
secure parameter references or native Azure teardown. Qualify that complete path before advertising it.

## Adoption order

First finish a safe two-repository AWS path using the existing split-job IaC contract and a native app rollout.
Qualify the implemented CloudFormation/CDK path and test the same release contract on Azure and GCP.
Add portable builds, artifact signatures, and a curated
template registry before a large UI framework, plugin marketplace, or distributed control plane.

Stars inform what deserves inspection. Complete user paths, understandable maintenance, license fit,
security boundaries, and evidence decide what belongs in the product.
