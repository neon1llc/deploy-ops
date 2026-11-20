# Dependencies and ownership

This repository maintains the authorization model, policies, state machine, audit ledger, client, and runner. No upstream application code is vendored.

- FastAPI handles HTTP routing, and Pydantic validates inputs.
- PyJWT and cryptography verify signatures.
- HTTPX handles bounded HTTP requests, and Uvicorn serves the controller.
- SQLite, HMAC, hashing, process control, and the command parser use Python's standard library.

Terraform, OpenTofu, their providers, GitHub Actions, and Slack stay external and aren't bundled or relicensed.

`requirements.txt` records the exact runtime versions used in verification, and a fresh install passed on macOS with Python 3.12.10. It's a tested snapshot, not a hash-verified lockfile. Before a production release, add a reviewed, hashed lock and pin a reviewed digest for the Docker base image.
