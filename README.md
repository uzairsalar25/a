# 👑 Royal News — Full Auto Video Editor

Ek folder me numbered raw videos (`01.mp4`, `02.mp4` …) daalo, ek command chalao,
aur finished YouTube Royal-News videos (1280x720, 30fps) + metadata mil jaayenge —
**bina kisi sawaal ke, fully automatic.**

Pipeline: `probe → subtitles → EDL (story analysis) → narration TTS → member B-roll
→ postcard-frame timeline → FFmpeg render → YouTube metadata`.

---

## 🚀 Quick Start

```bash
# 1) Python packages
pip install -r requirements.txt

# 2) ffmpeg + ffprobe (system pe hona chahiye)
#    Ubuntu : sudo apt install ffmpeg
#    Mac    : brew install ffmpeg
#    Windows: https://www.gyan.dev/ffmpeg/builds/  (PATH me daalein)

# 3) config
cp .env.example .env        # keys optional — default sab FREE

# 4) apni videos daalein
#    input_videos/01.mp4, 02.mp4, ...

# 5) B-roll + backgrounds daalein  (neeche "Assets" dekhein)

# 6) CHALAO 🎬
python run_batch.py 1 5          # videos 01..05
python run_batch.py 3            # sirf 03
python run_batch.py 3 9 --encoder cpu
```

Output: `output/NN_edited.mp4` + `output/NN_metadata.txt`
Logs: `output/progress.log`, `output/errors.log`

---

## 🎙️ Voice & 📝 Subtitles — kya default hai

| Cheez | Default (FREE) | Optional upgrade |
|-------|----------------|------------------|
| **Voice (TTS)** | EdgeTTS — `en-US-GuyNeural` (koi key nahi) | AI33Pro/ElevenLabs (`.env` me `TTS_ENGINE=ai33pro`) |
| **Subtitles** | Whisper local (koi key nahi) | AssemblyAI (`.env` me `ASSEMBLYAI_API_KEY`) — ho to auto use, warna Whisper |

Voice badalna ho to `.env` me `EDGE_TTS_VOICE` change karein. List dekhne ke liye:
```bash
edge-tts --list-voices | grep en-
```
Achhe news-anchor voices: `en-US-GuyNeural`, `en-GB-RyanNeural`, `en-US-ChristopherNeural`.

---

## 📦 Assets (ek baar setup)

```
assets/
├── member_clips/
│   ├── harry/       ← Harry ke photos (.jpg/.png) + short clips (.mp4)
│   ├── meghan/
│   ├── charles/  camilla/  william/  kate/  andrew/  ...
│   ├── Harry with Meghan/     ← GROUP folders (dono members wale)
│   ├── kate with william/
│   └── general_royal/         ← generic monarchy shots
├── backgrounds/     ← postcard ke peeche wala background (image/video)
└── grids/           ← ya grid style background
```

- Narration ke waqt jis member ki baat hoti hai, sirf **uske** folder(s) ke clips
  use hote hain (Meghan ki baat pe Kate/William nahi aayenge).
- Har clip video me **sirf ek baar** use hota hai (repeat nahi).
- Photos aur videos dono chalti hain — photos pe slow Ken Burns zoom lag jaata hai.
- Har video ke liye **ek** background chun kar poori video me wahi rehta hai.

> Folders khaali honge to us member ka B-roll skip ho jaayega — kam se kam
> `general_royal/` me kuch clips zaroor rakhein.

---

## 🧠 Do modes

1. **Full-auto (`python run_batch.py`)** — sab kuch script khud karti hai (heuristic
   story analysis). Bina dekhe chal jaati hai. Fast, hands-off.
2. **Claude-driven (skill)** — Claude Code me `royal-news-editor` skill se chalao;
   tab EDL (kaunsa hissa rakhna, hook, narration script) Claude khud likhta hai —
   editorial quality behtar hoti hai. Baaki mechanical steps yahi scripts karte hain.

Dono ek hi file format (`temp/NN_edl.json`, `temp/NN_timeline.json`) use karte hain.

---

## ⚙️ Tuning

| Kahaan | Kya |
|--------|-----|
| `.env` | voice, subtitle engine, encoder |
| `scripts/build_edl.py` (upar constants) | retention %, silence gap, block length, hook |
| `scripts/render_video.py` (upar constants) | frame margin, border, canvas, fps |
| `config/frame_style.md` | postcard frame ki poori spec |

---

## 🧩 Scripts

| File | Kaam |
|------|------|
| `run_batch.py` | orchestrator — ek command me poora batch |
| `scripts/probe_video.py` | video info (ffprobe/ffmpeg) |
| `scripts/transcribe.py` | subtitles (Whisper / AssemblyAI / embedded / sidecar) |
| `scripts/build_edl.py` | story analysis → hook + keep-segments + narration |
| `scripts/tts.py` | narration voice (EdgeTTS / AI33Pro) |
| `scripts/build_timeline.py` | B-roll selection + timeline |
| `scripts/render_video.py` | postcard frame + FFmpeg render (NVENC/CPU) |
| `scripts/gen_metadata.py` | YouTube title/description/tags |

---

## ❗ Notes / limits

- **NVENC (GPU)** ho to auto use hota hai, warna `libx264` (CPU) — dono chalte hain.
- Heuristic auto-EDL me kabhi-kabhi retention 60% se thoda upar reh sakti hai ya
  ek chhota intro line reh sakti hai; behtar editorial cuts ke liye Claude-driven
  mode use karein.
- Koi bhi video fail ho to batch rukta nahi — `errors.log` me likh kar agli video
  pe chala jaata hai.
