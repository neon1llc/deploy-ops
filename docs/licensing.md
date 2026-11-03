# Licensing and credit

DeployOps uses the standard Apache License, Version 2.0, with a `NOTICE` naming neon1 LLC.
This keeps free use, modification, and redistribution practical while retaining normal attribution
obligations for distributions. It also supplies a patent license under the license's conditions.

Apache-2.0 requires preserving applicable notices and, when a NOTICE file is included, carrying its
relevant attribution notices in a permitted readable form. It does not require a public "powered by"
badge in every UI or compel every private user or hosted service to advertise neon1. No custom credit
clause has been added. See the [license text](https://www.apache.org/licenses/LICENSE-2.0) and
[Apache's application guidance](https://www.apache.org/legal/apply-license.html).

That is the chosen balance for neon1's free-sharing goal. Use "DeployOps by neon1" in the project's
README, About screen, documentation, and package metadata. Downstream users may remove product branding
where the license allows. Trademark and copyright are different matters. This is a project licensing choice,
not a trademark clearance or a promise of legal protection.

The earlier DeployGuard MIT notice is preserved in [licenses/DeployGuard-MIT.txt](../licenses/DeployGuard-MIT.txt).
This archive does not revoke previously granted MIT rights to that earlier code.

## Upstream material

No source code from the researched deployment platforms was copied into this release. Their ideas informed
scope and design. Python libraries are dependencies with their own licenses, not renamed neon1 code.
The bundled appearance library in `src/deploy_ops/ui/theme.mjs` retains its upstream MIT license in
[licenses/theme-MIT.txt](../licenses/theme-MIT.txt). It supplies the semantic tokens, presets and preference
validation. The console owns its browser integration and uses separate local appearance storage.
A future copied feature set must record its exact source revision, original file paths, license, required
notices, and local changes. Do not label an entire mixed-license repository permissive because its root is Apache-2.0.

OpenTofu and SOPS use MPL-2.0. Invoking an external executable differs from copying and modifying its files.
Terraform's current licensing must be checked for the selected version and use case. Dokploy includes
proprietary portions. Neither "free to download" nor a high star count grants unrestricted code reuse.

Review [the research ledger](research/prior-art.md) before adopting or copying code. Preserve the upstream
license and notices when required. Optional thanks are welcome but do not create new downstream obligations.

Guided configuration uses [TOMLKit](https://github.com/python-poetry/tomlkit/tree/a0b15ebb09167ede584fc36ad5362fc3039b0530)
0.15.2 as a normal MIT-licensed dependency. It preserves TOML structure and comments while the existing
workspace model validates the edited contract. No TOMLKit implementation is copied into DeployOps; its
package retains its own license. Review upgrades and rerun draft-preservation tests before changing the pin.
