# DLL Dependency Cache Implementation Plan

> **For agentic workers:** use superpowers:subagent-driven-development. User authorized implementation on 2026-10-14. This workspace is not a Git repository; modify only assigned files, with independent review and real compiler verification.

**Goal:** Select DLLs, export/import SHA-256 records, reuse valid compressed dependency packages, and ship the updated Qt executable.

**Architecture:** Per-role selections feed a bounded hash pool and immutable-generation cache. A cached extractor writes only to the main installer's temporary directory; external Inno file entries perform final hash-verified installation under the original product's ownership.

**Tech Stack:** Python, PySide6 6.12, hashlib/concurrent.futures, Inno Setup 7, PyInstaller.

**Spec:** `../../../DLL选择与哈希缓存依赖包-需求分析.md`

## Constraints

- C required; A/B optional, independently installed. Runtime EXE/MSI installers stay first.
- Full file enumeration reads metadata only. Only selected DLLs are SHA-256 hashed on each build.
- No software snapshot, explanatory cards or installer preview.
- Cache identity includes normalized paths, SHA and recipe, not source root or mtime.
- No generated installer is executed on the build host. Tests may compile tiny fixtures.

## Task 1: Record, hashing and cache backend

Files: `packager/dependencies.py`, `packager/models.py`, `tests/test_dependencies.py`.

- [x] Write failing tests for byte changes despite unchanged metadata, missing selections, invalid paths, cache corruption and portable record export.
- [x] Implement `scan_files(root,cancel,on_batch)`, `hash_selected(root,paths,cancel,workers,progress)`, `load_record`, `save_record`, `cache_key`, `DependencyCache.find/publish`.
- [x] Extend BundleConfig with dependency_files, dependency_records, hash_workers; extend BuildResult with dependency_records.
- [x] Verify tests and inspect publication/rollback behavior.

## Task 2: Qt file selection and configuration

Files: `ui/dependencies.py`, `ui/mainwindow.py`, `tests/test_dependencies_ui.py`.

- [x] Write failing UI tests for persistent hidden selections, non-DLL restrictions, imported missing files, and configuration roundtrip.
- [x] Add a styled shared per-role file table with scan worker, search/filter/batch actions, record import/export and cancellation.
- [x] Connect role path rows, selected counts, hash-worker setting, and successful build record paths.
- [x] Verify widgets and background task shutdown, inspect screenshots.

## Task 3: Build and Inno integration

Files: `packager/builder.py`, `packager/inno.py`, `tests/test_dependency_build.py`, `tests/test_dependency_inno.py`.

- [x] Write failing tests for unchanged-cache reuse, changed DLL rebuild, selection changes and compiler rejection of incorrect SHA.
- [x] Resolve selected DLLs, compute fresh hashes and recipe, find/import validated cache or compile/publish helper.
- [x] Generate helper with no uninstaller, main external DLL entries with Hash/ExternalSize and PrepareToInstall extraction.
- [x] Store dependency records in build artifacts; protect old successful cache on failure/cancellation.
- [x] Avoid recompressing nested EXEs; use bounded LZMA2 block threads for newly compiled payloads.
- [x] Compile real helper/main/outer fixture chain, verify generated output and hash-mismatch failure without launching installers.

## Task 4: Review and release

- [x] Review integrated code against the spec and repair significant findings.
- [x] Run complete regression suite and native Qt screenshot checks.
- [x] Rebuild DesktopPackager.exe and ZIP; smoke-test frozen app and verify source/EXE/ZIP hashes.
- [x] Update release manifest, README and feature validation report with measured results and remaining installation-machine checks.
