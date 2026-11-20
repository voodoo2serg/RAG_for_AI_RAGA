# Security policy

This is an initial single-user local-only community release.

- Bind the server to 127.0.0.1. Do not publish the API directly to the internet.
- Use RAGA_API_KEY if placing RAGA behind a private gateway. This is a shared secret, not full user authorization.
- Keep Docker volumes and exported conversations private.
- Do not commit conversation exports, credentials or production configuration.
- Do not report live secrets in public issues.

To report a vulnerability, contact the maintainer through their GitHub profile using a private channel. If a private reporting channel is unavailable, open an issue asking how to submit a report without disclosing details.
