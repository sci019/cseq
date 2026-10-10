# cseq Python compatibility release gate

## Established support

The release candidate must continue to support CPython **3.8 through 3.15 inclusive** on Windows x86-64. The `3.x` job follows newer stable CPython releases independently. Do not remove an existing supported Python version merely because newer versions are available.

## What happens on a release-branch update

1. The `Python compatibility` workflow reconstructs the archived base source and applies **all versioned overlays in ascending version order**. Future `release/overlay-X.Y.Z/` changes are therefore included automatically.
2. The stdlib-only `release/verify_python_compatibility.py` check rejects a raised Python floor, a Python upper bound that excludes an established version, missing dependency-profile markers, missing CI matrix entries, or an assembled version that does not match the latest overlay. This is deliberately a *failure gate*, not an automatic downgrade of requirements.
3. Every established Python version executes package installation with the Tree-sitter extra, `cseq doctor`, the full pytest suite, and bundled-document export. The latest stable `3.x` job runs separately. A failure is a compatibility regression to repair, **not** approval to drop the old version.
4. Manual TestPyPI and production PyPI workflows are blocked unless the **exact source commit SHA** has already passed the full compatibility workflow. Publishing remains manual, and the published version is derived from the dynamically assembled overlay, not a hardcoded `0.0.4` wheel name.

## Change procedure for subsequent releases

- Start from the maintained `python-compat-0.0.3` branch, or explicitly port these three checks into a successor release branch before publishing.
- Add the next `release/overlay-X.Y.Z/` with updated project metadata, application changes, and any Python-version-specific dependency profiles.
- Keep the CI matrix and oldest supported `Requires-Python` intact. If the latest Python causes a failure, fix the adapter/dependency or provide a versioned compatibility profile without removing the older lanes.
- Wait for all nine current CI jobs to succeed. Review the real-install and docs results before manually triggering publishing.
- After each new release, independently archive/offline-test the old-Python wheel sets and replicate them to the Windows PC `releases/dependency-wheelhouse/` folder as a version-specific immutable copy; the 0.0.4 snapshot does not automatically cover newer releases.
- Other OS architectures are a separate deferred task.

## Baseline retained artifacts

`cseq-0.0.4-legacy-wheels-win64-v1` GitHub Release holds the verified Python 3.8 and 3.9 Windows wheelhouses and SHA256SUMS. These are recovery artifacts; they do not change normal `pip install cseq[parser-tree-sitter]`.

Important implementation boundary: this workflow currently lives on the maintained release branch. If future development moves to a different branch, its protection does not follow automatically unless the workflows and guard are carried over.