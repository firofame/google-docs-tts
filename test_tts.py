import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import tts


class ArgumentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.dict(tts.CONFIG))
        stack.enter_context(patch('sys.stderr', new_callable=io.StringIO))

    def parse(self, *args):
        with patch.object(sys, 'argv', ['tts.py', *map(str, args)]):
            return tts.parse_args()

    def test_default_and_explicit_outputs(self):
        source = self.root / 'chapter.txt'
        for extra, output in [
            ([], source.with_suffix('.ogg')),
            (['--opus'], source.with_suffix('.ogg')),
            ([self.root / 'audio.mp3'], self.root / 'audio.mp3'),
            ([self.root / 'audio.ogg'], self.root / 'audio.ogg'),
        ]:
            with self.subTest(extra=extra):
                self.assertEqual(self.parse(source, *extra).jobs, [tts.FileJob(source, output)])

    def test_output_directories(self):
        source = self.root / 'chapter.txt'
        for name, create in [('existing', True), ('new', False)]:
            output = self.root / name
            if create:
                output.mkdir()
            with self.subTest(name=name):
                self.assertEqual(
                    self.parse(source, output).jobs,
                    [tts.FileJob(source, output / 'chapter.ogg')],
                )

    def test_directory_filters_and_sorts_inputs(self):
        inputs = self.root / 'inputs'
        inputs.mkdir()
        for name in ['b.TXT', 'a.md', 'ignored.pdf']:
            (inputs / name).touch()
        (inputs / 'nested.txt').mkdir()
        expected = [inputs / 'a.md', inputs / 'b.TXT']
        self.assertEqual([job.input_path for job in self.parse(inputs).jobs], expected)
        output = self.root / 'output'
        self.assertEqual(
            self.parse(inputs, output).jobs,
            [tts.FileJob(path, output / path.with_suffix('.ogg').name) for path in expected],
        )

    def test_multiple_inputs(self):
        inputs = [self.root / f'{i}.txt' for i in range(3)]
        for output in [None, self.root / 'output']:
            with self.subTest(output=output):
                args = inputs + ([output] if output else [])
                self.assertEqual(
                    self.parse(*args).jobs,
                    [
                        tts.FileJob(path, output / f'{path.stem}.ogg' if output else path.with_suffix('.ogg'))
                        for path in inputs
                    ],
                )

    def test_flags_and_login(self):
        args = self.parse('--debug', '--headless', '--no-headless', '--login')
        self.assertTrue(args.login_only)
        self.assertEqual(args.jobs, [])
        self.assertFalse(tts.CONFIG['headless'])
        self.assertTrue(tts.CONFIG['debug'])
        self.parse('--headless', self.root / 'chapter.txt')
        self.assertTrue(tts.CONFIG['headless'])

    def test_invalid_inputs_exit(self):
        for args in [[], [self.root], [self.root, self.root / 'a.txt', self.root / 'b.txt']]:
            with self.subTest(args=args), self.assertRaises(SystemExit) as error:
                self.parse(*args)
            self.assertEqual(error.exception.code, 1)


class TextTests(unittest.TestCase):
    def test_chunk_limits(self):
        with patch.dict(tts.CONFIG, max_chunk_length=20):
            for text in ['x' * 61, 'several words ' * 10, 'first\nsecond\nthird\nfourth', '\u0d05' * 41]:
                with self.subTest(text=text):
                    chunks = tts.split_text(text)
                    self.assertTrue(chunks)
                    self.assertTrue(all(0 < len(chunk) <= 20 for chunk in chunks))
                    self.assertEqual(''.join(''.join(chunks).split()), ''.join(text.split()))
            self.assertEqual(tts.split_text(''), [])
            self.assertEqual(tts.split_text('first\nsecond'), ['first\nsecond'])

    def test_retry_splits_only_first_long_sentence(self):
        sentence = 'word ' * 80 + 'end.'
        text = f'Short.\n{sentence}\n{sentence}'
        updated, changed = tts.dissect_first_long_sentence(text)
        self.assertTrue(changed)
        self.assertEqual(updated.splitlines()[0], 'Short.')
        self.assertNotEqual(updated.splitlines()[1], sentence)
        self.assertEqual(updated.splitlines()[2], sentence)
        self.assertTrue(all(len(part) <= 280 for part in updated.splitlines()[1].split('. ')))
        self.assertEqual(tts.dissect_first_long_sentence('Short.'), ('Short.', False))
        self.assertEqual(tts.dissect_first_long_sentence('x' * 300), ('x' * 300, False))


class AudioTests(unittest.TestCase):
    @patch('tts.subprocess.run')
    def test_conversion_options(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, b'', b'')
        self.assertTrue(tts.convert_mp3_to_ogg_opus(Path('in.mp3'), Path('out.ogg')))
        self.assertEqual(
            run.call_args.args[0],
            [
                'ffmpeg', '-y', '-i', 'in.mp3', '-c:a', 'libopus', '-b:a', '64k',
                '-ac', '1', '-ar', '48000', '-application', 'voip', '-f', 'ogg', 'out.ogg',
            ],
        )

    def test_concat_options_and_manifest_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chunks = [root / "one's.mp3", root / 'two.mp3']
            manifest = root / 'ffmpeg_concat_list.txt'

            def run(args, **kwargs):
                self.assertEqual(
                    manifest.read_text(),
                    ''.join(tts._ffmpeg_concat_line(path) for path in chunks),
                )
                return subprocess.CompletedProcess(args, 0, b'', b'')

            with patch('tts.subprocess.run', side_effect=run) as command:
                for suffix in ['.mp3', '.ogg']:
                    output = root / f'book{suffix}'
                    self.assertTrue(tts.concatenate_audio_chunks(chunks, output))
                    args = command.call_args.args[0]
                    self.assertEqual(args[:8], ['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', str(manifest)])
                    self.assertEqual(args[8:-1], tts.OPUS_ARGS if suffix == '.ogg' else ['-c', 'copy'])
                    self.assertEqual(args[-1], str(output))
                    self.assertFalse(manifest.exists())

                command.side_effect = FileNotFoundError('ffmpeg')
                with patch('sys.stderr', new_callable=io.StringIO):
                    self.assertFalse(tts.concatenate_audio_chunks(chunks, root / 'book.ogg'))
                self.assertFalse(manifest.exists())

    def test_concat_path_escaping(self):
        self.assertEqual(
            tts._ffmpeg_concat_line(Path("/tmp/it's\\audio.mp3")),
            "file '/tmp/it\\'s\\\\audio.mp3'\n",
        )

    def test_ffmpeg_failures_are_reported(self):
        for result, error in [
            (subprocess.CompletedProcess([], 1, b'', b'encoder failure'), None),
            (None, FileNotFoundError('ffmpeg')),
        ]:
            with self.subTest(error=error), patch(
                'tts.subprocess.run', return_value=result, side_effect=error,
            ), patch('sys.stderr', new_callable=io.StringIO) as stderr:
                self.assertFalse(tts.convert_mp3_to_ogg_opus(Path('in.mp3'), Path('out.ogg')))
                self.assertIn('ffmpeg', stderr.getvalue())


class FileProcessingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'source.txt'
        self.source.write_text('short', encoding='utf-8')
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.dict(tts.CONFIG, max_chunk_length=12))
        stack.enter_context(patch('sys.stdout', new_callable=io.StringIO))
        self.generate = stack.enter_context(patch('tts.process_chunk', side_effect=self.save_chunk))
        self.close = stack.enter_context(patch('tts.close_player', new_callable=AsyncMock))
        self.convert = stack.enter_context(patch('tts.convert_mp3_to_ogg_opus', side_effect=self.save_audio))
        self.concat = stack.enter_context(patch('tts.concatenate_audio_chunks', side_effect=self.save_audio))

    async def save_chunk(self, page, creds, doc_id, text, path):
        path.write_bytes(b'mp3')
        return 'blob:audio'

    def save_audio(self, source, output):
        output.write_bytes(b'final audio')
        return True

    async def process(self, output):
        await tts.process_file(None, None, 'doc-id', tts.FileJob(self.source, output))

    async def test_single_and_multiple_chunks_in_both_formats(self):
        for text, count in [('short', 1), ('first chunk\nsecond chunk', 2)]:
            for suffix in ['.mp3', '.ogg']:
                with self.subTest(count=count, suffix=suffix):
                    self.source.write_text(text, encoding='utf-8')
                    output = self.root / 'nested' / f'book-{count}-{suffix[1:]}{suffix}'
                    await self.process(output)
                    self.assertTrue(output.exists())
                    audio = output.with_suffix('.mp3')
                    expected = [tts.suffix_path(audio, f'-{i + 1}') for i in range(count)] if count > 1 else [audio]
                    self.assertEqual([call.args[-1] for call in self.generate.await_args_list], expected)
                    self.assertEqual([call.args[3] for call in self.generate.await_args_list], text.splitlines())
                    self.assertEqual(self.close.await_count, count)
                    if count > 1:
                        self.concat.assert_called_once_with(expected, output)
                        self.assertTrue(all(not path.exists() for path in expected))
                    elif suffix == '.ogg':
                        self.convert.assert_called_once_with(audio, output)
                        self.assertFalse(audio.exists())
                    else:
                        self.convert.assert_not_called()
                        self.concat.assert_not_called()
                    self.generate.reset_mock()
                    self.close.reset_mock()
                    self.concat.reset_mock()
                    self.convert.reset_mock()

    async def test_existing_final_output_skips_input(self):
        output = self.root / 'book.ogg'
        output.write_bytes(b'already complete')
        self.source.unlink()
        await self.process(output)
        self.generate.assert_not_awaited()
        self.convert.assert_not_called()
        self.assertEqual(output.read_bytes(), b'already complete')

    async def test_resume_existing_chunks_and_regenerate_empty_chunks(self):
        self.source.write_text('first chunk\nsecond chunk', encoding='utf-8')
        output = self.root / 'book.ogg'
        (self.root / 'book-1.mp3').write_bytes(b'existing')
        (self.root / 'book-2.mp3').touch()
        await self.process(output)
        self.generate.assert_awaited_once()
        self.assertEqual(self.generate.await_args.args[3:], ('second chunk', self.root / 'book-2.mp3'))
        self.assertTrue(output.exists())
        self.assertFalse((self.root / 'book-1.mp3').exists())

    async def test_resume_single_mp3_before_conversion(self):
        (self.root / 'book.mp3').write_bytes(b'existing')
        output = self.root / 'book.ogg'
        await self.process(output)
        self.generate.assert_not_awaited()
        self.convert.assert_called_once_with(self.root / 'book.mp3', output)
        self.assertFalse((self.root / 'book.mp3').exists())

    async def test_sentence_failure_retries_with_shorter_sentences(self):
        text = 'word ' * 80 + 'end.'
        self.source.write_text(text, encoding='utf-8')
        tts.CONFIG['max_chunk_length'] = 20_000
        attempts = 0

        async def generate(*args):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError('Audio generation was unsuccessful')
            return await self.save_chunk(*args)

        self.generate.side_effect = generate
        await self.process(self.root / 'book.mp3')
        self.assertEqual(attempts, 2)
        self.assertNotEqual(self.generate.await_args.args[3], text)
        self.close.assert_awaited_once()

    async def test_unrecoverable_errors_are_not_retried(self):
        for message in ['Audio generation was unsuccessful', 'unrelated error']:
            with self.subTest(message=message):
                self.generate.side_effect = RuntimeError(message)
                with self.assertRaisesRegex(RuntimeError, message):
                    await self.process(self.root / 'book.mp3')
                self.generate.assert_awaited_once()
                self.generate.reset_mock()

    async def test_assembly_failures_preserve_source_audio(self):
        for text, helper, names in [
            ('short', self.convert, ['book.mp3']),
            ('first chunk\nsecond chunk', self.concat, ['book-1.mp3', 'book-2.mp3']),
        ]:
            with self.subTest(text=text):
                self.source.write_text(text, encoding='utf-8')
                helper.side_effect = None
                helper.return_value = False
                with self.assertRaisesRegex(RuntimeError, 'failed'):
                    await self.process(self.root / 'book.ogg')
                self.assertTrue(all((self.root / name).read_bytes() == b'mp3' for name in names))


class BrowserLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_launch_modes_login_and_cleanup(self):
        for login, headless in [(False, False), (False, True), (True, True)]:
            with self.subTest(login=login, headless=headless), tempfile.TemporaryDirectory() as directory:
                playwright = MagicMock()
                context = MagicMock(close=AsyncMock())
                playwright.chromium.launch_persistent_context = AsyncMock(return_value=context)
                manager = MagicMock()
                manager.__aenter__ = AsyncMock(return_value=playwright)
                manager.__aexit__ = AsyncMock(return_value=False)
                page = MagicMock(close=AsyncMock())
                jobs = [tts.FileJob(Path('in.txt'), Path('out.ogg'))]
                args = tts.Args(jobs=[] if login else jobs, login_only=login)
                with (
                    patch.dict(tts.CONFIG, headless=headless, profile_dir=Path(directory)),
                    patch('sys.stdout', new_callable=io.StringIO),
                    patch('tts.parse_args', return_value=args),
                    patch('tts.async_playwright', return_value=manager),
                    patch('tts.get_google_credentials', return_value='creds') as credentials,
                    patch('tts.open_tts_page', new_callable=AsyncMock, return_value=page),
                    patch('tts.login_flow', new_callable=AsyncMock) as login_flow,
                    patch('tts.process_file', new_callable=AsyncMock) as process_file,
                ):
                    await tts.main()
                launch = playwright.chromium.launch_persistent_context
                self.assertEqual(launch.await_args.kwargs['headless'], headless and not login)
                self.assertEqual(launch.await_args.kwargs['args'], ['--disable-blink-features=AutomationControlled', '--mute-audio'])
                context.close.assert_awaited_once()
                manager.__aexit__.assert_awaited_once()
                if login:
                    login_flow.assert_awaited_once_with(context)
                    credentials.assert_not_called()
                    process_file.assert_not_awaited()
                else:
                    process_file.assert_awaited_once_with(page, 'creds', tts.get_doc_id(tts.CONFIG['doc_url']), jobs[0])
                    page.close.assert_awaited_once()

    async def test_processing_failure_closes_resources_and_does_not_report_success(self):
        with tempfile.TemporaryDirectory() as directory:
            context = MagicMock(close=AsyncMock())
            playwright = MagicMock()
            playwright.chromium.launch_persistent_context = AsyncMock(return_value=context)
            manager = MagicMock()
            manager.__aenter__ = AsyncMock(return_value=playwright)
            manager.__aexit__ = AsyncMock(return_value=False)
            page = MagicMock(close=AsyncMock())
            with (
                patch.dict(tts.CONFIG, profile_dir=Path(directory)),
                patch('sys.stdout', new_callable=io.StringIO) as stdout,
                patch('tts.parse_args', return_value=tts.Args([tts.FileJob(Path('in.txt'), Path('out.ogg'))])),
                patch('tts.async_playwright', return_value=manager),
                patch('tts.get_google_credentials', return_value='creds'),
                patch('tts.open_tts_page', new_callable=AsyncMock, return_value=page),
                patch('tts.process_file', side_effect=RuntimeError('conversion failed')),
            ):
                with self.assertRaisesRegex(RuntimeError, 'conversion failed'):
                    await tts.main()
            self.assertNotIn('All files processed successfully', stdout.getvalue())
            page.close.assert_awaited_once()
            context.close.assert_awaited_once()
            manager.__aexit__.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
