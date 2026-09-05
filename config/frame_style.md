# Postcard Frame Style (STEP 8)

The signature look applied by `scripts/render_video.py`:

- Canvas: **1280x720**, 16:9, 30fps CFR (fixed).
- One background/grid asset is chosen **per video** and reused for the whole
  video (never rotated). Picked from `assets/backgrounds/` or `assets/grids/`.
  If both are empty, a neutral dark fill is used.
- Main video sits inside a **white-bordered rectangle**, inset ~7% each side.
  - Inner frame: ~1102 x 620 px
  - White border: 5 px, sharp edges
  - Source scaled to *fit* inside the frame (aspect preserved, no crop of
    subject); padded inside the frame if the aspect differs.
- Narration B-roll uses the **same** frame + background. Member clips that do
  not match the frame aspect are placed over a **blurred-fill copy of
  themselves** (not black bars, not cropped).
- Image stills get a slow, centred **Ken Burns** zoom (~8% over the clip).
- No overlays, no LUTs, no color grading, no logo/watermark removal.

## Tunable constants (top of `render_video.py`)
| Constant | Meaning | Default |
|----------|---------|---------|
| `MARGIN` | inset fraction each side | `0.07` |
| `BORDER` | white border px | `5` |
| `CANVAS_W/H` | output size | `1280x720` |
| `FPS` | output frame rate | `30` |
