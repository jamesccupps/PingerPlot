"""Public half of the release-signing key (packaging/signing.py new-key).

The updater installs only releases whose latest.json is signed by the
matching private key, which lives on the maintainer's PC and in the
repository secret UPDATE_SIGNING_KEY, never in the repository.
"""

UPDATE_PUBLIC_KEY = "Zt+m/5tNeng4C3M8udzVifsyFPqHRGMJNuMWehLyKQ8="
