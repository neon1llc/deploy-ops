# Dependencies and ownership

This repository maintains the authorization model, policies, state machine, audit ledger, client, and runner. No upstream application code is vendored.

- FastAPI handles HTTP routing, and Pydantic validates inputs.
- PyJWT and cryptography verify signatures.
- HTTPX handles bounded HTTP requests, and Uvicorn serves the controller.
- SQLite, HMAC, hashing, process control, and the command parser use Python's standard library.

Terraform, OpenTofu, their providers, GitHub Actions, and Slack stay external. The project doesn't relicense them, and the source archive doesn't bundle them. Installed packages keep their own licenses.

`requirements.txt` records the exact runtime versions used in verification. A fresh install passed on macOS with Python 3.12.10. It's a tested snapshot, not a hash-verified cross-platform lockfile. Validate dependency resolution on your runner and add a reviewed, hashed lock before a production release.

The Docker base image uses a maintained Python minor-version tag. Pin a reviewed image digest for a release and keep a patch-update process. Pinning alone doesn't make dependencies safe.
