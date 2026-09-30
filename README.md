# Apiary Django

Multi-project Django workspace. Uses PostgreSQL schemas plus a separate BOM
SQLite database; uploaded media remains in the existing S3-compatible bucket.

Development: `uv sync`, then `docker --context rootless compose up`.
The Compose stack loads development fixtures; do not use it for data migration.

Source remains at `github.com/chnm/apiary-django`. The `deploy/forgejo-k0s`
branch prepares GitHub Actions → Zot → Argo CD (`k8s/`), with Gunicorn,
WhiteNoise, and a migration hook for all five database aliases. The pinned
`chnm/.github` reusable checks the test stage and builds the runtime image on
GitHub-hosted runners. Only trusted `main` builds publish to Zot, on
`[self-hosted, IncusOS]`; PRs never use internal runners. The deployment hook
never loads project fixtures or overwrites users.

Infrastructure onboarding remains parked in
`rrchnm-systems/infra:k0s/gitops/apps/parked/apiary-django.yaml`.
Before activation, point its AppProject and Application at
`https://github.com/chnm/apiary-django.git` and provide read-only repository
access if required. Grant this repository access to the IncusOS runner group
and supply `ZOT_TOKEN` to CI. Digest pins require `GITHUB_TOKEN` write access
under the branch rules. Seed `kv/eso/apiary-django` with
`DJANGO_SECRET_KEY`, `DB_PASS`, existing `OBJ_STORAGE_*` values, and enabled
`ALLAUTH_*` credentials. Adjust the S3 egress hostname if the endpoint changes.

Do not activate Argo or change DNS until PostgreSQL, BOM SQLite, and object
storage backups have passed an isolated restore rehearsal. Preserve the old
deployment for rollback. Deployment reference:
https://docs.rrchnm.org/subsystems/reverse-proxy/k0s-public-edge/
