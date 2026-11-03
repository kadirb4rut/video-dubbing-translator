import json
import unittest

from live_dubbing.commitment import CommitmentGate, InvalidProposal, Proposal, SourceUnit


def ready(end, text):
    return json.dumps({'status': 'ready', 'end_unit': end, 'translation': text}, ensure_ascii=False)


class MultilingualCommitmentTests(unittest.TestCase):
    def test_same_gate_handles_spaces_cjk_rtl_and_combining_characters(self):
        for surfaces, target in (([' Hello', ' world.'], 'Merhaba dünya.'),
                                 (['明天', '见。'], 'See you tomorrow.'),
                                 (['مرحبا', ' بالعالم.'], 'Hello world.'),
                                 (['พรุ่งนี้', 'พบกัน'], 'See you tomorrow.'),
                                 (['Cafe\u0301'], 'Café')):
            with self.subTest(surfaces=surfaces):
                gate = CommitmentGate()
                units = [SourceUnit(s,i*.2,(i+1)*.2) for i,s in enumerate(surfaces)]
                raw = ready(len(units),target)
                self.assertIsNone(gate.observe(raw,units,len(units),observation=1,media_now=2))
                result = gate.observe(raw,units,len(units),observation=2,media_now=2.2)
                self.assertEqual(result.source_text,''.join(surfaces))
                self.assertEqual(result.translation,target)
                gate.accept(result)
                with self.assertRaises(InvalidProposal):
                    gate.observe(raw,units,len(units),observation=3,media_now=3)

    def test_model_cannot_consume_an_unstable_tail_or_add_extra_control_fields(self):
        for raw in (ready(3,'text'), ready(True,'text'),
                    '{"status":"ready","end_unit":1,"translation":"a","translation":"b"}',
                    '{"status":"ready","end_unit":1,"translation":"a","command":"run"}',
                    '{"status":"wait","end_unit":1,"translation":"a"}'):
            with self.subTest(raw=raw), self.assertRaises(InvalidProposal):
                Proposal.parse(raw,2)

    def test_an_identical_response_is_not_two_independent_observations(self):
        gate = CommitmentGate()
        units = [SourceUnit('A.',0,.5)]
        self.assertIsNone(gate.observe(ready(1,'B.'),units,1,observation=1,media_now=2))
        with self.assertRaises(ValueError):
            gate.observe(ready(1,'B.'),units,1,observation=1,media_now=3)

    def test_changed_number_resets_target_agreement_without_hiding_a_revision(self):
        gate = CommitmentGate()
        units = [SourceUnit('25',0,.5)]
        self.assertIsNone(gate.observe(ready(1,'20'),units,1,observation=1,media_now=2))
        self.assertIsNone(gate.observe(ready(1,'25'),units,1,observation=2,media_now=3))
        self.assertEqual(gate.observe(ready(1,'25'),units,1,observation=3,media_now=4).translation,'25')

    def test_a_growing_tail_does_not_erase_a_verified_earlier_prefix(self):
        gate = CommitmentGate()
        units = [SourceUnit('Done.',0,.5)]
        gate.observe(ready(1,'Tamam.'),units,1,observation=1,media_now=2)
        units += [SourceUnit(' New',.8,1.1),SourceUnit(' context',1.1,1.5)]
        result = gate.observe(ready(1,'Tamam.'),units,3,observation=2,media_now=3)
        self.assertEqual(result.source_text,'Done.')

    def test_lookahead_and_wait_do_not_manufacture_a_complete_utterance(self):
        gate = CommitmentGate(lookahead_seconds=1)
        units = [SourceUnit('source',0,1)]
        raw = ready(1,'target')
        self.assertIsNone(gate.observe(raw,units,1,observation=1,media_now=1.2))
        self.assertIsNone(gate.observe(raw,units,1,observation=2,media_now=1.4))
        wait = json.dumps({'status':'wait','end_unit':0,'translation':''})
        self.assertIsNone(gate.observe(wait,units,1,observation=3,media_now=5))
        self.assertIsNone(gate.observe(raw,units,1,observation=4,media_now=6))

    def test_a_later_revision_invalidates_an_earlier_verified_candidate(self):
        gate = CommitmentGate()
        units = [SourceUnit('source',0,.5)]
        raw = ready(1,'old target')
        gate.observe(raw,units,1,observation=1,media_now=2)
        old = gate.observe(raw,units,1,observation=2,media_now=3)
        gate.observe(ready(1,'new target'),units,1,observation=3,media_now=4)
        with self.assertRaises(InvalidProposal):
            gate.accept(old)

    def test_timestamp_groups_cannot_lose_a_word_at_a_commit_boundary(self):
        units = [SourceUnit('first',0,.5),SourceUnit(' second',.5,.5)]
        with self.assertRaises(InvalidProposal):
            CommitmentGate().observe(ready(1,'target'),units,2,observation=1,media_now=2)


if __name__ == '__main__':
    unittest.main()
