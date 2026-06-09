# Google Docs TTS

Standalone command line tool for generating MP3 audio from `.txt` and `.md`
files using Google Docs Text-to-Speech through Playwright / CDP.

## Setup

```bash
cd /home/firoz/Desktop/google-docs-tts
uv venv
uv pip install -r requirements.txt
```

Install `ffmpeg` separately if you want multi-chunk files concatenated into one
MP3.

## Credentials

The tool expects Google Docs API credentials in `credentials.json` by default
and stores the OAuth token in `google_token.json`. Both files are ignored by
Git.

Supported credential types:

- Google service account JSON
- Google OAuth client JSON

For OAuth credentials, run the login flow once:

```bash
uv run google-docs-tts --login
```

## Usage

```bash
uv run google-docs-tts path/to/input.txt
uv run google-docs-tts path/to/input.txt path/to/output.mp3
uv run google-docs-tts path/to/chapter-dir path/to/output-dir
```

You can also use the compatibility wrapper:

```bash
uv run tts.py path/to/input.txt
```

## Configuration

Edit `src/google_docs_tts/cli.py` to change the Google Doc URL, chunk size,
timeouts, retry behavior, and browser profile directory.
