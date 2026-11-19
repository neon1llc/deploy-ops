# Local Docker selection

Compose settings describe the application. The operator separately selects the native tools and Docker
endpoint through `DEPLOY_OPS_DOCKER_PROFILE_FILE`. The profile is an owner-only JSON file outside the
workspace and all source repositories. Configuration editing and graph preview need no Docker profile.
New Compose release creation checks the selected tools, namespace and socket before capturing source
or executing a dependency.

Use `deploy-ops --project deploy-ops.toml doctor` or **Inspect Compose prerequisites** under **Local build
and deployment settings**. Both inspect metadata only. “Metadata ready” does not establish daemon
identity, health, image availability or container readiness. Inspection never starts Docker or changes a
Docker context. The browser cannot write an operator profile.

## Explicit profile

The closed format contains these fields; replace the illustrative values with an independently selected,
owned local endpoint and installed native tools:

```json
{
  "schema_version": 1,
  "workspace_digest": "<digest from config inspect>",
  "docker_executable": "/absolute/path/to/docker",
  "compose_executable": "/absolute/path/to/docker-compose",
  "endpoint": "unix:///absolute/path/to/docker.sock",
  "endpoint_owner_uid": 501,
  "daemon_id": "<independently observed Docker daemon ID>",
  "projects": {"development/web": "development-web"}
}
```

The tools must be executable, have trusted ownership and stay outside contributor source. Choose the
installed Compose executable explicitly, including a vendor's plugin executable when it supports native
standalone invocation. No personal Docker configuration, credential helper, ambient Docker endpoint,
automatic plugin discovery or global context is imported. The selected Unix socket must use its canonical
path, have the declared current-user or root owner, and already exist. Projects are exact
environment/component mappings, each with a distinct literal namespace matching the resolved component
option or its `environment-component` default. The operator must select a namespace it owns; a profile
does not discover ownership across unrelated deployments or ledgers.

Protect the profile with owner-only permissions, select its absolute path in
`DEPLOY_OPS_DOCKER_PROFILE_FILE`, then restart the console or invoke the CLI with that reference. An
unknown field, stale workspace digest, contributor-controlled reference, missing tool/socket or mismatched
project refuses admission. Docker must already be available; DeployOps does not activate a daemon.

## Review and execution

Native commands use the existing private worker HOME and Docker configuration directory. Docker's native
`info` must return the exact selected daemon ID before Compose preparation and again before `up`. Compose
renders through the explicit executable with an explicit project. Existing pinned-image, host-access,
volume and manual-approval restrictions remain. Registry credentials used for image builds stay separate
and auths-only; this profile contains no registry authentication.

Fresh Compose evidence uses adapter contract 4. It freezes profile bytes, tool bytes and ownership, socket
identity/owner, daemon ID and project. Review displays the selected target and tool hashes. The existing
claim check revalidates the selection before consuming approval, and the worker checks it again before
native use. A changed tool, profile or endpoint needs fresh evidence. Replacing a socket, even with the
same path, invalidates the prior binding.

Older Compose records remain readable with their original bytes and digests. They cannot execute under
the new binding contract; preserve them and prepare a fresh release. No approval is reminted or replayed.
These checks coordinate the selected operator profile and existing controller; they are not a filesystem
or daemon lock against unrelated applications or malicious changes by the same host user. Native daemon
identity checks do not prove container health or rollback remote effects after interruption.

See Docker's [Compose configuration command](https://docs.docker.com/reference/cli/docker/compose/config/)
and [Docker CLI environment/configuration](https://docs.docker.com/reference/cli/docker/).
