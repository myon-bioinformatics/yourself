# Native pytest evidence

## Same-run controlled child and JUnit collection

`test_native_failure_evidence` runs its existing child fixture with and without
JUnit. Actual `yourself.to_json()` output is compared with deliberately wrong
dummy expectations. Both modes retain exit 1 and identical full native phase
counts, validated by shared `pytest_receipt()`. The JUnit-enabled run imports
five identities with `cases_from_junit()`: two parameterized call failures,
setup error, teardown error and strict XPASS. Pass/skip and xfail controls remain.

The fixture-local correspondence maps call failure/strict XPASS to JUnit failure,
and setup/teardown failure to error. JUnit loses native phase information and
does not distinguish xfail from ordinary skip. Parameter variants retain native
hashes but share compact JUnit display names; both records are kept. Raw
parameter/message/stdout/stderr sentinels must be present before asserting that
both compact schemas exclude them. No reproduction input is inferred.
Generic classification, malformed receipt and interruption coverage stays
upstream in [xprobe #7](https://github.com/myon-bioinformatics/xprobe/pull/7).

CI fetches the test-only xprobe module at merged commit
`37d582ee4039d2803335b54e855ed770caedabfc`, verifying Git blob
`8cc1abbaf4269e5298de44f1b4ce7de692ec9ae2` before import.
The existing vendored adapter is unchanged. For local full-suite runs, use the
workflow's fetch/verify step, then `PYTHONPATH=build/shared python -m pytest`.
Missing importer fails collection rather than silently skipping this test.

With `YOURSELF_FAILURE_EVIDENCE` set to a fresh directory, child raw XML/native
JSONL and exit status are saved before validation; compact identities and
validated receipt are saved as soon as computed. CI uploads these separately as
`controlled-failure-<os>-py<python>` (14 days). Reused destinations are rejected.
Timeouts before subprocess completion are outside this retention example.
Public-repository artifacts are downloadable by signed-in users; controlled
reports contain dummy sentinels. No raw XML is published to Pages.

The canonical reusable JUnit workflow at `4dfda95d6573250477f991a0421fa6acb9bc0258`
collects exactly seven ordinary `test-report-<os>-py<python>/junit.xml` reports.
Its `test-report-*` download pattern excludes controlled evidence. Collection
runs after a failed producer; missing reports mean incomplete collection.
The producer's pytest result is never overridden by collector success.
The expected failing child makes the outer regression green only when its
observations match; unexpected outer failures still fail CI.

CI writes `reports/pytest-events.jsonl` through the shared xprobe pytest adapter
and preserves it with the JUnit report, including when tests fail. All seven
OS/Python jobs use the adapter. The runtime `yourself.py` remains stdlib-only.

For local runs, choose a fresh destination (existing evidence is never overwritten):

```sh
python -m pytest -p vendor.xprobe_pytest --xprobe-jsonl=reports/local-001.jsonl --xprobe-repository=myon-bioinformatics/yourself
```

The vendored adapter is byte-for-byte from xprobe commit
`326acd667e13b21bf53ccc1590af960edf8cbf6c`, `scripts/xprobe_pytest.py`.
Its license and SHA-256/Git blob provenance live alongside it in `vendor/`.
Update from a reviewed upstream commit and refresh the provenance together.

Records distinguish setup/call/teardown and collection, with native failure,
error, skip, xfail, xpass and strict XPASS outcomes. Records describe phases,
not one final result per test. A finish record's `complete` means no observations
were dropped; it does not mean tests passed. Missing finish means partial evidence.
The pytest exit status is preserved. Serial pytest only; xdist is not supported.

Repository identity is explicit. Commit SHA stays null until supplied from the
canonical metadata producer; GitHub's environment SHA is not substituted.
Tracebacks, captured output, marker reasons and parameter labels are omitted.
Test names remain visible, and node hashes are not encryption. Reports are
14-day Actions artifacts, not published Pages content.

## Explore downloaded reports

Using an xprobe checkout at the same commit (replace paths with your locations):

```sh
python /path/to/xprobe/scripts/xprobe_cli.py '"outcome": "xfail"' /path/to/downloaded-reports --include '*.jsonl'
python /path/to/xprobe/scripts/xprobe_cli.py '"phase": "teardown"' /path/to/downloaded-reports --include '*.jsonl'
```

For structured use, load the JSONL with `xprobe.corpus_from_json(text, jsonl=True)`.
Keep downloaded artifacts in separate job directories so identical filenames
do not overwrite each other. Run IDs distinguish observations across jobs.

Reproduction inputs and chat-text references must be explicit, reviewed records
linked by `origin_id`; this adapter does not infer causes or ingest chat history.
