# Local Docker selection

Compose settings describe the application. The operator separately selects the native tools and Docker endpoint through `DEPLOY_OPS_DOCKER_PROFILE_FILE`, an owner-only JSON file outside the workspace and all source repositories. Configuration editing and graph preview need no Docker profile. New Compose release creation checks the selected tools, namespace, and socket before capturing source or executing a dependency.

Use `deploy-ops --project deploy-ops.toml doctor` or **Inspect Compose prerequisites** under **Local build and deployment settings**. Both inspect metadata only. "Metadata ready" doesn't establish daemon identity, health, image availability, or container readiness. Inspection never starts Docker or changes a Docker context, and the browser can't write an operator profile.

## Explicit profile

The closed format has these fields. Replace the illustrative values with an independently selected, owned local endpoint and installed native tools:

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

- The tools must be executable, trusted-owned, and outside contributor source. Choose the Compose executable explicitly, including a vendor's plugin executable when it supports standalone invocation.
- No personal Docker configuration, credential helper, ambient endpoint, plugin discovery, or global context is imported.
- The Unix socket must use its canonical path, have the declared current-user or root owner, and already exist.
- Projects are exact environment and component mappings, each with a distinct literal namespace matching the component option or its `environment-component` default. Select a namespace you own. A profile doesn't discover ownership across unrelated deployments or ledgers.

Set the profile's absolute path in `DEPLOY_OPS_DOCKER_PROFILE_FILE`, then restart the console or run the CLI with it. An unknown field, stale workspace digest, contributor-controlled reference, missing tool or socket, or mismatched project refuses admission. Docker must already be running. DeployOps doesn't start a daemon.

## Review and execution

Native commands use the private worker HOME and Docker configuration directory. Docker's `info` must return the selected daemon ID before Compose preparation and again before `up`. Compose renders through the explicit executable with an explicit project. Pinned-image, host-access, volume, and manual-approval restrictions still apply. Registry credentials for image builds stay separate and auths-only, and this profile has no registry authentication.

Fresh Compose evidence uses adapter contract 4. It freezes profile bytes, tool bytes and ownership, socket identity and owner, daemon ID, and project. Review shows the target and tool hashes. The claim check revalidates the selection before consuming approval, and the worker checks again before native use. A changed tool, profile, or endpoint needs fresh evidence, and replacing a socket, even at the same path, invalidates the binding.

Older Compose records stay readable with their original bytes and digests but can't execute under the new contract. Preserve them and prepare a fresh release. No approval is reminted or replayed. These checks coordinate the selected profile and controller, and aren't a filesystem or daemon lock against unrelated applications or malicious changes by the same host user. Daemon identity checks don't prove container health or roll back remote effects after interruption.

See Docker's [Compose configuration command](https://docs.docker.com/reference/cli/docker/compose/config/) and [Docker CLI environment and configuration](https://docs.docker.com/reference/cli/docker/).
