# API

The service expects HTTPS outside loopback. Requests use JSON except for Slack's signed form body. The default body limit is 2 MB. Validation errors do not echo submitted plan data.

| Method and path | Authentication | Purpose |
| --- | --- | --- |
| `GET /healthz` | None | Basic process health |
| `GET /v1/targets/{target}/plan-context` | GitHub OIDC, planning identity | Read registered planning settings |
| `GET /v1/targets/{target}/receipts/{id}` | Registered consumer GitHub identity | Resolve an authorized, current infrastructure receipt |
| `POST /v1/admin/receipts/{id}/revoke` | Operator token | Revoke infrastructure evidence |
| `POST /v1/requests` | GitHub OIDC, planning identity | Normalize and record a proposal |
| `GET /v1/requests/{id}` | Registered GitHub identity or operator token | Read a sanitized request |
| `POST /v1/requests/{id}/claim` | GitHub OIDC, matching executor identity | Claim an approved artifact once |
| `POST /v1/executions/{id}/heartbeat` | One-request execution token | Recheck current authority |
| `POST /v1/executions/{id}/complete` | One-request execution token | Report the runner's outcome |
| `GET /v1/admin/requests` | Operator token | List the latest 100 requests |
| `POST /v1/admin/requests/{id}/decision` | Operator token | Approve or reject an exact digest |
| `POST /v1/admin/requests/{id}/resolve` | Operator token | Reconcile an uncertain execution |
| `GET, POST /v1/admin/delegations` | Operator token | List or create bounded windows |
| `POST /v1/admin/delegations/{id}/revoke` | Operator token | Revoke a window |
| `POST /v1/admin/pause` | Operator token | Stop new authorization and invalidate active authority |
| `POST /v1/admin/resume` | Operator token | Resume without restoring old grants |
| `GET /v1/admin/audit` | Operator token | Export the ledger |
| `GET /v1/admin/audit/verify` | Operator token | Verify links and return the head hash |
| `POST /v1/slack` | Slack HMAC and configured member | Handle approval buttons and commands |

Bearer tokens use `Authorization: Bearer TOKEN`. GitHub tokens need the configured `deploy_ops` audience. An execution token cannot authorize operator endpoints. The operator token is not accepted as a workflow identity for submitting or claiming a deployment.

The authoritative request shapes are in [models.py](../src/deploy_ops/models.py). Extra fields are rejected. Costs use whole USD cents per month and may be unknown. Unknown estimates cannot qualify for automatic approval unless the target operator explicitly changes that rule.

A denied or rejected request cannot be turned into an approved request by editing its record. Submit a new plan. A policy change also requires a new request. A claim failure never means that a caller should apply anyway.

The API has no shell endpoint, credential export endpoint, public registration route, or agent-accessible policy editor. The generic manifest contract does not add one.

An operator can reject a pending or approved request before execution is claimed, including after its
approval expires. Rejection and claiming use the same transaction boundary. Once execution starts,
inspect and reconcile its outcome instead of treating rejection as a rollback.


## Local incident operations

The authenticated loopback console exposes `GET/POST /api/incidents`, `POST /api/incidents/tick`,
`POST /api/incidents/{id}/acknowledge`, `POST /api/incidents/{id}/resolve` and `GET /api/notifications`.
Opening accepts `release_id`, `owner`, `note`, `interval_seconds` and `limit`. Decisions require the
current `version` and a `note`. Read-only outbox inspection sends nothing. All endpoints use the existing
console token and origin/host checks; none grants remote paging or deployment authority.
See [local incidents](incidents.md) for the lifecycle and receiver ordering contract.
