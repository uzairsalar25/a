# Royal News Video Editor

Autonomous pipeline that turns numbered raw videos (`01.mp4`, `02.mp4`, …)
into finished YouTube long-form Royal News videos. The editorial rules live
in the skill; the scripts here are the deterministic building blocks it calls.

- **Skill / rules:** `.claude/skills/royal-news-editor/SKILL.md`
- **Frame style:** `config/frame_style.md`

## Setup
```bash
pip install -r requirements.txt        # Python deps (requests)
# ffmpeg + ffprobe must be on PATH (h264_nvenc used automatically when present)
cp .env.example .env                   # then fill in the API keys
```

## Scripts
| Script | Step | Purpose |
| --- | --- | --- |
| `scripts/probe_video.py <video>` | 1 | Print JSON: duration, resolution, fps, audio, subtitle streams. |
| `scripts/transcribe_assemblyai.py <video> <out.srt>` | 1 | Transcribe to SRT via AssemblyAI. |
| `scripts/tts_elevenlabs.py --text-file T --out M.mp3` | 5 | Narration TTS via the ElevenLabs-compatible AI‑33‑Pro endpoint. |
| `scripts/render_video.py <timeline.json> <out.mp4> [--encoder auto\|nvenc\|cpu]` | 9 | One final encode: postcard frame, B‑roll montage, loudnorm, 1280×720/30fps. |
| `scripts/ffcommon.py` | — | Shared helpers (env loading, ffprobe, media duration). |

## Folders
```
input_videos/          raw NN.mp4 sources (git-ignored)
temp/                  SRT, EDL, narration mp3s, timeline json (git-ignored)
output/                NN_edited.mp4 + NN_metadata.txt (git-ignored)
assets/backgrounds/    postcard backgrounds (one chosen per video)
assets/grids/          alternative postcard backgrounds
assets/member_clips/<member>/   B-roll per royal member
```

## Timeline format
`scripts/render_video.py` consumes a timeline JSON (see the header docstring
in that script and `config/frame_style.md`). Object form:
```json
{
  "background": "assets/backgrounds/bg1.jpg",
  "frame": { "margin": 0.065, "border_px": 5 },
  "segments": [
    { "type": "main", "src": "input_videos/03.mp4", "start": 0.0, "end": 28.4 },
    { "type": "narr", "audio": "temp/03_narr_1.mp3",
      "clips": ["assets/member_clips/harry/a.mp4", "assets/member_clips/harry/b.mp4"] }
  ]
}
```
The renderer distributes narration B-roll to cover the narration audio
exactly (no freeze padding), pads main video inside the frame, and shows
narration clips over a blurred-fill copy of themselves.
