# Changelog

## 1.5.4

Current public release.

### Added

- Dashboard benchmark tab for running FastFlowLM model benchmarks.
- Notes setup tab for vault location, categories, and LLM note behavior.
- FastFlowLM runtime status and update check in the dashboard.
- Local note search support for chat context.

### Changed

- App files now live at the repository root instead of a nested packaging folder.
- Public README now includes screenshots and first-run setup instructions.
- Default privacy mode keeps selected text redacted from history unless enabled.

### Fixed

- Note capture supports inbox fallback safely.
- Ask-in-chat launches without blocking the daemon.
- Multiline mode prefixes such as `prompt:` parse correctly.
- Production config/data/log paths resolve consistently between Python and AutoHotkey.

### Security

- Local daemon POST actions require the `X-FFP-API` header.
- Config patching is restricted to approved keys and local patch files.
- Update ZIP extraction validates paths before unpacking.
