# Independent application source

Replace this directory with the committed source of your application. Build and publish a public
OCI image in an isolated build job, then provide its immutable digest in the workspace or through
`deploy-ops run staging --component web --only --image IMAGE@sha256:DIGEST`.

The deployment worker needs no application build command. It updates the existing Container App
and waits for the requested revision to provision and become healthy. This example does not configure
private registry access, public ingress or an application HTTP probe.
