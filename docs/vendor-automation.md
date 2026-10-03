# Test-only vendor placement and updates

`vendor.lock.json` is the sole provenance record for the xprobe adapter, importer
and upstream LICENSE. All three remain test-only and byte-for-byte upstream
copies. `yourself.py` remains a standalone stdlib module. Source commit/blob and
SHA-256 values are explicit; normal CI never resolves upstream main.
Git attributes disable text conversion for vendor copies, preserving upstream
bytes (including LICENSE line endings) on Windows as well as Unix checkouts.

The shared tool/workflow is pinned to merged commit
`ec71deac0b4232132130021037482b3f97670805`. CI checks the checked-in copies first,
then deletes those three copies and materializes them from their locked GitHub
commits before testing. Verification failures fail CI. Checked-in copies retain
offline local testing; no source edits or provenance JSON copying are needed to
prepare an update proposal. The former per-adapter provenance JSON is retired.
The generic tool's validation/error regressions stay upstream.

## Local preparation

A normal checkout can run `python -m pytest` offline with test dependencies
installed from `tests/requirements.txt`. To use the same placement/update tool:

```sh
git clone https://github.com/myon-bioinformatics/myon-bioinformatics.git .vendor-sync-tools
git -C .vendor-sync-tools checkout ec71deac0b4232132130021037482b3f97670805
python .vendor-sync-tools/vendor_sync.py check
python .vendor-sync-tools/vendor_sync.py materialize
```

`check` is offline. `materialize` restores missing/wrong locked copies after hash
verification. `update` must start from correct locked files; it rejects local
edits before networking. To prepare a candidate in a disposable clean checkout:

```sh
python .vendor-sync-tools/vendor_sync.py update
python -m pytest
```

All entries track `refs/heads/main` explicitly, avoiding a branch/tag name
collision. A group resolves once; all related files move together if any selected
bytes change. Source pins do not churn on unrelated upstream commits. Shared-tool
and workflow pins are a separate infrastructure update; the consumer regression
requires CI checkout, reusable workflow ref and tool-commit to be identical.

## Scheduled proposals: prepared, not activated

`.github/workflows/vendor-update.yml` provides Monday 02:23 UTC (11:23 JST) and
manual-dispatch proposals through the shared workflow. It is gated by the Actions
repository variable `VENDOR_UPDATES_ENABLED` being exactly `true`. No flag or
secret is configured by this change; the job is skipped until enabled.

Before enabling, configure an App installation token or suitable PAT as the
Actions secret `VENDOR_UPDATE_TOKEN`, then set the repository variable above.
For installation tokens, arrange token generation/refresh before enabling; a
static stored installation token expires. Do not paste credentials into issues
or PR comments. The authenticated real-GitHub proposal/consumer-CI smoke test is
still pending. Manual placement and normal CI do not require this write token.

During the first activated pilot, dispatch once and verify the PR tree, ordinary
consumer CI and raw/native/compact evidence; dispatch again with the same source
state and verify no duplicate open PR. Updates propose verified source files and
the lock together and do not merge automatically. Closed candidates may be
re-proposed. Unexpected remote branch changes fail the proposal rather than
force-pushing. Track activation/results in shared issue
[myon-bioinformatics#35](https://github.com/myon-bioinformatics/myon-bioinformatics/issues/35).
