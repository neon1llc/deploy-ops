# Application input

This example selects an immutable public Python image. It does not build or publish an application image.
The Deployment's existing command serves the example page. DeployOps changes only the named container's
image after approval, waits for the rollout, and rechecks the declared image.
