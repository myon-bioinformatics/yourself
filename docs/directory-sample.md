# Bounded directory sample insertion

Directory observations still count every immediate entry without following
symlinks or reading file content. The bounded sorted name sample now inserts only
eligible names with bisect rather than sorting the whole sample for every entry.
Counting/error handling, Unicode name ordering and sample-size validation remain
unchanged. No platform-specific lookup, dependency or public API change.

```sh
uv run pytest -q
python -S -m scripts.benchmark_directory_sample
```

The added regression uses fake scandir entries with shuffled ASCII/Unicode names
and limits 0/1/10/100/300, comparing with a complete sorted-name oracle. No actual
machine paths or OS-specific installations are observed by that test.

Isolated sampling on 10000 seeded shuffled names, CPython 3.12.14/Linux, one warmup
plus seven alternating timing samples: limit 10 median 2.075→0.758 ms; limit 1000
94.933→2.605 ms. Raw samples in directory-sample-measurement.json. This excludes
filesystem/stat costs and does not measure complete directory observation speed,
memory, build time or CI duration. Both algorithms keep bounded sample storage.
