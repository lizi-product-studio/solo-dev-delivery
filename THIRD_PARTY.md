# Superpowers Lite integration

This distribution incorporates and adapts engineering guidance and installation/preflight techniques from [lizi-product-studio/superpowers-lite](https://github.com/lizi-product-studio/superpowers-lite), commit `e753ab13097b9f987f6f3ec4dadaea2656b887b2` (candidate 0.6.0). Superpowers Lite derives from [obra/superpowers](https://github.com/obra/superpowers), revision `bb92a77741419a4ab5f06e711a283343f1ada0c3`.

The MIT notice is included in the source at `vendor/superpowers-lite/LICENSE` and in exported Skills at `licenses/superpowers-lite-MIT.txt`. It applies to the incorporated material and does not relicense unrelated components of AGI-like.

Adapted material: risk-scaled engineering guidance, stateless capability requirement reporting, and link-safe/idempotent installation checks. The four Lite Skills are consolidated into one optional engineering reference rather than registered as a second bundle. The native queue, resource routing, AAP envelope, candidate-bound challenge receipts, and failure/evidence gates remain AGI-like's implementation. Lite's separate delivery report protocol and file-count routing thresholds are not imported.

Frontend quality guidance is an independent synthesis of the primary sources linked in `references/frontend-quality.md` (source path: `skill/agi-like/references/frontend-quality.md`). It does not vendor the external Skills or copy their text/code. Source snapshots and license-evidence boundaries are recorded with the frontend learning evaluation; no external brand's visual or writing preferences are imposed as universal rules.
