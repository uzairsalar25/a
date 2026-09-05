# Postcard Frame Style (STEP 8)

The signature Royal News look. `scripts/render_video.py` applies this
automatically; the values below are the defaults and can be overridden per
video via the `"frame"` object in the timeline JSON.

## Layout
- Canvas: **1280x720**, 16:9, 30fps CFR (fixed delivery format).
- One **background asset** per video, chosen from `assets/backgrounds/` or
  `assets/grids/`, kept behind the frame for the ENTIRE video. Never rotate
  or cycle backgrounds within a single video.
- The content sits inside a **white-bordered rectangular frame**, inset with
  roughly a 6-8% margin on every side (default `margin = 0.065`).
- **White border**: ~4-7px at 720p (default `border_px = 5`), sharp edges.
- The frame is fully inside the canvas; source content is never cropped by
  the frame.

## Content fitting inside the frame
- **Main video**: scaled to fit the inner rectangle preserving aspect;
  letterbox-padded (black) inside the frame on aspect mismatch.
- **Narration B-roll**: each clip is fit to the inner rectangle preserving
  aspect and placed over a **blurred-fill copy of itself** (not black bars,
  not a crop of the subject). Image B-roll gets a slow 3-5% Ken Burns zoom.

## Hard rules
- Exactly ONE background/grid per video.
- No overlays, noise, grain, LUTs, color grading, or effect layers.
- No logo/watermark removal; member clips are only trimmed/fit, never altered.
- If both background folders are empty, the renderer falls back to a heavily
  blurred copy of the video's own frame.

## Timeline frame overrides
```json
{
  "background": "assets/backgrounds/parchment.jpg",
  "frame": { "margin": 0.07, "border_px": 6 },
  "segments": [ ... ]
}
```
