"""Command line Text-to-Speech converter using Google Docs and Playwright/CDP."""

import os
import sys
import base64
import asyncio
import re
import subprocess
from pathlib import Path
from typing import Any
from dataclasses import dataclass
from playwright.async_api import async_playwright

# Google API Client Imports
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Configuration
CONFIG = {
    'doc_url': 'https://docs.google.com/document/d/1WVxgs-UywesdGppo1zLFR-YA57TQiwEpXDjKoq9EfyM/edit?usp=sharing',
    'max_chunk_length': 20_000,
    'timeout': 120_000,
    'profile_dir': Path.home() / '.google-docs-tts-profile',
    'debug': True,
    'save_success_screenshots': False,
    'headless': True,
    'google_credentials_json': 'credentials.json',
}

SELECTORS = {
    'tts_button': '#textToSpeechToolbarButton',
    'editor': '.kix-appview-editor',
    'player_audio': '.kixAudioPlayerView [data-media-url][data-media-type="audio"]',
    'player_max_time': '.docsUiWizAudioSliderMaxTime',
    'player_close': '.kixAudioPlayerPaletteCloseButton[aria-label="Close"]',
}


def get_doc_id(url: str) -> str:
    """Parse unique Google Document ID from the URL."""
    match = re.search(r'/document/d/([a-zA-Z0-9-_]+)', url)
    if not match:
        raise ValueError(f"Could not parse document ID from URL: {url}")
    return match.group(1)


def get_google_credentials():
    """Retrieve Google Service Account credentials."""
    creds_path = CONFIG['google_credentials_json']
    scopes = ['https://www.googleapis.com/auth/documents']
    
    if not os.path.exists(creds_path):
        raise FileNotFoundError(
            f"Google credentials file not found at '{creds_path}'.\n"
            f"Please obtain a Google Cloud Service Account credentials JSON "
            f"and save it to '{creds_path}'."
        )
        
    try:
        print(f"Using Google Service Account from '{creds_path}'")
        return service_account.Credentials.from_service_account_file(creds_path, scopes=scopes)
    except Exception as e:
        raise RuntimeError(f"Error loading service account credentials from '{creds_path}': {e}")


@dataclass
class FileJob:
    """Represents a text-to-speech job for a single file."""
    input_path: Path
    output_path: Path


@dataclass
class Args:
    """Command line arguments."""
    jobs: list[FileJob]
    login_only: bool = False


def parse_args() -> Args:
    """Parse command line arguments."""
    args = sys.argv[1:]

    if '--debug' in args:
        CONFIG['debug'] = True
        args.remove('--debug')

    if '--headless' in args:
        CONFIG['headless'] = True
        args.remove('--headless')

    if '--no-headless' in args:
        CONFIG['headless'] = False
        args.remove('--no-headless')

    if not args:
        print(
            'Usage: python tts.py [--debug] [--headless] [--no-headless] --login | input_path [output_path|output_dir]\n'
            '       python tts.py [--debug] [--headless] [--no-headless] input_file1 [input_file2 ...] [output_dir]',
            file=sys.stderr,
        )
        sys.exit(1)

    if args[0] == '--login':
        return Args(jobs=[], login_only=True)

    # Resolve paths
    resolved_paths = [Path(a).resolve() for a in args]

    # Check if the last argument should be treated as output dir
    output_dir = None
    if len(resolved_paths) > 1:
        last_path = resolved_paths[-1]
        if last_path.is_dir() or (not last_path.exists() and not last_path.suffix):
            output_dir = last_path
            resolved_paths.pop()

    jobs = []

    # If we have only one path left and it's a directory, expand it
    if len(resolved_paths) == 1 and resolved_paths[0].is_dir():
        input_dir = resolved_paths[0]
        files = sorted([
            p for p in input_dir.iterdir()
            if p.is_file() and p.suffix.lower() in ('.md', '.txt')
        ])
        if not files:
            print(f"No .md or .txt files found in directory: {input_dir}", file=sys.stderr)
            sys.exit(1)
            
        for f in files:
            out_path = (output_dir / f.with_suffix('.mp3').name) if output_dir else f.with_suffix('.mp3')
            jobs.append(FileJob(input_path=f, output_path=out_path))

    elif len(resolved_paths) == 1:
        f = resolved_paths[0]
        if f.is_dir():
            print(f"Error: {f} is a directory but expected files.", file=sys.stderr)
            sys.exit(1)
        out_path = (output_dir / f.with_suffix('.mp3').name) if output_dir else f.with_suffix('.mp3')
        jobs.append(FileJob(input_path=f, output_path=out_path))

    elif len(resolved_paths) == 2 and not output_dir:
        infile = resolved_paths[0]
        outfile = resolved_paths[1]
        jobs.append(FileJob(input_path=infile, output_path=outfile))

    else:
        for f in resolved_paths:
            if f.is_dir():
                print(f"Error: {f} is a directory. Multiple inputs must be files.", file=sys.stderr)
                sys.exit(1)
            out_path = (output_dir / f.with_suffix('.mp3').name) if output_dir else f.with_suffix('.mp3')
            jobs.append(FileJob(input_path=f, output_path=out_path))

    return Args(jobs=jobs)


def split_long_sentence(sentence: str, max_len: int = 280) -> list[str]:
    """Split a sentence into smaller chunks to avoid Google Docs TTS limits."""
    if len(sentence) <= max_len:
        return [sentence]
    
    words = sentence.split(' ')
    parts = []
    current_part = []
    current_len = 0
    
    for word in words:
        word_len = len(word) + (1 if current_len > 0 else 0)
        if current_len + word_len <= max_len - 1:
            current_part.append(word)
            current_len += word_len
        else:
            if current_part:
                part_text = ' '.join(current_part)
                if not part_text.rstrip().endswith('.'):
                    part_text = part_text.rstrip() + '.'
                parts.append(part_text)
            current_part = [word]
            current_len = len(word)
            
    if current_part:
        part_text = ' '.join(current_part)
        parts.append(part_text)
        
    return parts


def process_text_by_splitting_sentences(text: str, max_sentence_len: int = 280) -> str:
    """Process all paragraphs in the text, splitting any extra-long sentences."""
    paragraphs = text.split('\n')
    processed_paragraphs = []
    for para in paragraphs:
        if not para.strip():
            processed_paragraphs.append(para)
            continue
        
        # Split paragraph into sentences using basic sentence-ending punctuation
        raw_sentences = re.split(r'(?<=[.!?])\s+', para)
        new_sentences = []
        for s in raw_sentences:
            s_clean = s.strip()
            if not s_clean:
                continue
            split_s = split_long_sentence(s_clean, max_sentence_len)
            new_sentences.extend(split_s)
        
        processed_paragraphs.append(" ".join(new_sentences))
    return "\n".join(processed_paragraphs)


def dissect_first_long_sentence(text: str, max_len: int = 280) -> tuple[str, bool]:
    """Find and split the first sentence in text that is longer than max_len."""
    paragraphs = text.split('\n')
    for i, para in enumerate(paragraphs):
        if not para.strip():
            continue
        
        # Split paragraph into sentences using basic sentence-ending punctuation
        raw_sentences = re.split(r'(?<=[.!?])\s+', para)
        for j, s in enumerate(raw_sentences):
            s_clean = s.strip()
            if len(s_clean) > max_len:
                split_parts = split_long_sentence(s_clean, max_len)
                if len(split_parts) > 1:
                    raw_sentences[j] = " ".join(split_parts)
                    paragraphs[i] = " ".join(raw_sentences)
                    return "\n".join(paragraphs), True
    return text, False


def split_text(text: str) -> list[str]:
    """Split text into chunks that fit within maxChunkLength."""
    max_len = CONFIG['max_chunk_length']
    chunks = []
    current = ''

    # Step 1: Pre-process lines
    lines = text.split('\n')
    processed_lines = []
    
    for line in lines:
        if len(line) <= max_len:
            processed_lines.append(line)
        else:
            # Settle extremely long lines by splitting on space boundaries
            words = line.split(' ')
            sub_line = ''
            for word in words:
                # If a single word itself exceeds the limit, hard-slice it
                if len(word) > max_len:
                    if sub_line:
                        processed_lines.append(sub_line)
                        sub_line = ''
                    # Break the giant word down into strict character slices
                    for i in range(0, len(word), max_len):
                        processed_lines.append(word[i:i + max_len])
                    continue

                if len(sub_line) + len(word) + 1 > max_len:
                    if sub_line:
                        processed_lines.append(sub_line)
                    sub_line = word
                else:
                    sub_line = f"{sub_line} {word}".strip() if sub_line else word
            if sub_line:
                processed_lines.append(sub_line)

    # Step 2: Group processed lines into final chunks
    for line in processed_lines:
        if current and len(current) + len(line) + 1 > max_len:
            chunks.append(current)
            current = ''
        current += ('\n' if current else '') + line

    if current:
        chunks.append(current)

    return chunks


def suffix_path(file_path: Path, suffix: str) -> Path:
    """Add suffix to filename before extension."""
    return file_path.with_name(f"{file_path.stem}{suffix}{file_path.suffix}")


def get_clean_title(filename_stem: str) -> str:
    """Clean filename stem to get a spoken title."""
    # Remove leading sequence numbers, like '01_' or '01 - ' or '01_ഒന്നാം_...'
    cleaned = re.sub(r'^\d+[\s_\-]+', '', filename_stem)
    # Replace underscores with spaces
    cleaned = cleaned.replace('_', ' ')
    # Collapse multiple spaces
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned




async def click(page: Any, selector: str):
    """Click first matching element."""
    await page.locator(selector).first.click(timeout=CONFIG['timeout'])


async def wait_for_time_display(page: Any):
    """Wait for time display to show valid format."""
    await page.wait_for_function(
        """() => /^\\d{1,2}:\\d{2}(:\\d{2})?$/.test(document.querySelector('.docsUiWizAudioSliderMaxTime')?.textContent?.trim() || '')""",
        timeout=CONFIG['timeout']
    )


async def get_blob_url(page: Any) -> str:
    """Get blob URL from audio player."""
    result = await page.wait_for_function(
        """() => {
            const url = document.querySelector('.kixAudioPlayerView [data-media-url][data-media-type="audio"]')?.getAttribute('data-media-url') || '';
            return url.startsWith('blob:') ? url : null;
        }""",
        timeout=CONFIG['timeout']
    )
    return await result.json_value()


def _ffmpeg_concat_line(path: Path) -> str:
    """Return one safely escaped ffmpeg concat demuxer line."""
    escaped = str(path).replace("\\", "\\\\").replace("'", "\\'")
    return f"file '{escaped}'\n"


def concatenate_audio_chunks(chunk_paths: list[Path], output_path: Path) -> bool:
    """Concatenate MP3 chunks with ffmpeg if available."""
    list_file = output_path.parent / 'ffmpeg_concat_list.txt'

    try:
        with open(list_file, 'w', encoding='utf-8') as f:
            for chunk_path in chunk_paths:
                f.write(_ffmpeg_concat_line(chunk_path))

        result = subprocess.run(
            [
                'ffmpeg',
                '-y',
                '-f',
                'concat',
                '-safe',
                '0',
                '-i',
                str(list_file),
                '-c',
                'copy',
                str(output_path),
            ],
            check=False,
        )
        return result.returncode == 0
    finally:
        if list_file.exists():
            list_file.unlink()


async def save_blob(page: Any, blob_url: str, output_path: Path):
    """Download blob and save to file."""
    base64_data = await page.evaluate("""async (url) => {
        const res = await fetch(url);
        const blob = await res.blob();
        return new Promise((resolve) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result.split(',')[1]);
            reader.readAsDataURL(blob);
        });
    }""", blob_url)

    output_path.write_bytes(base64.b64decode(base64_data))


async def close_player(page: Any):
    """Close audio player if open."""
    try:
        await page.locator(SELECTORS['player_close']).first.click(timeout=3000)
        await asyncio.sleep(0.5)
    except Exception:
        pass  # Already closed


def _update_google_doc_content(creds: Any, doc_id: str, text: str):
    """Synchronous helper that handles the Google Docs API update."""
    service = build('docs', 'v1', credentials=creds)

    # Fetch the document to find the current end index
    doc = service.documents().get(documentId=doc_id).execute()
    content = doc.get('body', {}).get('content', [])
    end_index = content[-1].get('endIndex') if content else 1

    requests = []
    # Clear the entire document if it contains text
    # A blank document has end_index == 2 (contains a single paragraph with '\n')
    if end_index > 2:
        requests.append({
            'deleteContentRange': {
                'range': {
                    'startIndex': 1,
                    'endIndex': end_index - 1
                }
            }
        })

    # Insert the new text at index 1
    requests.append({
        'insertText': {
            'text': text,
            'location': {
                'index': 1
            }
        }
    })

    service.documents().batchUpdate(documentId=doc_id, body={'requests': requests}).execute()


async def insert_text(page: Any, creds: Any, doc_id: str, text: str):
    """Insert text into document editor using the Google Docs REST API.

    This avoids slow, flaky virtual keypress simulations by updating the doc
    directly via the REST API and allowing real-time collaboration to sync it.
    """
    normalized = text.replace('\r\n', '\n')

    # Run blocking API call in a separate thread to keep asyncio loop responsive
    await asyncio.to_thread(_update_google_doc_content, creds, doc_id, normalized)

    # Allow a moment for real-time collaboration to synchronize to browser tab
    await asyncio.sleep(1.5)

    # Focus the editor and scroll/jump cursor to top so TTS is triggered from the beginning
    await click(page, SELECTORS['editor'])
    await asyncio.sleep(0.5)
    # Reset cursor to the start of the document
    if sys.platform == 'darwin':
        # On macOS, Cmd + Up Arrow jumps to the top of the document
        await page.keyboard.press('Meta+ArrowUp')
    else:
        # On Windows/Linux, Ctrl + Home jumps to the top of the document
        await page.keyboard.press('Control+Home')

    await asyncio.sleep(0.5)


async def generate_audio(page: Any) -> str:
    """Generate audio from document text."""
    # First trigger initializes, second generates
    for i in range(2):
        await click(page, SELECTORS['tts_button'])
        
        # Wait for the player to appear or fail early if an error toast appears
        timeout_ms = CONFIG['timeout']
        start_time = asyncio.get_event_loop().time()
        while True:
            if await page.locator(SELECTORS['player_max_time']).is_visible():
                break
            
            # Check for error toast
            error_loc = page.locator("text=Audio generation was unsuccessful")
            if await error_loc.is_visible():
                raise RuntimeError("Google Docs TTS error: Audio generation was unsuccessful")
                
            elapsed_ms = (asyncio.get_event_loop().time() - start_time) * 1000
            if elapsed_ms >= timeout_ms:
                raise asyncio.TimeoutError("Timeout waiting for audio player to appear")
            
            await asyncio.sleep(0.5)

        await wait_for_time_display(page)

        if i == 0:
            await close_player(page)

    return await get_blob_url(page)


async def process_chunk(page: Any, creds: Any, doc_id: str, text: str, output_path: Path) -> str:
    """Process a single text chunk."""
    debug_dir = output_path.parent / 'debug'

    async def debug_screenshot(name: str):
        if not CONFIG['debug']:
            return
        debug_dir.mkdir(exist_ok=True)
        path = debug_dir / f'{output_path.stem}_{name}.png'
        await page.screenshot(path=str(path))
        print(f'  📸 {path}')

    try:
        print(f'Inserting {len(text)} chars...')
        await insert_text(page, creds, doc_id, text)
        if CONFIG.get('save_success_screenshots', False):
            await debug_screenshot('after_insert')

        print('Generating audio...')
        blob_url = await generate_audio(page)
        if CONFIG.get('save_success_screenshots', False):
            await debug_screenshot('after_audio')

        print('Saving...')
        await save_blob(page, blob_url, output_path)
        print(f'✅ {output_path}')

        return blob_url
    except Exception as err:
        await debug_screenshot('error')
        await close_player(page)
        print(f'❌ Chunk failed: {err}')
        raise


async def open_tts_page(context):
    """Open a page in the Playwright browser context."""
    # Use the default persistent page if it exists, otherwise create a new one
    page = context.pages[0] if context.pages else await context.new_page()
    await page.goto(CONFIG['doc_url'], wait_until='domcontentloaded')
    await page.wait_for_selector(SELECTORS['editor'], timeout=CONFIG['timeout'])
    return page


async def login_flow(context):
    """Open the document in a visible browser and let the user sign in."""
    page = context.pages[0] if context.pages else await context.new_page()
    await page.goto(CONFIG['doc_url'], wait_until='domcontentloaded')
    print(f'Browser profile: {CONFIG["profile_dir"]}')
    print('Log in to Google in the opened browser, then press Enter here to continue.')
    await asyncio.to_thread(input)
    await page.close()


async def main():
    """Main entry point."""
    args = parse_args()

    CONFIG['profile_dir'].mkdir(parents=True, exist_ok=True)

    is_headless = CONFIG['headless'] if not args.login_only else False

    p = await async_playwright().start()
    try:
        if is_headless:
            print(f"Launching local persistent headless Chromium browser (Profile: {CONFIG['profile_dir']})...")
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(CONFIG['profile_dir']),
                headless=True,
                ignore_default_args=['--enable-automation'],
                args=['--disable-blink-features=AutomationControlled', '--mute-audio']
            )
        else:
            mode_desc = "to save login session " if args.login_only else ""
            print(f"Launching local persistent visible Chromium browser {mode_desc}(Profile: {CONFIG['profile_dir']})...")
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(CONFIG['profile_dir']),
                headless=False,
                ignore_default_args=['--enable-automation'],
                args=['--disable-blink-features=AutomationControlled', '--mute-audio']
            )

        if args.login_only:
            await login_flow(context)
            print('Login session saved.')
            return

        if not args.jobs:
            print("❌ No input files specified to process.", file=sys.stderr)
            sys.exit(1)

        # Authenticate with Google Docs API once
        try:
            google_creds = get_google_credentials()
            doc_id = get_doc_id(CONFIG['doc_url'])
        except Exception as e:
            print(f"❌ Google Docs API authentication error: {e}", file=sys.stderr)
            sys.exit(1)

        print(f"Starting browser page for {len(args.jobs)} file(s)...")
        page = await open_tts_page(context)

        try:
            for job_idx, job in enumerate(args.jobs):
                print(f"\n==================================================")
                print(f"Processing File {job_idx + 1}/{len(args.jobs)}")
                print(f"Input:  {job.input_path}")
                print(f"Output: {job.output_path}")
                print(f"==================================================")

                # Ensure output directory exists
                job.output_path.parent.mkdir(parents=True, exist_ok=True)

                # Resume support: skip if the final concatenated file already exists
                if job.output_path.exists() and job.output_path.stat().st_size > 0:
                    print(f'⏭️  {job.output_path} already exists, skipping entire file.')
                    continue

                text = job.input_path.read_text(encoding='utf-8')
                chunks = split_text(text)
                print(f'Split into {len(chunks)} chunk(s)...')

                # Parse title and part number from filename stem
                stem = job.input_path.stem
                part_match = re.search(r'[\s_]+Part[\s_]*(\d+)$', stem, re.IGNORECASE)
                if part_match:
                    part_num = int(part_match.group(1))
                    title_part = stem[:-len(part_match.group(0))]
                else:
                    part_num = None
                    title_part = stem

                cleaned_title = get_clean_title(title_part)
                has_malayalam = bool(re.search(r'[\u0d00-\u0d7f]', cleaned_title))
                if part_num is not None:
                    part_label = f"ഭാഗം {part_num}" if has_malayalam else f"Part {part_num}"
                    spoken_title = f"{cleaned_title} - {part_label}"
                else:
                    spoken_title = cleaned_title

                for i, chunk in enumerate(chunks):
                    print(f'\n--- Chunk {i + 1}/{len(chunks)} ---')

                    if len(chunks) > 1:
                        out = suffix_path(job.output_path, f'-{i + 1}')
                    else:
                        out = job.output_path

                    # Resume support: skip chunks already on disk
                    if out.exists() and out.stat().st_size > 0:
                        print(f'⏭️  {out} already exists, skipping.')
                        continue
                    
                    # Prepend spoken title to the first chunk only
                    if i == 0:
                        chunk_to_process = f"{spoken_title}.\n\n{chunk}"
                    else:
                        chunk_to_process = chunk

                    current_text = chunk_to_process
                    while True:
                        try:
                            await process_chunk(page, google_creds, doc_id, current_text, out)
                            await close_player(page)
                            break
                        except RuntimeError as err:
                            if "Audio generation was unsuccessful" in str(err):
                                new_text, split_done = dissect_first_long_sentence(current_text, 280)
                                if split_done:
                                    print("⚠️ Audio generation failed. Dissecting the first long sentence and retrying...")
                                    current_text = new_text
                                    continue
                            raise

                # Concatenate multiple audio chunks into a single file
                if len(chunks) > 1:
                    chunk_paths = [
                        suffix_path(job.output_path, f'-{i + 1}')
                        for i in range(len(chunks))
                    ]
                    chunks_exist = all(chunk_path.exists() for chunk_path in chunk_paths)
                    
                    if chunks_exist:
                        print('\nConcatenating audio chunks...')
                        if concatenate_audio_chunks(chunk_paths, job.output_path):
                            print(f'✅ Final audiobook saved as {job.output_path}')
                            # Clean up individual chunk files
                            for chunk_path in chunk_paths:
                                if chunk_path.exists():
                                    chunk_path.unlink()
                            print(f'Cleaned up {len(chunks)} chunk files.')
                        else:
                            print('⚠️  ffmpeg concatenation failed.')
                            print(f'Individual chunks are preserved as {job.output_path.stem}-N{job.output_path.suffix}')
                    else:
                        print('⚠️  Cannot concatenate: not all chunk files are present.')

            print('\nAll files processed successfully!')
        finally:
            await page.close()
    finally:
        if 'context' in locals():
            try:
                await context.close()
            except Exception:
                pass
        await p.stop()


def run() -> None:
    """Run the command line application."""
    try:
        asyncio.run(main())
    except Exception as err:
        print(f'Error: {err}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    run()
