<p align="center">
  <img src="assets/header.svg" alt="Vid2Cards — YouTube playlists to Anki flashcards" width="100%">
</p>

# Vid2Cards

Turns the videos in a YouTube playlist into Anki flashcards plus a daily
quiz, using only local models (no paid API): **faster-whisper** on the GPU
for transcription and **Ollama** for generating the cards.

Runs once a day via a systemd timer, processes only new videos, and
delivers the cards straight into Anki via **AnkiConnect** (syncing with
AnkiWeb, so they show up on your phone) — while always leaving a backup
`.apkg` in `output/`.

## Requirements

- Linux with an NVIDIA GPU (tested on an RTX 3050 4GB) — proprietary driver
  + CUDA 12 + cuDNN 9. Without a GPU, it automatically falls back to Whisper
  on CPU (slower).
- `ffmpeg`, `yt-dlp`, `ollama`, Python 3.11+.
- Anki Desktop + the AnkiConnect add-on, if you want automatic delivery
  (optional — without it, the pipeline just generates the `.apkg` in
  `output/`).

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Pull whichever Ollama models you plan to use (configurable in `config.toml`):

```bash
ollama pull qwen3:14b        # default: dense, more cards per chunk, slower
ollama pull qwen3:30b-a3b    # MoE alternative: ~3x faster, fewer cards
```

## Configuration

Edit `config.toml`:

- `playlists.urls`: list of YouTube playlists to monitor. For a private
  playlist, set `cookies_file` (exported from your browser).
- `limits.videos_per_run`: how many videos to process per day (default 5 —
  on modest hardware, card generation alone can take tens of minutes per
  video; adjust to your hardware).
- `llm.model`: the Ollama model used for generation. `llm.alternative_model`
  is only used by the `bench` command.
- `ankiconnect.enabled`: if `true`, tries to send cards straight to Anki via
  AnkiConnect (in addition to the `.apkg`). If AnkiConnect doesn't respond,
  the pipeline doesn't fail — it just produces the `.apkg` as usual.

## Commands

```bash
vid2cards run                      # syncs playlists + processes + exports (what the timer runs)
vid2cards sync                     # only lists the playlists and registers new videos in the database
vid2cards add <url>                # processes a single video, outside of any playlist
vid2cards status                   # table with the state of each video
vid2cards retry [--id ID]          # moves video(s) in an error state back into the queue
vid2cards regen --id ID            # regenerates the cards for a video from its saved transcript
vid2cards export --all             # re-exports all cards already generated (apkg + AnkiConnect)
vid2cards bench --test-file <txt>  # compares the two configured models on a sample transcript
```

## Importing into Anki manually

If you haven't set up AnkiConnect, double-click the most recent `.apkg` in
`output/` (or drag it into Anki Desktop). Decks come in as
`Vid2Cards::<playlist>::<video>`; reimporting never duplicates cards (the
GUID is stable per video+question).

## Pipeline — how it works internally

A daily run (`vid2cards run`) goes through sequential phases (never in
parallel, since a 4GB GPU can't hold Whisper and the LLM at the same time):

1. **Sync**: lists the playlists, registers new videos in SQLite
   (`data/vid2cards.db`), identified by `video_id` (unique key).
2. **Transcription**: downloads only the audio, transcribes it with
   `faster-whisper` on the GPU (`vad_filter=True`, with an automatic
   fallback without VAD if the result comes back empty — music/singing
   sometimes fools the voice detector), deletes the audio, saves the
   transcript to `data/transcripts/<id>.json`, frees the GPU.
3. **Generation**: splits the transcript into ~20,000-character chunks
   (cutting at sentence boundaries), sends each chunk to Ollama with
   structured output (JSON Schema + Pydantic validation, up to 3 attempts),
   removes duplicate cards across chunks (rapidfuzz), saves to
   `data/cards/<id>.json`. Each card's timestamp isn't just the chunk's
   start (which can span 15-20 minutes of video) — `chunking.find_time`
   compares the card's `concept` against that chunk's Whisper segments
   (fuzzy match) and uses the start of the closest match, so the source
   link jumps straight to the exact moment that concept was explained.
4. **Export**: builds the day's `.apkg` (only with what was processed in
   this run) plus the quiz in Markdown, and pushes the cards via
   AnkiConnect if enabled.

Every stage is a checkpoint: if a run is interrupted, the next one picks up
where it left off (it never re-transcribes a video that already has a
saved transcript, never reprocesses a video already exported). A file lock
(`data/vid2cards.lock`) prevents two simultaneous runs.

## Daily timer (systemd)

```bash
sudo cp deploy/vid2cards.service deploy/vid2cards.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now vid2cards.timer
systemctl list-timers vid2cards.timer
```

The schedule is set in `deploy/vid2cards.timer` (`OnCalendar`) — it's in UTC
by default, so adjust it to your timezone if you want a specific local
time. `Persistent=true`: if the machine is off at that time, it runs as
soon as it's back on.

## AnkiConnect (automatic delivery to your phone)

To get updated decks straight into Anki on your phone, Anki Desktop runs
headless on the server (via Xvfb) with the AnkiConnect add-on, syncing to
an AnkiWeb account — the same app on your phone, logged into that account,
pulls the updates.

```bash
sudo cp deploy/anki-headless.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now anki-headless.service
```

**Initial login (one time only):** AnkiConnect doesn't log in by itself —
you need to open the headless Anki screen once to authenticate with
AnkiWeb. Easiest via a temporary VNC session:

```bash
sudo apt install -y x11vnc
# find the Xvfb display: ps aux | grep Xvfb
x11vnc -display :<N> -auth <path-to-the-process-Xauthority> \
       -passwd <temporary-password> -listen localhost -rfbport 5900 -forever &
```

Then tunnel over SSH (`ssh -L 5900:localhost:5900 user@server`) and connect
with any VNC client (on macOS, `Cmd+K` → `vnc://localhost:5900` in Finder is
enough). Inside Anki: **Sync** button → log into AnkiWeb → **Upload** the
first time. After that, `x11vnc` can be shut down — the session stays saved
in the profile, and every sync after that happens automatically (driven by
the pipeline, via the API).

If a dialog ever shows up asking you to choose between "Upload" or
"Download" (meaning the server and AnkiWeb have diverged — for example,
after manually touching one side or the other), pick the side you know is
correct.

## Directories (outside of git)

```
data/    SQLite database, transcripts, and already-generated cards
output/  daily .apkg files and quizzes
logs/    rotated log files
```

## Troubleshooting

- **`faster-whisper` fails to decode audio** (`TypeError:
  metadata_errors`): an incompatibility between the `av` package's wheel
  and the system `ffmpeg` on a too-new Python. The project already works
  around this by decoding directly via the `ffmpeg` CLI (`transcribe.py`),
  with no dependency on `av` for that.
- **Transcription comes back empty** (0 segments) for videos with
  music/singing: the VAD classified everything as "no speech". Already
  handled with an automatic fallback without VAD — if it still happens,
  run `vid2cards regen` after deleting the problematic transcript JSON.
- **Cloze cards silently dropped**: check that the prompt in `generate.py`
  uses `{{{{c1::...}}}}` (4 braces) — it's a string passed through
  `.format()`, which requires the doubled, escaped braces to actually
  produce `{{c1::...}}` in the final text.
- **Headless Anki won't start** (`AttributeError: 'NoneType' object has no
  attribute 'replace'`): pass `-l en` explicitly in the systemd
  `ExecStart` — it's a locale auto-detection bug in that environment.
