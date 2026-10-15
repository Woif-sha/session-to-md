# Upstream

Source: https://github.com/Ann-Diana/codex-project-chat-exporter

Reviewed revision: `e4c54576c3d542df423f88e90ae604eb935f646c`.

The Python history resolver adapts the upstream `resolveHistoryPlan`, `validateHistoryBoundary`, and logical segment streaming algorithms in `bin/export-codex-project-chats.mjs`: resolve references by rollout identity, validate byte and ordinal cutoffs, traverse ancestor segments oldest first, and omit segment metadata from conversation output. Its paginated-history fixtures informed the branch-boundary tests.

Session to MD is a separate, locally maintained skill. It selects the invoking session, adds copied-fork attribution and visible-message mirror matching, and renders only the agreed single Markdown transcript. It does not bundle upstream document renderers, asset handling, bulk project selection, or archive manifests. The upstream MIT notice is retained in `LICENSE`.

Git remote `upstream` records provenance; it is not a publication destination for this skill. `.openskills.json` marks the local implementation `local-only` so the skills updater will not replace it with the upstream project.
