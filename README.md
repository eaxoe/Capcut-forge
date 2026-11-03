# capcut-forge

Tell Claude "edit my CapCut project Holiday" and the edit appears in CapCut.

capcut-forge finds the footage you imported into a CapCut project (or a
folder you point it at) and edits it: pauses, filler words and false starts
cut, word-timed captions, punch-in zooms, b-roll cutaways, music, colour
grade, or a montage cut on the beat. The result is written straight into the
CapCut project, so you keep editing by hand from there. It can also render a
finished MP4 with ffmpeg, no CapCut needed.

Everything runs on your own computer, macOS or Windows. Speech recognition
is local (Whisper); nothing is uploaded.

## Requirements

- macOS or Windows 10/11.
- [CapCut](https://www.capcut.com/) desktop app (the international edition),
  opened at least once. Tested with CapCut 9.3 and 9.5 for Mac. Windows
  support passes the automated tests on Windows but hasn't been tried
  against a real CapCut for Windows install yet. If something's off,
  please open an issue.
- ffmpeg 7 or newer, uv and git:

  **macOS** (with [Homebrew](https://brew.sh)):

  ```bash
  brew install ffmpeg uv git
  ```

  **Windows** (PowerShell; then open a new PowerShell window so the tools
  are on your PATH):

  ```powershell
  winget install Gyan.FFmpeg astral-sh.uv Git.Git
  ```

uv installs the right Python by itself; you don't need to set one up.

## Install

Same on both systems (Terminal on macOS, PowerShell on Windows):

```bash
git clone https://github.com/eaxoe/capcut-forge.git
cd capcut-forge
uv sync --extra mcp --extra transcribe
uv run capcut-forge doctor      # checks ffmpeg, CapCut, your draft folder
uv run capcut-forge capcut ls   # lists your CapCut projects
```

If `capcut ls` lists your projects, it's working. It finds CapCut's project
folder by itself (`~/Movies/CapCut/User Data/Projects/com.lveditor.draft` on
macOS, `%LOCALAPPDATA%\CapCut\User Data\Projects\com.lveditor.draft` on
Windows), including a location you changed in CapCut's settings.

Optional on macOS: `--extra live` drives the open CapCut window (see
[Live lane](#live-lane-capcut-open-macos-only)).

## Connect it to Claude

### Claude Code

Run this from inside the `capcut-forge` folder (Terminal or
PowerShell). It makes the tools available in every Claude Code session:

```bash
claude mcp add --scope user capcut-forge -- uv run --directory "$PWD" --extra mcp --extra transcribe capcut-forge-mcp
```

Start a new session (or run `/mcp`) and check `capcut-forge` is
connected. To use it in this folder only, skip the command above and copy
`.mcp.json.example` to `.mcp.json` instead.

### Claude desktop app (chat)

Open Settings → Developer → Edit Config, add the block below to
`claude_desktop_config.json`, and restart the Claude app. Desktop apps don't
see your terminal's PATH, so use uv's full path.

**macOS** (`which uv` prints uv's path):

```json
{
  "mcpServers": {
    "capcut-forge": {
      "command": "/opt/homebrew/bin/uv",
      "args": ["run", "--directory", "/Users/YOU/capcut-forge",
               "--extra", "mcp", "--extra", "transcribe", "capcut-forge-mcp"]
    }
  }
}
```

**Windows** (`(Get-Command uv).Source` prints uv's path; in JSON every `\`
is written `\\`):

```json
{
  "mcpServers": {
    "capcut-forge": {
      "command": "C:\\Users\\YOU\\AppData\\Local\\Microsoft\\WinGet\\Links\\uv.exe",
      "args": ["run", "--directory", "C:\\Users\\YOU\\capcut-forge",
               "--extra", "mcp", "--extra", "transcribe", "capcut-forge-mcp"]
    }
  }
}
```

### Then just ask

1. In CapCut, create a project and import your clips (and a song, if you
   want one) into its Media panel. You don't need to put anything on the
   timeline.
2. Ask Claude: *"edit my CapCut project Holiday"*, or *"make a video from the
   clips in my Downloads/trip folder"*.
3. Claude asks before closing CapCut (it must be closed while a project is
   written; CapCut saves on a normal quit and is reopened afterwards).
4. Open the project in CapCut. Something you don't like? *"undo that"* rolls
   it back from the backup.

### First run

- The first edit with someone talking downloads the Whisper speech model
  (about 500 MB, once).
- macOS may ask whether Claude (or your terminal) can access your Downloads,
  Desktop or Documents folder. Allow it, or the footage can't be read.

## Update and uninstall

Update (then run `/mcp` in Claude to reconnect):

```bash
git pull
uv sync --extra mcp --extra transcribe
```

Uninstall: disconnect it from Claude, then delete the `capcut-forge`
folder and its backups and caches (`~/Library/Application
Support/capcut-forge` on macOS, `%APPDATA%\capcut-forge` on Windows).

```bash
claude mcp remove --scope user capcut-forge
```

## Edit a CapCut project in one step

Make a project in CapCut and import your footage (and music, if you have any)
into its Media panel. Then name the project:

```bash
uv run capcut-forge auto "TestY"                 # loose names work: "testy", "test y"
uv run capcut-forge auto ~/Downloads/trip        # a folder -> a new project called "trip"
uv run capcut-forge auto "TestY" --folder ~/Downloads/more-clips   # project + extra footage
```

Or ask Claude: *"edit my CapCut project TestY"*.

capcut-forge looks at the footage and picks the edit:

| footage | edit |
|---|---|
| someone talking (Whisper hears steady speech) | pauses, "um"/"uh" and false starts cut, word-timed captions, punch-in on every other cut, b-roll cutaways from the other clips, music low underneath, `clean-bright` grade |
| clips / photos + music with a beat | montage cut on the beat, camera moves, one grade with per-shot matching, fades, clips muted under the music |
| clips / photos, no music (or music without a beat) | montage on an even pace over the music, or over an original ambient bed |

The canvas follows the footage (9:16 for a portrait speaker, 16:9 for
landscape b-roll), phone rotation included. Montages default to 60 seconds;
talking edits keep everything. Override with `--style talking|montage`,
`--aspect 9:16`, `--max-seconds 30`, `--music song.mp3`, `--look warm-film`,
`--captions punch`, `--hook "watch this"`, `--title "Day one"`.

The edit is written **into that project** (same project, same Media panel),
after a backup. A project whose timeline already has clips is only rebuilt
with `--replace`, and `capcut-forge capcut restore <project>` undoes it. A
folder with no project of that name becomes a new project. The plan behind
each edit is saved to the `plans/` folder of capcut-forge's data folder
(`~/Library/Application Support/capcut-forge` on macOS,
`%APPDATA%\capcut-forge` on Windows) so you can tweak and re-apply it with
`capcut build --into-existing`.

Transcripts are cached (keyed by file version), so editing the same footage
again never waits on Whisper twice.

## The one-liner (from a raw file)

```bash
uv run capcut-forge make raw.mp4 --transcribe --drop-retakes --punch-in 1.12 \
  --hook "nobody tells you this" --music bed.mp3 \
  --render out/final.mp4 --capcut my-video --quit-app
```

That transcribes locally, cuts pauses, filler words ("um", "uh") and false
starts, zooms in on every other cut, captions every word in time with the
speech, adds a hook card and a music bed that loops and fades, then renders
an MP4 **and** builds a CapCut draft of the same edit.

## Step by step

Each stage writes a file you can read and edit before the next stage:

```bash
uv run capcut-forge transcribe raw.mp4                   # -> raw.transcript.json
uv run capcut-forge plan raw.mp4 --transcript raw.transcript.json \
    --style tiktok-raw --hook "watch this" -o edit.plan.json
# edit edit.plan.json by hand if you like: clips, captions, overlays, audio
uv run capcut-forge render edit.plan.json --until 15     # quick preview
uv run capcut-forge render edit.plan.json -o final.mp4
uv run capcut-forge capcut build edit.plan.json --name my-video
uv run capcut-forge srt edit.plan.json                   # captions for YouTube
```

With no transcript, cutting falls back to silence detection (`--cut silence`).
To reuse an old kit `cuts.json`, pass `--cuts path/to/cuts.json`.

## The edit plan

`plan.json` holds a whole edit as one backend-neutral file. Times are seconds;
clip times are on the source file, everything else on the output timeline.

```json
{
  "width": 1080, "height": 1920, "fps": 30,
  "clips":    [{"source": "raw.mp4", "start": 3.2, "end": 7.9, "zoom": 1.0}],
  "captions": [{"text": "this changes everything", "start": 0.0, "end": 1.4,
                "style": "tiktok-raw", "role": "caption"}],
  "overlays": [{"source": "stat.mov", "at": 4.0, "layer": 1, "x": 0, "y": 0.4, "scale": 0.6}],
  "audio":    [{"source": "bed.mp3", "volume": 0.15, "fade_in": 1.5, "fade_out": 1.5, "loop": true}]
}
```

Caption styles: `tiktok-raw` (white with a black outline, low on the frame),
`hook` (black on a white card, top), `explainer` (white on a black box,
centred) and `punch` (one big word at a time). They're defined in
`capcut_forge/styles.py`.

## CapCut drafts (file lane)

```bash
uv run capcut-forge capcut ls
uv run capcut-forge capcut inspect my-video
uv run capcut-forge capcut add-text my-video "SUBSCRIBE" --at 12 --dur 2 --style hook
uv run capcut-forge capcut add-overlay my-video graphic.mov --at 4.0 --layer 2
uv run capcut-forge capcut add-audio my-video whoosh.wav --at 3.9 --volume 0.6
uv run capcut-forge capcut add-captions my-video edit.plan.json
uv run capcut-forge capcut transform my-video --track overlay --index 0 --scale 0.5 --y 0.4
uv run capcut-forge capcut keyframe my-video --track main --index 2 --at 8.0 --scale 1.0
uv run capcut-forge capcut keyframe my-video --track main --index 2 --at 9.5 --scale 1.25
uv run capcut-forge capcut remove my-video --track text --index 3
uv run capcut-forge capcut backups my-video
uv run capcut-forge capcut restore my-video          # undo the last capcut-forge write
```

Safety rules built in:

- **It never quits CapCut on its own.** A write fails while CapCut is open,
  unless you pass `--quit-app`. CapCut saves on a normal quit, then capcut-forge
  writes. This matters because CapCut rewrites its registry on quit, which
  would clobber a write made while it was open.
- **Every write is backed up first** to `backups/<draft>/` in
  capcut-forge's data folder (`~/Library/Application Support/capcut-forge`
  on macOS, `%APPDATA%\capcut-forge` on Windows), outside CapCut's folder.
  `restore` rolls back.
- **`--dry-run`** shows the before and after tracks and writes nothing.
- **Writes are atomic** (temp file, then rename) and structure-checked. A
  build that fails halfway deletes its partial folder.
- **It works on top of your hand edits.** It loads the draft as CapCut last
  saved it and adds to it.
- **Media and fonts are hardlinked into the draft's `Resources/`.** CapCut's
  sandbox can always read that folder.

## Live lane (CapCut open, macOS only)

Drives the open CapCut window through macOS accessibility, so it doesn't
exist on Windows (everything else does). Needs `--extra live` and
Accessibility permission for your terminal.

```bash
uv run capcut-forge live open my-video
uv run capcut-forge live seek 12.5          # frame-exact
uv run capcut-forge live split 20.0
uv run capcut-forge live export --to ~/Movies/exports
uv run capcut-forge live shot qa.png
uv run capcut-forge live dump Player        # find automation ids after a CapCut update
```

`export` never touches CapCut's "sync to space" cloud upload. It dismisses the
share screen with Escape instead of clicking through it, because clicking
through publishes the video.

The live lane clicks on CapCut's internal test IDs, which change when CapCut
updates. `export` also clicks at a measured position. Check both with `shot`
and `dump` after updating CapCut.

## Claude / MCP tools

Setup is under [Connect it to Claude](#connect-it-to-claude). The connector
exposes tools to Claude. The main one is `capcut_auto_edit` (project
name and/or folder in, edit written into CapCut out). Also `capcut_projects`
(every project, with its media counts), `capcut_project_media`,
`capcut_inspect`, `capcut_add`, `capcut_grade`, `capcut_backups`,
`capcut_restore`, `make_plan`, `render_plan` and others. Project names match
loosely everywhere.

`capcut_auto_edit` writes directly (after a backup) and refuses to replace a
timeline that already has clips unless `replace=true`. The lower-level write
tools default to a dry run and only write with `apply=true`. No tool quits
CapCut unless `quit_app=true`, touches the network, or spends money. Errors
come back as plain sentences ("CapCut is open…", "no CapCut draft called…")
so Claude can tell you what to do.

After changing capcut-forge's code, restart the MCP server (reconnect it in
Claude, `/mcp`) so Claude picks up the new version.

## Tests

```bash
uv run --group dev pytest
```

The tests build real drafts in a temporary folder, using a fake CapCut that
never touches your real app. They also render real MP4s. GitHub Actions runs
them on macOS and Windows on every push (`.github/workflows/tests.yml`).

## Calibration notes

- CapCut's text sizes (`capcut_size` in `styles.py`) are approximate. Build
  one draft, look at it, and adjust once.
- Every time is snapped to the frame grid the way CapCut 9.3 does it: rounded
  up to the next frame, then down to the microsecond. A draft built from the
  self-test plan matched CapCut's own re-save exactly. Without the snapping,
  CapCut shifts clips by up to a frame when it opens the draft.
- CapCut must be closed only when writing to its real draft folder. A
  `--draft-root` pointing anywhere else ignores whether the app is open.
- CapCut 9.5 re-saves paths inside a draft as
  `##_draftpath_placeholder_<id>_##/Resources/...` (timeline) and
  `./Resources/...` (Media panel). capcut-forge expands both.
- The draft folder is read from CapCut's own setting
  (`currentCustomDraftPath` in `User Data/Config/globalSetting`), so a moved
  draft location is followed.
- Drafts with more than one timeline (CapCut 9's multi-timeline projects) are
  refused for writes: the edit path would drop the extra timelines.
- Transcription feeds Whisper audio decoded by ffmpeg, not a file path:
  faster-whisper 1.2's own decoder breaks on PyAV 19.
- CapCut for Windows names a draft's timeline file `draft_content.json`
  (macOS: `draft_info.json`); some versions keep both. capcut-forge reads
  whichever exists, keeps both in step when there are two, and gives new
  drafts the platform's own name. JianYing (the Chinese edition) encrypts
  that file; such drafts are reported, not edited.
- On Windows, CapCut is closed with a normal close request (`taskkill`
  without `/F`), so it saves first, just like a quit on macOS.
- All files are read and written as UTF-8, so captions and project names in
  any language survive Windows' legacy default encoding.
- Track roles are identified by type: main and overlay video (`flag` 2),
  text, and audio. The old kit looked text tracks up by `flag == 1`, but
  CapCut writes text tracks with flag 0.
