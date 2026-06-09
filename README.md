# Google Docs TTS

A powerful command-line utility for generating high-quality MP3 audio from `.txt` and `.md` files by automating Google Docs' built-in Text-to-Speech (TTS) feature. 

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
4. **Capture & Download**: The script waits for the audio player to load, grabs the generated audio `blob:` URL, fetches it directly inside the browser context, and writes the base64-decoded bytes to disk as an `.mp3` file.
5. **Auto-Concatenation**: If the input text exceeds the maximum chunk size (20,000 characters), it is split into chunks, processed sequentially, and combined using `ffmpeg`.

---

## ✨ Features

- ⚡ **REST API Integration**: Direct document manipulation via Google Docs API.
- 🧩 **Smart Text Chunking**: Automatically splits long texts (default: 20,000 characters) into separate parts.
- 🛠 **Punctuation Workaround**: Replaces/appends punctuation on the first line of each chunk to prevent Google Docs TTS from failing to speak the opening sentence.
- 🔄 **Resume Support**: Automatically skips files or chunks that have already been generated on disk, allowing you to resume interrupted jobs.
- 🗣 **Clean Spoken Titles**: Automatically cleans up chapter/file names (removing numbers like `01_` or `01 - `) and prepends the title to the speech output. Supports Malayalalm part formatting (e.g. `ഭാഗം 1`).
- 🎛 **Flexible Modes**: Run headlessly for background scripts, or headed to troubleshoot.

---

## 📋 Prerequisites

1. **Python**: Version `3.10` or higher.
2. **uv**: Fast Python package installer and resolver (recommended).
3. **ffmpeg**: Required if you want multi-chunk files concatenated into a single output MP3. Ensure `ffmpeg` is available in your system `PATH`.

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

### Step 1: Google Docs REST API Credentials (`credentials.json`)
1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project and enable the **Google Docs API**.
3. Go to **APIs & Services > Credentials** and create a **Service Account**.
4. Generate a **JSON Key** for the Service Account, download it, rename it to `credentials.json`, and place it in the root of this project.
5. Copy the Service Account's email address (e.g. `your-service-account@your-project.iam.gserviceaccount.com`).
6. Share your target Google Document with this email address, granting **Editor** permissions.

### Step 2: Browser Session Login
To save your Google account login session to the persistent browser profile:
```bash
uv run main.py --login
```
This launches a visible Chromium browser window. Log in to your Google Account (the one that has access to the target Google Doc) and resolve any 2FA prompts. Once the document editor is successfully loaded, return to the terminal and press **Enter** to save the session and close the browser.

---

## 📖 Usage

### Standard Command

```bash
# Convert a single file (outputs to path/to/input.mp3)
uv run main.py path/to/input.txt

# Convert a single file and specify a custom output path
uv run main.py path/to/input.txt path/to/output.mp3

# Convert all .txt and .md files in a directory to an output directory
uv run main.py path/to/input-dir path/to/output-dir
```

### Command Line Flags
- `--headless`: Run Playwright in headless mode (default for file jobs).
- `--no-headless`: Run Playwright in visible (headed) mode using the saved profile session. Use this to watch the automation run in a visible browser.
- `--debug`: Prints detailed logs and saves screenshots to a `debug/` folder in the output directory if chunks fail or insert successfully.

---

## ⚙ Configuration

You can customize the script settings by editing the `CONFIG` dictionary in [`main.py`](file:///home/firoz/Desktop/google-docs-tts/main.py):

| Config Key | Default Value | Description |
| :--- | :--- | :--- |
| `doc_url` | `'https://docs.google.com/.../edit'` | The URL of the Google Doc used for TTS processing. |
| `max_chunk_length`| `20_000` | Maximum character length for each audio chunk. |
| `timeout` | `120_000` | Playwright operations timeout in milliseconds. |
| `profile_dir` | `~/.google-docs-tts-profile` | Directory where the Playwright browser profile/session is stored. |
| `google_credentials_json` | `'credentials.json'` | Path to the service account credentials JSON file. |
| `save_success_screenshots` | `False` | Save screenshots on successful text insertion and audio generation. |
| `debug` | `True` | Whether to print verbose debug output. |
| `headless` | `True` | Run the browser in headless mode by default. |

