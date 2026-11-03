import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'analyze_live_latency.py'
SPEC = importlib.util.spec_from_file_location('analyze_live_latency', SCRIPT)
ANALYZER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ANALYZER)


class LiveLatencyAnalysisTests(unittest.TestCase):
    def test_report_correlates_phrase_metrics_without_copying_transcripts(self):
        events = [
            {'type':'ready','server_elapsed':0.,'server_monotonic_started_at':100.},
            {'type':'hypothesis','server_elapsed':1.0,'decode_coalescing_wait_seconds':.1,
             'local_agreement_seconds':.01,'boundary_model_seconds':.02,'endpoint_decision_seconds':.03,
             'ingest_queue':1,'phrase_queue':0,'input_queue_seconds':.02,
             'provisional':'PRIVATE SOURCE WORDS'},
            {'type':'committed','server_elapsed':1.1,'phrase_id':'phrase-a','source_start':0.,'source_end':1.},
            {'type':'phrase_ready','server_elapsed':2.0,'phrase_id':'phrase-a','presentation_mode':'speech',
             'metrics':{'speech_end_to_usable_audio_seconds':1.5,'speech_end_to_asr_seconds':.5,
               'source_asr_inference_seconds':.3,'tts_first_chunk_seconds':.1,
               'source_seconds':1.,'audio_seconds':1.2,'presentation_audio_seconds':1.4,
               'tts_to_source_duration_ratio':1.2,'presentation_to_source_duration_ratio':1.4,'retry_count':1,
               'translation_commitment':{'draft_context':{'translator_inference_seconds':.4}},
               'stage_timestamps_monotonic':{
                 'speech_end_source_monotonic_at':100.,'asr_dispatched_at':100.2,
                 'asr_worker_started_at':100.25,'asr_worker_completed_at':100.45,
                 'asr_completed_at':100.5,'stable_prefix_confirmed_at':100.6,
                 'endpoint_decision_completed_at':100.8,'source_committed_at':100.9,
                 'phrase_queue_enqueued_at':100.95,'render_dispatched_at':101.1,
                 'render_executor_started_at':101.2,'model_worker_started_at':101.25,
                 'model_worker_completed_at':101.8,'render_executor_completed_at':101.9,
                 'speaker_analysis_started_at':101.3,'speaker_analysis_completed_at':101.4,
                 'context_retrieval_started_at':101.4,'context_retrieval_completed_at':101.5,
                 'translation_started_at':101.6,'translation_completed_at':101.7,
                 'translation_validation_started_at':101.7,'translation_validation_completed_at':101.75},
               'tts_candidate_timings':[{'conditioning_cache_seconds':.02,
                 'conditioning_started_at':101.9,'conditioning_completed_at':101.92,
                 'tts_started_at':101.92,'tts_first_chunk_at':102.02,'tts_completed_at':102.2,
                 'qa_started_at':102.2,'qa_completed_at':102.7,'qa_seconds':.5,
                 'tts_first_chunk_seconds':.1,'tts_seconds':.28,'tts_rtf':.28,
                 'speaker_similarity_seconds':.03,'speaker_similarity_started_at':102.65,
                 'speaker_similarity_completed_at':102.68,'failures':[]} ]},
             'target_text':'PRIVATE TARGET WORDS'},
            {'type':'played','server_elapsed':3.0,'phrase_id':'phrase-a',
             'speech_end_to_playback_seconds':2.,'playback_queue_seconds':.4,
             'audio_video_offset_seconds':.02,'video_playback_rate':.39,
             'requested_video_playback_rate':.39,'video_playback_rate_clamped':False,
             'audio_output_start_performance_ms':7000.,
             'audio_output_end_performance_ms':8000.,'playback_admission_performance_ms':6500.,
             'presentation_mode':'speech','live_edge_to_displayed_video_seconds':3.},
            {'type':'clock_sync','server_elapsed':2.9,
             'client_perf_to_server_monotonic_offset_ms':95000.,'uncertainty_ms':4.},
            {'type':'playback_metrics','server_elapsed':3.1,'queue_depth':2,'buffer_seconds':5.,
             'capture_media_time':4.,'video_media_time':1.},
            {'type':'capture_metrics','server_elapsed':3.2,'capture_media_time':4.},
            {'type':'coverage','server_elapsed':3.3,'analyzed_until':3.5},
            {'type':'warning','server_elapsed':3.4,'code':'untranslated_final_tail',
             'source_start':3.,'source_end':4.},
            {'type':'hypothesis','server_elapsed':4.0,'ingest_queue':0,'phrase_queue':1,'input_queue_seconds':0.},
        ]
        summary = ANALYZER.summarize(events)
        self.assertEqual(summary['phrase_count'], 1)
        phrase = summary['phrases'][0]
        self.assertEqual(phrase['stages_seconds']['speech_end_to_playback_seconds'], 2.)
        self.assertEqual(summary['stage_distributions_seconds']['translation_inference_seconds']['p50'], .4)
        self.assertEqual(summary['tts_candidate_stage_distributions_seconds']['tts_candidate_ttfa_seconds']['p50'], .1)
        self.assertEqual(summary['stage_distributions_seconds']['presentation_to_source_duration_ratio']['p50'], 1.4)
        self.assertEqual(summary['stage_distributions_seconds']['tts_retry_count']['max'], 1)
        self.assertEqual(summary['queue_timeline'][1]['browser_phrase_queue_depth'], 2)
        self.assertEqual(phrase['browser_clock_mapping']['audio_output_start_server_elapsed_seconds'],2.)
        self.assertEqual(phrase['browser_clock_mapping']['playback_admission_server_elapsed_seconds'],1.5)
        self.assertIn('tts_attempt_0_qa', {stage['stage'] for stage in phrase['critical_path']})
        self.assertAlmostEqual(summary['critical_path_stage_distributions_seconds'][
            'speech_end_to_asr_dispatch']['p50'], .2)
        self.assertEqual(summary['critical_path_stage_distributions_seconds'][
            'tts_attempt_0_qa']['max'], .5)
        self.assertEqual(summary['coverage']['untranslated_tail_seconds'],1.)
        self.assertEqual(summary['coverage']['committed_interval_over_capture_ratio'],.25)
        self.assertEqual(summary['playback']['speech_count'],1)
        self.assertEqual(summary['playback']['minimum_video_playback_rate'],.39)
        self.assertEqual(summary['playback']['video_playback_rate_clamp_count'],0)
        self.assertEqual(phrase['stages_seconds']['requested_video_playback_rate'],.39)
        self.assertEqual(phrase['stages_seconds']['video_playback_rate_clamped'],0)
        self.assertEqual(summary['queue_timeline'][1]['live_edge_to_displayed_video_seconds'],3.)
        with TemporaryDirectory() as temporary:
            json_path, html_path = ANALYZER.write_report(summary, Path(temporary))
            rendered = json_path.read_text()+html_path.read_text()
        self.assertNotIn('PRIVATE SOURCE WORDS', rendered)
        self.assertNotIn('PRIVATE TARGET WORDS', rendered)
        self.assertIn('speech_end_to_playback_seconds', rendered)
        self.assertIn('Ordered critical-path stage distributions', rendered)
        self.assertIn('Source-media backlog', rendered)

    def test_caption_ready_is_not_counted_as_audio_and_missing_capture_clock_is_unknown(self):
        events = [
            {'type':'committed','phrase_id':'caption-a','source_start':0.,'source_end':1.},
            {'type':'phrase_ready','phrase_id':'caption-a','presentation_mode':'caption_only',
             'metrics':{'speech_end_to_usable_audio_seconds':1.8}},
            {'type':'committed','phrase_id':'speech-b','source_start':2.,'source_end':3.},
            {'type':'phrase_ready','phrase_id':'speech-b','presentation_mode':'speech',
             'metrics':{'speech_end_to_usable_audio_seconds':2.4}},
            {'type':'coverage','analyzed_until':5.},
        ]
        summary = ANALYZER.summarize(events)
        self.assertIsNone(summary['coverage']['captured_source_seconds'])
        self.assertFalse(summary['coverage']['capture_measurement_available'])
        self.assertEqual(summary['coverage']['analyzed_until_seconds'], 5.)
        self.assertEqual(summary['ready_outputs'], {
            'count':2,'speech_count':1,'caption_only_count':1})
        self.assertEqual(summary['stage_distributions_seconds'][
            'speech_end_to_first_audio_seconds']['n'], 1)
        self.assertEqual(summary['stage_distributions_seconds'][
            'speech_end_to_first_audio_seconds']['p50'], 2.4)
        self.assertEqual(summary['stage_distributions_seconds'][
            'speech_end_to_usable_output_seconds']['n'], 2)

    def test_video_rate_clamping_is_visible_in_playback_summary(self):
        events = [{'type':'committed','phrase_id':'slow-a','source_start':0.,'source_end':1.},
            {'type':'played','phrase_id':'slow-a','presentation_mode':'speech',
             'video_playback_rate':.25,'requested_video_playback_rate':.125,
             'video_playback_rate_clamped':True}]
        summary = ANALYZER.summarize(events)
        self.assertEqual(summary['playback']['video_playback_rate_clamp_count'],1)
        self.assertEqual(summary['playback']['minimum_video_playback_rate'],.25)
        self.assertEqual(summary['stage_distributions_seconds']['video_playback_rate_clamped']['p50'],1)

    def test_source_backlog_tracks_capture_to_asr_commit_and_ready_frontiers(self):
        events = [
            {'type':'hypothesis','server_elapsed':1.,'source_end':2.5},
            {'type':'committed','server_elapsed':1.1,'phrase_id':'phrase-a','source_start':1.,'source_end':2.},
            {'type':'phrase_ready','server_elapsed':2.,'phrase_id':'phrase-a','presentation_mode':'speech'},
            {'type':'capture_metrics','server_elapsed':2.1,'capture_media_time':4.},
            {'type':'hypothesis','server_elapsed':3.,'source_end':3.5},
            {'type':'committed','server_elapsed':3.1,'phrase_id':'phrase-b','source_start':2.5,'source_end':3.},
            {'type':'phrase_ready','server_elapsed':4.,'phrase_id':'phrase-b','presentation_mode':'speech'},
            {'type':'capture_metrics','server_elapsed':4.1,'capture_media_time':5.},
        ]
        summary = ANALYZER.summarize(events)
        captures = [point for point in summary['queue_timeline'] if point.get('capture_media_time') is not None]
        self.assertEqual(captures[0]['capture_minus_latest_asr_window_seconds'], 1.5)
        self.assertEqual(captures[0]['capture_minus_latest_committed_end_seconds'], 2.)
        self.assertEqual(captures[0]['capture_minus_latest_ready_end_seconds'], 2.)
        self.assertEqual(captures[1]['capture_minus_latest_asr_window_seconds'], 1.5)
        self.assertEqual(summary['source_backlog_distributions_seconds'][
            'capture_minus_latest_ready_end_seconds']['p50'], 2.)
        self.assertEqual(summary['queue_growth_per_second'][
            'capture_minus_latest_ready_end_seconds'], 0.)


if __name__ == '__main__':
    unittest.main()
