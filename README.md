# client-build

A small, reusable CI pipeline that produces signed, white-label desktop and
Android builds of open-source apps from their upstream sources.

Build profiles (names, colors, icons, signing material) are **not** stored in
this repository. They are supplied at build time from external storage and from
CI Secrets, so the same generic pipeline can target many profiles without
hardcoding any of them here.

## How it works

Each workflow run takes a `brand` id and an upstream `ref`, then:

1. fetches that profile (config + icon) from external object storage;
2. clones the requested upstream `ref`;
3. applies the profile (app name, colors, icons, identifiers);
4. builds and signs the installers;
5. uploads the artifacts back to object storage.

Artifacts are published to storage only — not to this repository.

## Layout

- `scripts/prepare.py` — clone upstream, apply a profile, generate icons.
- `scripts/fetch_brand.py` — pull a profile + icon from storage at build time.
- `scripts/r2_upload.py` — upload built artifacts to storage.
- `scripts/adapters/` — per-framework configuration adapters.
- `.github/workflows/` — `desktop`, `android`, `cmfa` build workflows.

## Run

Trigger a workflow from the Actions tab and provide the `brand` id and `ref`.

## Configuration & secrets

Nothing brand-specific lives in this repo. Required CI Secrets:

- Storage access: `R2_ACCOUNT_ID`, `R2_BUCKET`, `R2_AUTH_EMAIL`, `R2_AUTH_KEY`.
- Android signing (per profile, profile id uppercased): `KEYSTORE_BASE64_<ID>`,
  `KEY_ALIAS_<ID>`, `STORE_PASSWORD_<ID>`, `KEY_PASSWORD_<ID>`.

Signing keystores are never committed; they are decoded from Secrets at build
time only.
