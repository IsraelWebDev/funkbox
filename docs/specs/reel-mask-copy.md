# Reel shot source: text mask from the copy deck

Status: built on `card/reel-mask-copy`, PR pending.

## Problem

Kinetic type is the trend, and funkbox can already mask a loop with typed text (PR #25, `mask: {"text", "font", "size", "align"}`). But that text lives in a loop recipe. A reel's approved copy and its `final.text_audit.json` never see it, so a word that appears in the video can ship without being locked or listed. Source: promo feedback of 2026-11-12, claude/promo-feedback-2026-11-12.md.

## Design

- A funk2 shot source may name a copy line: `"source": {"funk2": "recipe.json", "mask_copy_id": "hook"}`.
- The recipe has exactly one text-masked layer. The layer's mask keeps its font and size but has no `text` of its own. The copy line supplies the text.
- Validate (before any render):
  - the copy id exists in the deck (the same check as every other copy reference);
  - the recipe has exactly one text-masked layer;
  - that mask carries no text.
- Render: when the recipe loads, the copy text becomes the mask text. The poster text pipeline already refuses a mask whose drawn text is not exact, so a text that cannot be drawn fails the render, not the video.
- Audit: each funk2 shot with `mask_copy_id` gets one entry, `<shot>/source_mask/0`, with the exact copy text and the frames the shot shows. The audit `ok` flag fails if that entry is missing.
- Copy lock: the text lives in `copy.json`, which is hash-locked, so changing the word means re-approving the copy.

## Not in this card

- Validate does not check that the text fits the mask's frame; the render reports a failure if it does not draw.
- One text mask per recipe. Two text-masked layers are refused.
- The mask is static. A reveal (typewriter, per-letter) on a mask is a later card.

## Tests

- `tests/reel/test_mask_copy.py`: parse (funk2 only), copy-id reference, validate (recipe without text validates; unknown copy id; own text refused; two text masks refused).
- Render and audit entry are not covered by a local test here, because the GL tests need a display this laptop does not have. CI runs them.
