import unittest
from types import SimpleNamespace

from live_dubbing.speech import IncrementalWhisper


EMPTY_ALIGNMENT_ERROR = (
    'boolean index did not match indexed array along axis 0; '
    'size of axis is 0 but size of corresponding boolean axis is 1')


class AlignmentRetryTests(unittest.TestCase):
    def test_empty_word_alignment_retries_asr_text_with_segment_timing(self):
        info = SimpleNamespace(language='en', language_probability=.97)
        partial = SimpleNamespace(text=' partial words', start=.1, end=.4,
            words=[SimpleNamespace(word=' partial', start=.1, end=.2)],
            no_speech_prob=0., avg_logprob=-.1)
        fallback = SimpleNamespace(text=' We were hired today.', start=.2,
            end=1.3, words=None, no_speech_prob=0., avg_logprob=-.12)

        class Model:
            calls = []

            def transcribe(self, audio, **kwargs):
                self.calls.append(kwargs)
                if kwargs['word_timestamps']:
                    def broken_segments():
                        yield partial
                        raise IndexError(EMPTY_ALIGNMENT_ERROR)
                    return broken_segments(), info
                return iter([fallback]), info

        model = Model()
        result = IncrementalWhisper(model_instance=model).transcribe(
            b'\x01\x00' * 16000, start=4.0, language='en')

        self.assertEqual(len(model.calls), 2)
        self.assertTrue(model.calls[0]['word_timestamps'])
        self.assertFalse(model.calls[1]['word_timestamps'])
        self.assertEqual(result.text, 'We were hired today.')
        self.assertEqual(len(result.words), 1)
        self.assertAlmostEqual(result.words[0].start, 4.2)
        self.assertAlmostEqual(result.words[0].end, 5.3)
        self.assertEqual(result.runtime_metrics['word_timing_alignment_fallback'], {
            'reason': 'empty_alignment_frames', 'word_timestamps_requested': True,
            'word_timestamps_used': False, 'text_redecoded': True})

    def test_unrelated_index_error_is_not_retried_or_swallowed(self):
        info = SimpleNamespace(language='en', language_probability=.97)

        class Model:
            calls = 0

            def transcribe(self, audio, **kwargs):
                self.calls += 1
                def broken_segments():
                    raise IndexError('unrelated array indexing bug')
                    yield  # pragma: no cover - makes this a generator
                return broken_segments(), info

        model = Model()
        with self.assertRaisesRegex(IndexError, 'unrelated array indexing bug'):
            IncrementalWhisper(model_instance=model).transcribe(b'\x01\x00' * 16000)
        self.assertEqual(model.calls, 1)


if __name__ == '__main__':
    unittest.main()
