Place the OpenSearch HTTP root CA for the prod-like compose stack here as `root-ca.pem`.

Phase 11 keeps the base `docker-compose.yml` security-aware:

- OpenSearch remains on HTTPS with the security plugin enabled.
- The orchestrator validates `OPENSEARCH_CA_BUNDLE_PATH` at startup.
- The default container mount expects `/run/opensearch-certs/root-ca.pem`.

The dev override disables the OpenSearch security plugin and does not use this CA bundle.
