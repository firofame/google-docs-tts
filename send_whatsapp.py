"""Send OGG audio files as WhatsApp voice notes via WhatsApp Web.

Uses Playwright to drive WhatsApp Web and injects WPPConnect/wa-js
to send audio as a real PTT (push-to-talk) voice note with waveform.

Usage:
    python send_whatsapp.py --login          # First-time QR code scan
    python send_whatsapp.py PHONE file.ogg   # Send voice note
    python send_whatsapp.py PHONE f1.ogg f2.ogg ...  # Send multiple

PHONE should include country code without '+' (e.g. 919876543210).
"""

import sys
import base64
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

# Shared profile directory with tts.py
PROFILE_DIR = Path.home() / '.google-docs-tts-whatsapp-profile'

WA_JS_CDN = 'https://cdn.jsdelivr.net/npm/@wppconnect/wa-js/dist/wppconnect-wa.js'

# Wait up to 2 minutes for WhatsApp Web to fully load
LOAD_TIMEOUT = 120_000


async def inject_wpp(page) -> None:
    """Inject WPPConnect/wa-js into the WhatsApp Web page."""
    print('Injecting WPPConnect/wa-js...')
    await page.add_script_tag(url=WA_JS_CDN)

    # Wait for WPP to initialize and connect
    await page.wait_for_function(
        '() => typeof WPP !== "undefined" && WPP.isReady',
        timeout=LOAD_TIMEOUT,
    )
    print('✅ WPP ready')


async def send_voice_note(page, phone: str, ogg_path: Path) -> None:
    """Send a single OGG file as a PTT voice note."""
    chat_id = f'{phone}@c.us'
    audio_bytes = ogg_path.read_bytes()
    b64 = base64.b64encode(audio_bytes).decode('ascii')
    data_uri = f'data:audio/ogg;codecs=opus;base64,{b64}'

    print(f'Sending {ogg_path.name} to {phone}...')
    result = await page.evaluate(
        """async ([chatId, dataUri, filename]) => {
            try {
                const msg = await WPP.chat.sendFileMessage(chatId, dataUri, {
                    type: 'audio',
                    isPtt: true,
                    filename: filename,
                    mimetype: 'audio/ogg; codecs=opus',
                });
                return { ok: true, id: msg.id?.toString() || 'sent' };
            } catch (e) {
                return { ok: false, error: e.message || String(e) };
            }
        }""",
        [chat_id, data_uri, ogg_path.name],
    )

    if result.get('ok'):
        print(f'✅ Sent as voice note: {ogg_path.name}')
    else:
        print(f'❌ Failed: {result.get("error")}', file=sys.stderr)
        sys.exit(1)


async def wait_for_whatsapp_ready(page, timeout: int = LOAD_TIMEOUT) -> None:
    """Wait for WhatsApp Web to be fully loaded (chat list visible)."""
    print('Waiting for WhatsApp Web to load...')
    # Broad set of selectors — WhatsApp Web changes these periodically
    await page.wait_for_function(
        """() => {
            const s = document.querySelectorAll('[data-testid]');
            for (const el of s) {
                const tid = el.getAttribute('data-testid');
                if (tid && (tid.includes('chat-list') || tid.includes('chatlist')
                    || tid.includes('conversation-panel') || tid.includes('side'))) {
                    return true;
                }
            }
            return !!document.querySelector('#pane-side')
                || !!document.querySelector('[aria-label*="Chat"]')
                || !!document.querySelector('.two, ._aigs');
        }""",
        timeout=timeout,
    )
    print('✅ WhatsApp Web loaded')


async def main():
    args = sys.argv[1:]

    if not args:
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    is_login = args[0] == '--login'

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    p = await async_playwright().start()
    try:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,  # WhatsApp Web blocks headless browsers
            bypass_csp=True,
            ignore_default_args=['--enable-automation'],
            args=['--disable-blink-features=AutomationControlled'],
        )

        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto('https://web.whatsapp.com/', wait_until='domcontentloaded')

        if is_login:
            print('Scan the QR code in the browser window.')
            print('Once you see your chats, press Enter here to save the session.')
            await asyncio.to_thread(input)
            # Verify it actually loaded (short timeout — user said they see chats)
            try:
                await wait_for_whatsapp_ready(page, timeout=10_000)
            except Exception:
                print('⚠️  Could not verify chat list, but session may still be saved.')
            print('✅ Session saved!')
            return

        # Parse: PHONE file1.ogg [file2.ogg ...]
        phone = args[0]
        ogg_files = [Path(a).resolve() for a in args[1:]]

        if not ogg_files:
            print('Error: no OGG files specified.', file=sys.stderr)
            sys.exit(1)

        for f in ogg_files:
            if not f.exists():
                print(f'Error: {f} does not exist.', file=sys.stderr)
                sys.exit(1)

        await wait_for_whatsapp_ready(page)
        await inject_wpp(page)

        for ogg_file in ogg_files:
            await send_voice_note(page, phone, ogg_file)
            # Small delay between messages to avoid rate limiting
            if len(ogg_files) > 1:
                await asyncio.sleep(1)

        print(f'\n🎉 All {len(ogg_files)} voice note(s) sent!')

    finally:
        if 'context' in locals():
            try:
                await context.close()
            except Exception:
                pass
        await p.stop()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print('\nAborted.')
    except Exception as e:
        print(f'Error: {e}', file=sys.stderr)
        sys.exit(1)
