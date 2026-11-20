# Catalogs and marketplaces

A deployment template catalog and a commercial software marketplace are different. A catalog can supply reviewed building blocks. A marketplace may require buying a product, accepting terms, and granting new permissions. Selecting a template must never authorize those separate actions.

| System | Useful lesson |
|---|---|
| [AWS Service Catalog](https://docs.aws.amazon.com/servicecatalog/latest/adminguide/external-engine.html) | Approved internal products and external-engine lifecycle integration |
| [Azure Deployment Environments](https://learn.microsoft.com/en-us/azure/deployment-environments/configure-environment-definition) | Versioned catalog definitions and runner settings |
| [GCP Infrastructure Manager](https://docs.cloud.google.com/infrastructure-manager/docs/overview) | Managed Terraform infrastructure execution, not application delivery |
| [GCP Terraform blueprints](https://docs.cloud.google.com/docs/terraform/blueprints/terraform-blueprints) | Reusable infrastructure blueprint sources |
| [AWS Marketplace](https://docs.aws.amazon.com/marketplace/latest/buyerguide/what-is-marketplace.html) | Product discovery and purchase, with separate license and billing obligations |
| [Microsoft Marketplace](https://learn.microsoft.com/en-us/marketplace/purchase-software-appsource) | Product acquisition and activation, not blanket code-reuse permission |
| [Google Cloud Marketplace](https://docs.cloud.google.com/marketplace/docs/get-products) | Product-specific procurement, deployment, and billing |

## Proposed catalog contract

Each deployable entry should record:

- Source URL and immutable revision, license, and notices.
- Provider and tool versions, required identity scopes, input schema, and expected outputs.
- Costs, subscription prerequisites, preview guarantees, health checks, and cleanup behavior.

A template moves from research to runnable only after an integration test establishes its complete path.

Represent an existing entitlement as an operator-supplied reference. Never scrape marketplace login sessions or accept terms automatically. Subscription, role creation, payment, quota changes, and production access stay explicit human decisions. Even a free listing can deploy billable resources.

## Current implementation

`deploy-ops catalog` lists curated metadata and native adapter maturity. It fetches no arbitrary code and provisions no catalog products. The local starter is the one executable generated template. Provider catalog backends and signed template distribution are later work.
