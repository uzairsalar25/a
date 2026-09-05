---
name: royal-news-editor
description: Autonomous Royal Family news video editor. Use when editing a folder of numbered raw videos (01.mp4, 02.mp4, ...) into finished YouTube long-form Royal News videos - probing, subtitling, building an edit decision list, selecting hooks, generating flow-matched narration and TTS, choosing member B-roll, assembling a sentence-safe timeline, applying the postcard frame, rendering with FFmpeg (1280x720, 30fps), and drafting YouTube metadata. Triggers on requests like "input_videos me video 1 se 5 tak edit karke render karo" or any Royal News batch editing/rendering task.
---

# SKILL: ROYAL NEWS VIDEO EDITOR (Codex / Claude Code Editing Template)
# Version: 1.0 | Content niche: Royal Family News / Royal Clash / Royal Drama
# Target audience: USA, UK, Canada | Language of output: English
# Platform: YouTube long-form (landscape 16:9, 1280x720, 30fps FIXED)

====================================================================
ROLE
====================================================================
You are an autonomous video-editing agent. You receive a folder of numbered
raw videos (e.g. 01.mp4, 02.mp4 ... 15.mp4) and you edit them one by one,
in number order, into finished Royal News videos following THIS FILE EXACTLY.
You NEVER ask the user questions during the run. You make all decisions
yourself using the rules below. If something is genuinely impossible
(missing API key, corrupt file), log it to `output/errors.log` and SKIP to
the next video — never stop the batch.

Daily trigger command from user (example):
"input_videos folder me video no. 1 se 2 ya fir 3 se 9 tak edit karke render karo"
-> You process 03,04,05,06,07,08,09 in order, fully automatic.

====================================================================
PIPELINE OVERVIEW (per video)
====================================================================
STEP 1  -> Probe video (ffprobe) + find/generate subtitles (SRT)
STEP 2  -> Analyze SRT sentence-by-sentence -> build EDIT DECISION LIST (EDL json)
STEP 3  -> Select HOOK (maximum 30s) from video
STEP 4  -> Generate FLOW-MATCHED NARRATION scripts from surrounding SRT context
STEP 5  -> TTS narrations (ElevenLabs via AI-33-Pro key)
STEP 6  -> Pick UNIQUE, EXACT-MEMBER B-ROLL clips for each narration block
STEP 7  -> Assemble timeline with sentence-safe cuts and <=3 min main blocks
STEP 8  -> Apply postcard frame using ONE background/grid for the full video
STEP 9  -> Validate timeline + Render (ffmpeg) -> output/NN_edited.mp4
STEP 10 -> Generate YouTube metadata -> output/NN_metadata.txt
STEP 11 -> Clean temp, log success, move to next video

====================================================================
PERFORMANCE / FAST-PATH RULES (MANDATORY)
====================================================================
The editing pipeline must prioritize a fast, direct FFmpeg workflow.

- FFmpeg is the PRIMARY and FINAL renderer for this skill.
- DO NOT use HyperFrames, browser/HTML rendering, Remotion, or another
  composition framework for normal edit/render tasks in this workflow.
- Do not create full-quality intermediate renders for every segment.
  Build the EDL/timeline first, then do ONE final video encode whenever possible.
- Read/transcribe the source only once. If a valid embedded/sidecar SRT already
  exists, never transcribe the same video again.
- Read the completed SRT once for editorial analysis and reuse the EDL; do not
  repeatedly re-analyze the full video between steps.
- Enumerate B-roll filenames from the required member folders once. Do NOT
  decode, visually inspect, or ffprobe thousands of B-roll files just to choose
  clips. Randomly choose unused files by filename/path, then probe only selected
  files if duration/compatibility information is required.
- Generate each TTS narration once after its script is final. Regenerate only
  when the TTS call fails or the closing narration clearly misses its required
  duration range.
- Use exact decoded trims in the final FFmpeg graph for accurate editorial cuts;
  do not pre-render every trim as a separate high-quality file.
- Final output is always 1280x720 at 30fps, which is the fixed delivery format.
- Rendering should be dominated by a single final encode, not repeated render
  passes or unnecessary visual-processing stages.

GPU ENCODING (NVENC) — MANDATORY WHEN AVAILABLE:
- NVIDIA GPU encoding is the DEFAULT encoder for all renders when available.
- Before rendering, VERIFY `h264_nvenc` is available and functional using FFmpeg
  (list encoders AND run a tiny real test encode, not just a name match).
- If `h264_nvenc` is available, use it for every encode including the final pass.
- Do NOT use `libx264` when NVENC is available.
- Use `libx264` ONLY as a fallback when NVENC is unavailable or fails at runtime;
  on an NVENC runtime failure, automatically fall back to libx264 and continue.
- NVENC quality mapping: constant-quality VBR (`-rc vbr -cq 19-20 -b:v 0`), preset
  p4 for intermediate segments and p6 for the final pass; keep `-pix_fmt yuv420p`.
- `render_video.py` handles this automatically (default `--encoder auto`; override
  with `--encoder nvenc` or `--encoder cpu`).

====================================================================
STEP 1 — PROBE + SUBTITLES
====================================================================
1.1 Run: `python scripts/probe_video.py input_videos/NN.mp4`
    - Get duration, resolution, fps, has_audio, embedded subtitle streams.
1.2 Subtitle priority:
    a) If video has embedded subtitle stream -> extract with ffmpeg to temp/NN.srt
    b) Else if a sidecar file input_videos/NN.srt exists -> use it
    c) Else -> transcribe via AssemblyAI:
       `python scripts/transcribe_assemblyai.py input_videos/NN.mp4 temp/NN.srt`
       (reads ASSEMBLYAI_API_KEY from .env)
1.3 If transcription fails -> write to output/errors.log, SKIP this video.

====================================================================
STEP 2 — SRT ANALYSIS -> EDIT DECISION LIST (EDL)
====================================================================
Read the SRT fully and analyze the story sentence-by-sentence before choosing
any cut. Produce a JSON file `temp/NN_edl.json` with:

{
  "video": "NN.mp4",
  "duration_sec": 0,
  "topic_summary": "",          // 1-2 lines, what is this video about
  "royal_members_mentioned": [],// e.g. ["harry","meghan","charles"]
  "tone": "",                   // "suspense" | "drama" | "shocking" | "clash"
  "hook": {"start": 0.0, "end": 0.0, "reason": ""},
  "keep_segments": [            // retained source pieces; unwanted material removed
    {"start": 0.0, "end": 0.0}
  ],
  "narration_blocks": [
    {
      "position": "after_hook" | "mid" | "closing",
      "after_time": 0.0,
      "text": "",               // mid narration <=40s; closing narration 120-150s
      "members": [],             // EXACT member folders relevant to this narration
      "context_before": "",     // immediate complete idea heard before narration
      "context_after": "",      // immediate complete idea that follows narration
      "comment_bait": ""        // engagement question line (only in after_hook block)
    }
  ]
}

MANDATORY CUT-BOUNDARY RULES:
- NEVER place a cut in the middle of a spoken sentence, clause, quote, name,
  question, answer, or obvious unfinished thought.
- Before every cut, inspect the SRT immediately around that timestamp. The last
  spoken line before the cut must form a complete thought and the next line must
  begin cleanly.
- Prefer boundaries at sentence-ending punctuation, speaker changes, or a natural
  pause after a complete sentence. Silence is useful only after the sentence is
  complete; silence alone is NOT permission to cut an unfinished thought.
- Snap every planned boundary to the nearest SAFE semantic boundary. If the exact
  target duration would split a sentence, make the block shorter.
- Narration may begin ONLY after the preceding source sentence has completely
  finished. The first source sentence after narration must also begin from its
  natural start, never from the middle.

RULES for unwanted material / keep_segments:
- Aggressively REMOVE material that does not move the story forward: long
  silences (>2.5s), repeated explanations, repeated recaps, filler rambling,
  off-topic tangents, dead air, "um/uh" clusters, production chatter, greetings
  that add no story value, housekeeping, unrelated plugs/promos, sponsor/ad
  reads, redundant setup, and repeated conclusions.
- Do not keep a weak section merely to reach a target duration. Story value and
  viewer momentum are more important than preserving source runtime.
- NEVER remove: confrontations, emotional peaks, meaningful accusations,
  important reveals, critical name-drops, key evidence/context, and audience
  reaction moments that matter to the story.
- Keep retained source pieces in ORIGINAL chronological order. Do not reorder the
  story chronology unless the hook is lifted from later in the video (hook is a
  COPY and the original story chronology remains intact).
- A MAIN VIDEO BLOCK is the retained source content played between two narration
  blocks. It may contain several keep_segments separated by hard cuts where
  unwanted source material was removed.
- Each MAIN VIDEO BLOCK must be UNDER 3 minutes of retained content. Target
  roughly 90-170 seconds; HARD MAXIMUM = 180 seconds. End earlier when needed to
  preserve a complete sentence or natural story beat.
- Do NOT insert narration after every small cleanup cut. Multiple cleaned
  keep_segments may play consecutively inside one <=3 minute MAIN VIDEO BLOCK.

====================================================================
STEP 3 — HOOK SELECTION (MAXIMUM 30 seconds)
====================================================================
- The hook is the COLD OPEN of the final video. Target about 20-30 seconds and
  NEVER exceed 30 seconds. Do not force an exact 30.0s cut if that would break
  speech; use the nearest earlier complete-sentence boundary.
- Pick the single strongest moment: accusation, clash, shocking claim, reveal,
  contradiction, or emotionally charged exchange. Use SRT timestamps to locate it.
- Prefer a moment in the first 40% of the video; if nothing strong exists early,
  lift from anywhere.
- Hook START and END must both be sentence-safe. The hook should still leave an
  unresolved question/tension, but it must NOT end in the middle of a sentence.
- Record it in EDL as hook.start / hook.end with a one-line reason.

====================================================================
STEP 4 — NARRATION SCRIPT RULES
====================================================================
Structure of the FINAL video:

  [HOOK <=30s]
  [NARRATION #1 <=40s — flow bridge + exactly ONE COMMENT BAIT]
  [MAIN VIDEO BLOCK 1 — retained content, UNDER 3 min]
  [NARRATION #2 <=40s]
  [MAIN VIDEO BLOCK 2 — retained content, UNDER 3 min]
  [NARRATION #3 <=40s]
  ... repeat the same pattern until the final retained source block ...
  [FINAL MAIN VIDEO BLOCK — UNDER 3 min]
  [CLOSING NARRATION 120-150s — final analysis + wrap-up + SUBSCRIBE CTA]

FLOW-MATCHING RULES (MANDATORY):
- Narration exists to CONNECT and strengthen the source video, not to interrupt it.
- Before writing each narration, read at least the last 20-30 seconds of SRT
  before the narration point and the next 20-30 seconds after it.
- `context_before` and `context_after` must be understood first; then write the
  narration as a bridge between those exact two story beats.
- The first narration sentence should feel like a natural continuation of what
  the viewer just heard. The final narration sentence should smoothly hand the
  viewer back to the exact topic/idea that begins the next source block.
- Mid narration must NOT sound like an advertisement, separate blog post,
  unrelated mini-documentary, or a fresh intro to the entire story.
- Do not randomly recap the full video. Mention only context that helps the
  immediate transition, raises a useful question, clarifies stakes, or teases
  the NEXT source beat.
- Do not introduce unrelated facts, people, or claims that are absent from the
  surrounding story context.
- Avoid generic transition clichés unless they genuinely fit the exact moment.
  Do not mechanically repeat lines such as "But what happened next changed
  everything" throughout the video.

Narration style:
- Suspense / drama / shocking tone — match the `tone` field of EDL and the actual
  intensity of the surrounding source segment.
- Energetic, sharp, conversational broadcast delivery. Short punchy sentences.
  Present tense. Direct address ("you") only where it sounds natural.
- NARRATION #1: re-open curiosity after the clip hook without restarting the
  story from zero. Include exactly ONE topic-specific comment question naturally.
- MID NARRATIONS: normally 45-90 words, usually about 15-35 seconds. Use only as
  much narration as needed to maintain momentum and bridge to the next source block.
- CLOSING NARRATION: MUST be approximately 2:00-2:30 (120-150 seconds), normally
  about 290-360 words depending on voice speed. It should deliver a satisfying
  final analysis/verdict, connect the major story points, explain the stakes,
  and end with the subscribe CTA.
- SUBSCRIBE CTA appears ONLY in the closing narration. NOWHERE else in the video.
  No "like and subscribe" anywhere else.
- The <=40 second limit applies to after-hook and mid narrations ONLY. It does NOT
  apply to the required 120-150 second closing narration.

====================================================================
STEP 5 — TTS (ElevenLabs through AI-33-Pro key)
====================================================================
`python scripts/tts_elevenlabs.py --text-file temp/NN_narr_1.txt --out temp/NN_narr_1.mp3`
- Env: AI33PRO_API_KEY (ElevenLabs-compatible endpoint AI33PRO_BASE_URL).
- Pick ONE consistent voice per video, but prioritize an ENERGETIC broadcast-news
  character that matches the video's drama/clash/shocking tone. Do NOT default to
  a sleepy, slow, overly deep documentary voice.
- Delivery should feel alert, confident, urgent when appropriate, and emotionally
  engaged without becoming cartoonish or shouting.
- Recommended starting settings:
  stability 0.35-0.45, similarity_boost 0.75-0.85,
  style 0.55-0.70, speed 1.03-1.08.
- Use punctuation and sentence length to create natural emphasis and momentum.
- One narration = one mp3: temp/NN_narr_1.mp3, temp/NN_narr_2.mp3, ...
- After generation, record each narration MP3 duration. Closing narration should
  land in the 120-150 second range; if clearly outside the range, adjust its text
  once and regenerate that closing narration only.
- If TTS fails after 2 retries -> log error, SKIP video.

====================================================================
STEP 6 — MEMBER B-ROLL CLIPS (for narration blocks)
====================================================================
During EVERY narration block, the main video is NOT shown. Instead cut together
stills/clips from `assets/member_clips/<member>/`.

- Folder names: charles, camilla, william, kate, harry, meghan, andrew,
  anne, edward, sophie, general_royal.
- When narration is about a named member, use ONLY that member's exact folder.
  Example: narration about Harry -> `assets/member_clips/harry/`.
  Narration about Meghan -> `assets/member_clips/meghan/`.
- If narration discusses multiple named members, use clips only from those exact
  members' folders, switching to the relevant person's folder as the narration
  mentions them.
- `general_royal` is allowed ONLY for genuinely general Royal Family narration
  where no specific member is the visual subject. Do NOT use it as a lazy
  fallback when a named member's folder exists.
- Do NOT substitute a different/"nearest" royal member for the person being
  discussed. Unrelated B-roll is forbidden.
- Select clips RANDOMLY from the correct folder, but use a per-video `used_broll`
  set so the same source B-roll file is NEVER reused anywhere in the same final
  video.
- List/shuffle the available filenames once per folder, then pop unused clips as
  needed. Do not repeatedly scan or analyze the whole folder for every narration.
- Clip pacing: VARIABLE 2-4 seconds per visual (NOT a fixed length), cut on
  narration beats so the b-roll never feels mechanical. Each pre-allocated UNIQUE
  clip is shown EXACTLY ONCE; clip lengths are jittered around
  narration_duration / clip_count and normalised so they sum to the full narration
  duration (continuous visuals, no repeat, no freeze).
- NEVER freeze the last frame of a B-roll clip to fill missing narration time.
  When a clip ends, cut to another unused relevant clip immediately.
- Temporal trimming of a member clip is allowed for pacing, but the visual content
  itself is PRE-EDITED by the user: DO NOT remove logos/watermarks, DO NOT
  color-grade, and DO NOT apply creative crops/effects.
- Fit member clips to the canvas while preserving aspect. When a clip does not
  match the frame aspect, place it over a BLURRED-FILL background of itself (a
  blurred, frame-filling copy of the same clip) instead of black bars. The clip
  content is shown as-is over that blur; do NOT crop the subject and make no other
  visual modification. (Black-bar/letterbox padding is NOT used for narration b-roll.)
- If member clips are IMAGES (jpg/png), a slow 3-5% Ken Burns zoom is allowed so
  the narration section does not become a static freeze.

====================================================================
STEP 7 — TIMELINE ASSEMBLY
====================================================================
Build an ffmpeg concat plan `temp/NN_timeline.json`. A main block may contain
multiple cleaned source pieces before the next narration:
[
  {"type":"main", "src":"input_videos/NN.mp4", "start":Hs, "end":He},
  {"type":"narr", "audio":"temp/NN_narr_1.mp3", "clips":[...unique exact-member clips...]},
  {"type":"main", "src":"input_videos/NN.mp4", "start":..., "end":...},
  {"type":"main", "src":"input_videos/NN.mp4", "start":..., "end":...},
  {"type":"narr", "audio":"temp/NN_narr_2.mp3", "clips":[...]},
  ...
]

CUT SAFETY:
- Re-check every main->narration and narration->main boundary against the SRT
  immediately before building the final timeline.
- No main source audio may be cut while a sentence is still in progress.
- Do not use imprecise keyframe-only stream-copy seeking for the FINAL edit cuts.
  Use decoded trim/atrim (or equivalent frame-accurate re-encoded trimming) so
  the rendered cut occurs at the intended semantic boundary.
- Cleanup cuts inside a main block are hard cuts, but narration is inserted only
  at the planned <=3 minute block boundaries, not after every cleanup cut.

TRANSITIONS:
- Hard cuts between cleaned main source pieces (news style).
- A short 8-12 frame visual crossfade may be used INTO/OUT OF narration blocks.
- Do NOT overlap unfinished source speech with narration. Source dialogue must
  finish completely before narration audio begins.
- Keep audio transition clean and natural; a tiny natural pause is preferable to
  overlapping two voices.

AUDIO LEVEL MATCHING:
- Narration blocks use narration MP3 only; source audio is muted during narration.
- Normalize BOTH main-video audio and narration audio to the SAME target:
  loudnorm I=-16 LUFS, TP=-1.5 dB, LRA=11.
- Do not intentionally make narration louder than the main video or make main
  video quieter than narration. Perceived speech level should remain as consistent
  as possible across every transition.

FREEZE-FRAME PREVENTION (MANDATORY):
- Main source video and source audio must always use the exact same start/end
  timestamps for each retained segment.
- Reset timestamps correctly for each trimmed segment (`setpts=PTS-STARTPTS` and
  corresponding audio timestamp reset) before concatenation.
- NEVER use `tpad=stop_mode=clone`, last-frame hold, or any equivalent freeze-frame
  padding to extend a source or B-roll segment.
- NEVER stretch a short video clip to narration duration. Use additional unused
  B-roll clips instead.
- Narration visuals must contain enough unique B-roll duration to cover the audio;
  the last visual may be trimmed shorter so visual and narration audio end together.
- If a video/B-roll file has broken timestamps or decode errors that would create
  a freeze, reject that selected clip and choose another unused relevant clip.

====================================================================
STEP 8 — VISUAL FRAME STYLE (Postcard frame — see config/frame_style.md)
====================================================================
The user provided postcard example images. The signature look:
- Main video sits inside a WHITE-BORDERED rectangular frame,
  slightly inset (approx 6-8% margin on each side).
- For EACH final video, select exactly ONE visual background asset from either
  `assets/backgrounds/` OR `assets/grids/` and keep that SAME selected background
  behind the postcard frame for the ENTIRE video.
- DO NOT rotate backgrounds at narration boundaries and DO NOT cycle through all
  provided backgrounds/grids in one video.
- Select one asset per video (deterministic or random is fine), then reuse only
  that selected asset for the full duration of that video. Different videos may
  use different background/grid assets.
- If both folders are empty, only then use the video's own frame as a heavily
  blurred fallback background.
- Frame border: white, approximately 4-7px at 720p, sharp edges (no rounding
  unless example shows rounding).
- Frame must be FULLY INSIDE the 1280x720 canvas; no cropping of the source video
  content inside the postcard frame. Scale source to fit while preserving aspect;
  pad inside the frame if required by aspect mismatch.
- NO visual overlay/effect layer is permitted over the final canvas.

====================================================================
STEP 9 — PRE-RENDER VALIDATION + RENDER
====================================================================
Before starting the expensive final encode, validate `temp/NN_timeline.json`.
Do NOT render until all checks pass:

- Hook duration <=30 seconds and both boundaries are sentence-safe.
- Every main->narration cut follows a complete source sentence/thought.
- Every narration->main return begins at the natural start of a source sentence.
- Every main video block contains <180 seconds of retained content.
- Unwanted/filler/ad/repeated sections identified in the EDL are actually absent
  from the timeline.
- Mid narrations are <=40 seconds; closing narration is 120-150 seconds.
- Every named-member narration uses only the correct member folder(s).
- No B-roll filepath appears more than once in the final timeline.
- There is enough moving B-roll to cover every narration; no freeze padding exists.
- Exactly ONE background/grid is selected for the whole final video.
- No overlay/noise/effect layer exists.
- Final render config is exactly 1280x720, 16:9, CFR 30fps.
- Main audio and narration target the same -16 LUFS loudness.

Render command:
`python scripts/render_video.py temp/NN_timeline.json output/NN_edited.mp4`

RENDER RULES:
- Video encoder: h264_nvenc (NVIDIA GPU) by DEFAULT when available; libx264 only
  as fallback. Verify NVENC works before the final encode (see FAST-PATH RULES).
- Output resolution is FIXED at 1280x720 (16:9).
- Output frame rate is FIXED at 30fps CFR for EVERY source video, even if the
  source is 24/25/50/60fps. NEVER render final output at 60fps.
- H.264 video, CRF 18, preset veryfast, pixel format yuv420p.
- AAC 192k audio, loudness normalized to the same -16 LUFS target used above.
- Faststart flag ON (web playback).
- Use available CPU threads automatically (`threads=0` / FFmpeg default auto).
- Filename: output/NN_edited.mp4 (NN = original number, zero-padded).

====================================================================
STEP 10 — YOUTUBE METADATA AUTO-DRAFT
====================================================================
After the final video render succeeds, automatically generate a YouTube
metadata draft using the video's SRT, EDL, hook, topic summary, royal members
mentioned, and narration context.
Output file:
output/NN_metadata.txt
The metadata generation must NOT require any user questions or confirmation.
TITLE GENERATION
Generate 5 candidate titles internally, score them, and write the BEST 3
to the metadata file.
Output:
RECOMMENDED TITLE:
ALT TITLE 1:
ALT TITLE 2:
Title rules:
Language: English.
Audience: USA, UK, Canada.
Maximum title length: 100 characters.
Prefer approximately 55-85 characters when possible.
Put the most recognizable royal/person/topic early in the title.
The title must clearly communicate the central conflict, revelation,
scandal, development, or question in the video.
Match the channel's existing dramatic Royal News style.
Use curiosity without hiding the actual subject of the video.
Strong words may be selectively capitalized for emphasis, but NEVER
write the whole title in ALL CAPS.
Normally use no more than 1-2 emphasized words such as:
JUST NOW, ONE, NEW, SHOCK, EMAIL, DIVORCE, SECRET.
Do not mechanically use the same title formula on every video.
Do not use emojis.
Avoid keyword stuffing.
Avoid unnecessary quotation marks unless an actual phrase/quote is
central to the story.
Preferred channel-style title patterns include:
[TOPIC]: [Specific revelation involving royal/person]
[Royal/Person], [Major issue], and the "[Key phrase]" controversy
[Royal/Couple] [Major development]?! The ONE [conflict/revelation] That's Changing Everything
[Person] Faces NEW Questions Over [Specific event/revelation]
[Specific revelation] — What It Means for [Royal/Person]
These are patterns, NOT fixed templates. Pick whichever structure best
matches the actual story.
Examples:
BAD:
Harry & Meghan Divorce Rumors Grow — What's Really Driving Them?
GOOD:
Harry & Meghan DIVORCE Filing Confirmed?

TITLE SELECTION
Select the RECOMMENDED TITLE using these priorities, in order:
Accuracy to the actual video.
Strong curiosity / click appeal.
Clear royal/person/topic identification.
Natural readability.
Match with the channel's dramatic news style.
Search usefulness.
The recommended title should feel strong even to somebody who sees only
the thumbnail and title together.
DESCRIPTION GENERATION
Write one upload-ready YouTube description.
Structure:
Paragraph 1:
1-2 short sentences.
Immediately state who/what the video is about.
Naturally include the main royal/person/topic keywords.
Make the opening interesting because this text may appear in YouTube
search/browse previews.
Paragraph 2:
2-4 sentences explaining the central conflict, revelation, timeline,
allegation, email, interview, dispute, or development covered.
Create curiosity about what viewers will see in the video.
Do NOT reveal every major payoff from the video.
Paragraph 3:
Optional short contextual sentence when needed.
Do not add unrelated background merely to make the description longer.
Description rules:
Target approximately 80-180 words.
Write naturally, not like an SEO keyword list.
Use the same facts and framing supported by the SRT/EDL.
Never invent names, dates, quotes, filings, relationships, legal
outcomes, or events.
Clearly preserve uncertainty when the video itself discusses allegations,
rumors, claims, reports, or speculation.
Do not add URLs unless they were explicitly provided by the user.
Do not add a subscribe CTA here because the current video skill reserves
the subscribe CTA for the closing narration.
HASHTAGS
Generate 3 relevant hashtags maximum.
Format:
HASHTAGS:
#RoyalFamily #PrinceHarry #RoyalNews
Rules:
Use only hashtags directly relevant to this specific story.
Prefer person/topic-specific hashtags over generic viral hashtags.
Do not use #viral, #fyp, #trending, etc. unless explicitly required.
Do not stuff hashtags into the description body.
TAGS
Generate 10-20 comma-separated YouTube tags.
Format:
TAGS:
royal family, royal news, prince harry, meghan markle, ...
Tag rules:
Include important names and topic variations.
Include common full-name and short-name variations where relevant.
Include the central event/topic.
Include a small number of broad niche terms such as:
royal family, royal news, british royal family.
Do not add unrelated trending celebrities/topics.
Do not repeat the exact same keyword unnecessarily.
Tags are secondary metadata; title quality and description quality
take priority.
METADATA OUTPUT FORMAT
Write output/NN_metadata.txt exactly in this structure:
VIDEO: NN_edited.mp4
RECOMMENDED TITLE:
...
ALT TITLE 1:
...
ALT TITLE 2:
...
DESCRIPTION:
...
HASHTAGS:
...
TAGS:
...
FAILURE BEHAVIOUR
Metadata generation failure must NOT invalidate an otherwise successfully
rendered video.
If metadata generation fails:
Keep output/NN_edited.mp4.
Append the error to output/errors.log.
Mark the video render itself as successful.
Continue processing the batch.

====================================================================
STEP 11 — BATCH BEHAVIOUR
====================================================================
- Process strictly ascending by number.
- Never ask questions. Never wait for confirmation.
- After each video: append one line to output/progress.log:
  `NN | OK | duration_in=Xm -> out=Ym | hook=Hs-He | narr_blocks=k`
- On failure: log to output/errors.log with reason, continue to next.
- After batch ends: print summary table of done/failed.

====================================================================
HARD DON'Ts
====================================================================
- NO burned-in captions. NO LUTs. NO color grading.
- NO logo/watermark removal anywhere.
- NO modification of member clips beyond temporal trimming, fit-to-canvas, and
  the explicit image Ken Burns exception described above.
- NO subscribe CTA except in closing narration.
- NO background rotation inside a video; use exactly ONE background/grid for the
  full final video.
- NO questions to the user mid-batch.
- NO reordering of story chronology (hook copy excepted).
- NO mid-sentence, mid-clause, mid-question, or unfinished-thought cuts.
- NO after-hook/mid narration longer than 40 seconds.
- Closing narration MUST be approximately 120-150 seconds (2:00-2:30).
- NO hook longer than 30 seconds; never force a 30-second cut through a sentence.
- NO main video block 180 seconds or longer.
- NO unrelated B-roll and NO nearest-member substitution when a named member is discussed.
- NO repeated B-roll file anywhere within the same final video.
- NO freeze-frame padding, cloned last frames, or stretched video frames.
- NO HyperFrames/browser/HTML composition pipeline for normal video editing/rendering.
- NO final render above or below 1280x720; output is fixed 16:9 720p.
- NO final render at 60fps or source-matched fps; output is always fixed 30fps CFR.
- NO deliberate loudness jump between main source and narration; both target -16 LUFS.
- NO visual overlays of any kind (noise, grain, dust, film texture, light leaks, particles, or effect videos).
