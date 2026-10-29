# Google Docs TTS

A command-line utility for generating OGG/Opus or MP3 audio from `.txt` and `.md` files by automating Google Docs' built-in Text-to-Speech (TTS) feature. Output defaults to OGG/Opus.

This tool uses a hybrid approach: inserting text quickly and reliably via the **Google Docs REST API**, and then driving a **Playwright browser instance** (via a local persistent context) to trigger the Google Docs TTS player and download the resulting audio blobs.

---

## 🛠 How It Works

```
┌─────────────────┐      1. Writes text       ┌──────────────────┐
│   Python CLI    │ ────────────────────────> │ Google Docs API  │
└─────────────────┘                           └──────────────────┘
         │                                              │
         │ 2. Launches / connects                       │ 3. Syncs doc
         ▼                                              ▼
┌─────────────────┐      4. Clicks TTS        ┌──────────────────┐
│   Playwright    │ ────────────────────────> │  Google Doc Tab  │
└─────────────────┘                           └──────────────────┘
         │                                              │
         │ 6. Saves MP3                                 │ 5. Plays Audio
         ▼                                              ▼
┌─────────────────┐     <───────────────────  ┌──────────────────┐
│   Output file   │      Downloads audio      │  Audio Player    │
└─────────────────┘                           └──────────────────┘
```

1. **Insert Text**: The CLI uses Google Docs REST API with service account credentials to clear and write text to a temporary Google Document. This avoids slow, flaky virtual keystrokes in the browser.
2. **Launch Browser**: Playwright opens the Google Doc.
3. **Trigger TTS**: Playwright clicks the Google Docs Text-to-Speech toolbar button.
4. **Capture & Download**: The script waits for the audio player to load and downloads its audio `blob:` URL as an MP3 through Playwright.
5. **Assemble Output**: If the input text exceeds the maximum chunk size (20,000 characters), it is split into chunks and processed sequentially. `ffmpeg` combines multiple chunks and converts `.ogg` output to mono, 48 kHz, 64 kbps Opus.

---

## ✨ Features

- ⚡ **REST API Integration**: Direct document manipulation via Google Docs API.
- 🧩 **Smart Text Chunking**: Automatically splits long texts (default: 20,000 characters) into separate parts.
- 🛠 **Sentence Retry**: If Google Docs reports unsuccessful audio generation, splits the first sentence longer than 280 characters at word boundaries and retries.
- 🔄 **Resume Support**: Automatically skips files or chunks that have already been generated on disk, allowing you to resume interrupted jobs.
- 🎛 **Flexible Modes**: Run headlessly for background scripts, or headed to troubleshoot.

---

## 📋 Prerequisites

1. **Python**: Version `3.10` or higher.
2. **uv**: Fast Python package installer and resolver (recommended).
3. **ffmpeg**: Required for the default OGG/Opus output and for concatenating multi-chunk files. Ensure `ffmpeg` is available in your system `PATH`.

---

## 🚀 Setup & Installation

1. Navigate to the project directory:
   ```bash
   cd /home/firoz/Desktop/google-docs-tts
   ```

2. Sync the project dependencies:
   ```bash
   uv sync
   ```

3. Install Playwright browser binaries:
   ```bash
   uv run playwright install chromium
   ```

---

## 🔑 Credentials & Session Setup

The tool requires two levels of authentication:
1. A **Service Account** to write text to the document via the REST API.
2. A **Google Account Session** in the Playwright browser to access the Google Docs page.

### Step 1: Google Docs REST API Credentials
1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project and enable the **Google Docs API**.
3. Go to **APIs & Services > Credentials** and create a **Service Account**.
4. Generate a **JSON Key** for the Service Account and download it. Set `CONFIG['google_credentials_json']` in `tts.py` to its absolute path. Keep the key outside the repository; the current local configuration points to `/Users/firozahmed/Documents/gen-lang-client-0282610083-ed211e26d114.json`.
5. Copy the Service Account's email address (e.g. `your-service-account@your-project.iam.gserviceaccount.com`).
6. Share your target Google Document with this email address, granting **Editor** permissions.

### Step 2: Browser Session Login
To save your Google account login session to the persistent browser profile:
```bash
uv run tts.py --login
```
This launches a visible Chromium browser window. Log in to your Google Account (the one that has access to the target Google Doc) and resolve any 2FA prompts. Once the document editor is successfully loaded, return to the terminal and press **Enter** to save the session and close the browser.

---

## 📖 Usage

### Standard Command

```bash
# Convert a single file (outputs to path/to/input.ogg by default)
uv run tts.py path/to/input.txt

# Save MP3 without Opus conversion
uv run tts.py path/to/input.txt path/to/output.mp3

# Convert all .txt and .md files in a directory to OGG/Opus
uv run tts.py path/to/input-dir path/to/output-dir

# Convert multiple files to an output directory
uv run tts.py path/to/first.txt path/to/second.md path/to/output-dir
```

With two file paths and no output directory, the second path is treated as the explicit output file. Use `.ogg` for Opus output; `.opus` is not an encoding selector.

### Command Line Flags
- `--opus`: Accepted for compatibility; OGG/Opus is already the default.
- `--headless`: Run Playwright in headless mode.
- `--no-headless`: Run Playwright in visible (headed) mode using the saved profile session. Use this to watch the automation run in a visible browser.
- `--debug`: Enable diagnostic screenshots in a `debug/` folder in the output directory (enabled by default). Successful-operation screenshots additionally require `save_success_screenshots`.

Conversion or concatenation failures exit with an error and preserve the source MP3 chunks for retry.

---

## ⚙ Configuration

You can customize the script settings by editing the `CONFIG` dictionary in [`tts.py`](./tts.py):

| Config Key | Default Value | Description |
| :--- | :--- | :--- |
| `doc_url` | `'https://docs.google.com/.../edit'` | The URL of the Google Doc used for TTS processing. |
| `max_chunk_length`| `20_000` | Maximum character length for each audio chunk. |
| `timeout` | `120_000` | Playwright operations timeout in milliseconds. |
| `profile_dir` | `~/.firofame-profile` | Directory where the Playwright browser profile/session is stored. |
| `google_credentials_json` | `'/Users/firozahmed/Documents/gen-lang-client-0282610083-ed211e26d114.json'` | Path to the service account credentials JSON file. |
| `save_success_screenshots` | `False` | Save screenshots on successful text insertion and audio generation. |
| `debug` | `True` | Whether to print verbose debug output. |
| `headless` | `False` | Run the browser in headless mode by default. Login always uses a visible browser. |

## Development

[`tts.py`](./tts.py) keeps browser setup and batch orchestration in `main()`, per-file processing and resume logic in `process_file()`, and shared ffmpeg execution in `run_ffmpeg()`.

Run the regression tests without opening a browser or accessing Google Docs:

```bash
uv run python -m unittest -v
```
