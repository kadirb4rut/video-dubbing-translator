import unittest

from live_dubbing.scheduling import QwenBoundaryAdmission


class BoundaryAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.policy = QwenBoundaryAdmission('balanced')

    def offer(self, *, words, end, age, signature=None, candidate='c1', **pressure):
        return self.policy.admit(
            signature=signature if signature is not None else (tuple(words), round(end, 2)),
            source_units=tuple(words), candidate_id=candidate,
            source_time=end, source_age_seconds=age, source_budget_seconds=20,
            asr_queue_depth=pressure.get('asr_queue_depth', 0),
            asr_queue_capacity=pressure.get('asr_queue_capacity', 100),
            tts_inflight=pressure.get('tts_inflight', False),
            phrase_queue_depth=pressure.get('phrase_queue_depth', 0),
            ready_speech_seconds=pressure.get('ready_speech_seconds', 0.0),
            previous_pair_seconds=pressure.get('previous_pair_seconds', 1.0),
            rate_window_time_seconds=pressure.get('rate_window_time_seconds', end))

    def test_admits_new_evidence_then_coalesces_identical_revision(self):
        first = self.offer(words=('the', 'first'), end=5, age=5)
        repeated = self.offer(words=('the', 'first'), end=5, age=5)
        self.assertTrue(first.admitted)
        self.assertFalse(repeated.admitted)
        self.assertEqual(repeated.reason, 'unchanged_source_revision')

    def test_requires_new_context_and_measured_source_gap(self):
        self.assertTrue(self.offer(words=('a', 'b'), end=5, age=5).admitted)
        one_word = self.offer(words=('a', 'b', 'c'), end=5.8, age=5.8)
        self.assertFalse(one_word.admitted)
        self.assertEqual(one_word.reason, 'insufficient_new_source_evidence')
        two_words = self.offer(words=('a', 'b', 'c', 'd'), end=9, age=9)
        self.assertTrue(two_words.admitted)
        self.assertGreaterEqual(two_words.minimum_source_gap_seconds, 4.0)

    def test_unstable_hypothesis_revision_does_not_count_as_stable_evidence(self):
        self.assertTrue(self.policy.admit(signature=('stable', 'draft-1'),
            source_units=('stable',), candidate_id='c1', source_time=4,
            source_age_seconds=5, source_budget_seconds=20).admitted)
        revised_draft = self.policy.admit(signature=('stable', 'draft-2'),
            source_units=('stable',), candidate_id='c1', source_time=4,
            source_age_seconds=7, source_budget_seconds=20)
        self.assertFalse(revised_draft.admitted)
        self.assertEqual(revised_draft.reason, 'insufficient_new_source_evidence')

    def test_defers_to_asr_and_tts_then_admits_when_uncertainty_is_urgent(self):
        self.assertFalse(self.offer(words=('one',), end=4, age=4,
                                    asr_queue_depth=30).admitted)
        self.assertFalse(self.offer(words=('one',), end=4, age=4,
                                    tts_inflight=True).admitted)
        # Deferred revisions do not consume the admitted-request budget.
        pressure_cleared = self.offer(words=('one',), end=4, age=4)
        self.assertTrue(pressure_cleared.admitted)
        urgent = self.offer(words=('one', 'two'), end=12.2, age=12.2,
                            tts_inflight=True, ready_speech_seconds=5)
        self.assertTrue(urgent.admitted)
        self.assertEqual(urgent.reason, 'urgent_uncertainty')

    def test_even_urgent_boundary_work_yields_to_a_backlogged_asr_queue(self):
        result = self.offer(words=('one', 'two'), end=12.2, age=12.2,
                            asr_queue_depth=30, tts_inflight=True)
        self.assertFalse(result.admitted)
        self.assertEqual(result.reason, 'asr_queue_has_priority')

    def test_audio_queue_pressure_uses_seconds(self):
        self.assertTrue(self.offer(words=('one',), end=4, age=4).admitted)
        pressured = self.offer(words=('one', 'two', 'three'), end=8, age=8,
                               ready_speech_seconds=3.1)
        self.assertFalse(pressured.admitted)
        self.assertEqual(pressured.reason, 'approved_tts_or_audio_backlog_has_priority')

    def test_modes_have_distinct_rate_and_urgency_policies(self):
        quality = QwenBoundaryAdmission('quality')
        balanced = QwenBoundaryAdmission('balanced')
        low_latency = QwenBoundaryAdmission('low_latency')
        result = []
        for policy in (quality, balanced, low_latency):
            result.append(policy.admit(signature='same', source_units=('x',),
                candidate_id='c', source_time=10, source_age_seconds=10,
                source_budget_seconds=20))
        self.assertFalse(result[0].urgent)
        self.assertFalse(result[1].urgent)
        self.assertTrue(result[2].urgent)
        self.assertGreater(result[0].minimum_source_gap_seconds,
                           result[1].minimum_source_gap_seconds)
        self.assertGreater(result[1].minimum_source_gap_seconds,
                           result[2].minimum_source_gap_seconds)

    def test_source_seek_resets_revision_budget(self):
        self.assertTrue(self.offer(words=('a', 'b'), end=50, age=5).admitted)
        restarted = self.offer(words=('a', 'b'), end=1, age=1)
        self.assertTrue(restarted.admitted)

    def test_boundary_pairs_have_a_mode_specific_source_minute_budget(self):
        quality = QwenBoundaryAdmission('quality')
        balanced = QwenBoundaryAdmission('balanced')
        for index in range(8):
            at = index * 5.0
            q = quality.admit(signature=('q', index), source_units=(str(index),),
                candidate_id=str(index), source_time=at, source_age_seconds=4,
                source_budget_seconds=30)
            b = balanced.admit(signature=('b', index), source_units=(str(index),),
                candidate_id=str(index), source_time=at, source_age_seconds=4,
                source_budget_seconds=30)
            self.assertEqual(q.admitted, index < 3)
            self.assertEqual(b.admitted, index < 4)

    def test_advancing_speech_frontier_expires_budget_when_stable_asr_time_stalls(self):
        policy=QwenBoundaryAdmission('balanced')
        for index,end in enumerate((5.,9.,13.,17.),1):
            outcome=policy.admit(signature=(index,end),source_units=('stable',str(index)),
                candidate_id=f'candidate-{index}',source_time=end,source_age_seconds=4.,
                source_budget_seconds=20.,previous_pair_seconds=1.,rate_window_time_seconds=end)
            self.assertTrue(outcome.admitted)
        blocked=policy.admit(signature=('same-final-asr',18.),source_units=('stable','final'),
            candidate_id='final',source_time=22.,source_age_seconds=4.,source_budget_seconds=20.,
            previous_pair_seconds=1.,rate_window_time_seconds=22.)
        self.assertFalse(blocked.admitted)
        self.assertEqual(blocked.reason,'source_minute_inference_budget')

        # The final stable ASR token only advanced 5 seconds, but VAD confirms
        # that the live source has continued for another minute.
        resumed=policy.admit(signature=('same-final-asr','later-context'),
            source_units=('stable','final','context'),candidate_id='later',source_time=27.,
            source_age_seconds=6.,source_budget_seconds=20.,previous_pair_seconds=1.,
            rate_window_time_seconds=65.01)
        self.assertTrue(resumed.admitted)
        self.assertEqual(resumed.requests_per_source_minute,4)

    def test_urgent_source_uncertainty_bypasses_soft_budget_but_obeys_cooldown(self):
        policy = QwenBoundaryAdmission('balanced')
        for index in range(4):
            admitted = policy.admit(signature=('stable', index),
                source_units=('stable', str(index)), candidate_id=str(index),
                source_time=index*4., source_age_seconds=4., source_budget_seconds=20.)
            self.assertTrue(admitted.admitted)
        urgent = policy.admit(signature=('urgent', 4),
            source_units=('stable', '4', 'next'), candidate_id='urgent-1',
            source_time=16., source_age_seconds=12., source_budget_seconds=20.,
            previous_pair_seconds=1.)
        self.assertTrue(urgent.admitted)
        self.assertEqual(urgent.reason, 'urgent_uncertainty')
        too_soon = policy.admit(signature=('urgent', 5),
            source_units=('stable', '4', 'next', 'more'), candidate_id='urgent-2',
            source_time=17., source_age_seconds=13., source_budget_seconds=20.,
            previous_pair_seconds=1.)
        self.assertFalse(too_soon.admitted)
        self.assertEqual(too_soon.reason, 'measured_boundary_rate_budget')
        self.assertEqual(too_soon.urgent_source_gap_seconds, 1.5)
        cooled = policy.admit(signature=('urgent', 6),
            source_units=('stable', '4', 'next', 'more', 'again'), candidate_id='urgent-3',
            source_time=20., source_age_seconds=16., source_budget_seconds=20.,
            previous_pair_seconds=1.)
        self.assertTrue(cooled.admitted)

    def test_urgent_cooldown_tracks_measured_boundary_pair_time(self):
        policy = QwenBoundaryAdmission('balanced')
        first = policy.admit(signature='first', source_units=('a',), candidate_id='a',
            source_time=4., source_age_seconds=12., source_budget_seconds=20.,
            previous_pair_seconds=.8)
        self.assertTrue(first.admitted)
        self.assertAlmostEqual(first.urgent_source_gap_seconds, 1.2)
        within_measured_cooldown = policy.admit(signature='second',
            source_units=('a', 'b'), candidate_id='b', source_time=5.1,
            source_age_seconds=13.1, source_budget_seconds=20., previous_pair_seconds=.8)
        self.assertFalse(within_measured_cooldown.admitted)
        just_after_measured_cooldown = policy.admit(signature='third',
            source_units=('a', 'b', 'c'), candidate_id='c', source_time=8.,
            source_age_seconds=16., source_budget_seconds=20., previous_pair_seconds=.8)
        self.assertTrue(just_after_measured_cooldown.admitted)

    def test_urgent_pair_budget_is_bounded_and_mode_specific(self):
        for mode, limit in (('quality', 18), ('balanced', 24), ('low_latency', 36)):
            policy = QwenBoundaryAdmission(mode)
            outcomes = []
            for index in range(limit + 1):
                outcomes.append(policy.admit(signature=('urgent', index),
                    source_units=('stable', str(index)), candidate_id=str(index),
                    source_time=index*1.5, source_age_seconds=16.+index*1.5,
                    source_budget_seconds=20., previous_pair_seconds=1.))
            self.assertTrue(all(item.admitted for item in outcomes[:limit]), mode)
            self.assertFalse(outcomes[-1].admitted, mode)
            self.assertEqual(outcomes[-1].reason, 'urgent_source_minute_inference_budget')

    def test_new_context_does_not_bypass_measured_pair_cooldown(self):
        first = self.offer(words=('a', 'b'), end=5., age=5.)
        self.assertTrue(first.admitted)
        too_soon = self.offer(words=('a', 'b'), end=6.5, age=6.5)
        self.assertFalse(too_soon.admitted)
        self.assertEqual(too_soon.reason, 'measured_boundary_rate_budget')
        after_cooldown = self.offer(words=('a', 'b'), end=9., age=9.)
        self.assertTrue(after_cooldown.admitted)


if __name__ == '__main__':
    unittest.main()
