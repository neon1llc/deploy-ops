# Dependencies and ownership

The authorization model, policies, state machine, audit ledger, client, and runner are maintained in this repository. No upstream application code has been vendored into this release.

FastAPI handles HTTP routing. Pydantic validates inputs. PyJWT and cryptography verify signatures. HTTPX handles bounded HTTP requests. Uvicorn serves the controller. SQLite, HMAC, hashing, process control, and the command parser use Python's standard library.

Terraform, OpenTofu, their providers, GitHub Actions, and Slack remain external tools or services. The project does not relicense them. Installed Python packages and action distributions retain their own licenses and notices. The source archive does not bundle those distributions.

`requirements.txt` records the exact runtime versions used in verification. A fresh network installation passed on macOS with Python 3.12.10. This is a tested version snapshot, not a hash-verified cross-platform lockfile. Validate dependency resolution on the target runner and add a reviewed, hashed lock before a production release.

The Docker base image uses a maintained Python minor-version tag. Pin a reviewed image digest for a release and keep a patch-update process. The project does not claim that pinning dependencies alone makes them safe.
