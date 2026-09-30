# Apiary Django

Multi-project Django workspace. Uses PostgreSQL schemas plus a separate BOM
SQLite database; uploaded media remains in the existing S3-compatible bucket.

Development: `uv sync`, then `docker --context rootless compose up`.
The Compose stack loads development fixtures; do not use it for data migration.

The local `deploy/forgejo-k0s` branch prepares Forgejo CI → Zot → Argo CD
(`k8s/`), with Gunicorn, WhiteNoise, and a migration hook for all five database
aliases. CI runs `docker build --target test .`; project fixtures and users are
never loaded by the deployment hook.

Infrastructure onboarding remains parked in
`rrchnm-systems/infra:k0s/gitops/apps/parked/apiary-django.yaml`.
Confirm the planned `rrchnm/apiary-django` repository and default branch before
publishing. Supply `ZOT_TOKEN` to CI and seed `kv/eso/apiary-django` with
`DJANGO_SECRET_KEY`, `DB_PASS`, existing `OBJ_STORAGE_*` values, and enabled
`ALLAUTH_*` credentials. Adjust the S3 egress hostname if the endpoint changes.

Do not activate Argo or change DNS until PostgreSQL, BOM SQLite, and object
storage backups have passed an isolated restore rehearsal. Preserve the old
deployment for rollback. Deployment reference:
https://docs.rrchnm.org/subsystems/reverse-proxy/k0s-public-edge/
