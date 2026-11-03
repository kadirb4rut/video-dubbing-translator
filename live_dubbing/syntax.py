"""Optional cached CPU syntax guard; explicitly installed English model only.

This supplies explainable grammatical evidence, not semantic probabilities or
translation-quality scores. It never downloads a pipeline or uses a GPU.
"""
from __future__ import annotations

from collections import OrderedDict


class EnglishSyntaxGuard:
    def __init__(self, model='en_core_web_sm'):
        import spacy
        spacy.require_cpu()
        self.nlp = spacy.load(model, disable=['ner', 'lemmatizer'])
        self.model = model
        self.version = self.nlp.meta.get('version')
        self.cache = OrderedDict()

    def _parse(self, text):
        if text not in self.cache:
            self.cache[text] = self.nlp(text)
            if len(self.cache) > 128:
                self.cache.popitem(last=False)
        self.cache.move_to_end(text)
        return self.cache[text]

    @staticmethod
    def _predicate(root):
        auxiliaries = [child for child in root.children if child.dep_ in ('aux', 'auxpass', 'cop')]
        heads = [root, *auxiliaries]
        finite = any('Fin' in head.morph.get('VerbForm') for head in heads)
        subject = any(child.dep_ in ('nsubj', 'nsubjpass', 'csubj', 'csubjpass', 'expl')
                      for head in heads for child in head.children)
        subordinate = any(child.dep_ == 'mark' and child.lower_ in
                          ('if', 'unless', 'because', 'although', 'when', 'while', 'until')
                          for child in root.children)
        infinitive = any(child.lower_ == 'to' and child.dep_ in ('aux', 'mark')
                         for child in root.children)
        imperative = root.pos_ == 'VERB' and root.tag_ == 'VB' and not subordinate and not infinitive
        return not subordinate and ((finite and subject) or imperative)

    def incomplete_reasons(self, text):
        doc = self._parse(text)
        tokens = [token for token in doc if not token.is_punct and not token.is_space]
        if not tokens:
            return ('empty_phrase',)
        short = text.strip(' .!?').casefold() in (
            'yes', 'no', 'okay', 'ok', 'thanks', 'right', 'exactly', 'sure', 'interesting', 'why')
        reasons = []
        if not short and any(not self._predicate(sentence.root) and not self._nominal_utterance(sentence)
                             for sentence in doc.sents):
            reasons.append('no_independent_predicate')
        last = tokens[-1]
        # A short noisy phrase can make the parser label a degree adverb as
        # the noun object ("including a very."). It still lacks its modifier.
        if last.lower_ in ('very', 'extremely', 'quite', 'rather'):
            reasons.append('unfinished_degree_modifier')
        if any(token.lower_ == 'where' and token.pos_ == 'SCONJ'
               and token.head.i < token.i
               and not any(part.pos_ in ('VERB', 'AUX') for part in doc[token.i+1:])
               for token in tokens):
            # The real "times ... where, frankly," still needs its relative
            # clause; an intervening discourse adverb cannot close it.
            reasons.append('missing_relative_clause_after_where')
        if last.pos_ in ('ADP', 'PART', 'DET', 'CCONJ', 'SCONJ') and last.dep_ != 'prt':
            reasons.append('unfinished_function_word')
        if any(token.pos_ == 'DET' and token.head.pos_ in ('ADJ', 'ADV') and token.head.i == last.i
               for token in tokens):
            reasons.append('determiner_without_nominal_head')
        object_predicate = (last.dep_ == 'ccomp' and
            any(child.dep_ in ('nsubj', 'nsubjpass') for child in last.children))
        if last.pos_ == 'ADJ' and last.dep_ not in ('acomp', 'attr', 'ROOT') and not object_predicate:
            reasons.append('unfinished_modifier')
        if last.pos_ == 'VERB' and last.lower_ in ('make', 'makes', 'making'):
            children = tuple(last.children)
            complement = any(child.dep_ in ('dobj', 'obj', 'ccomp', 'xcomp', 'attr')
                             for child in children)
            # A relative object can be implicit: "the cake they can make".
            # In the real cutoff "the type that can make", "that" is the
            # subject, so no object/complement exists even if ASR adds a dot.
            relative_object = (last.dep_ == 'relcl' and
                not any(child.dep_ in ('nsubj', 'nsubjpass') and
                        child.lower_ in ('that', 'who', 'which') for child in children))
            if not complement and not relative_object:
                reasons.append('missing_complement_after_make')
        return tuple(reasons)

    def _nominal_utterance(self, sentence):
        """Spoken headings and complete noun definitions need no matrix verb.

        This is grammatical evidence only. Existing punctuation, LocalAgreement,
        following-attachment and unfinished-word checks still gate commitment.
        """
        root = sentence.root
        tokens = [token for token in sentence if not token.is_punct and not token.is_space]
        if root.pos_ not in ('NOUN', 'PROPN', 'NUM') or not tokens:
            return False
        # Real live speech: "Item number two. Tents. Tents." Do not admit
        # prepositional fragments such as "all the way to San Francisco".
        if len(tokens) <= 5 and all(token.pos_ in ('NOUN', 'PROPN', 'NUM', 'ADJ') for token in tokens):
            return True
        # A spoken list can elaborate a nominal heading without a finite
        # matrix verb. Require a real object of "including", closed sentence
        # punctuation and no other unfinished predicate. The ordinary final
        # modifier/function-word checks still apply below the caller.
        if sentence.text.rstrip().endswith(('.', '!', '?')):
            elaborations = [token for token in tokens if token.lower_ == 'including'
                            and token.dep_ == 'prep' and token.head == root]
            if (elaborations and any(child.dep_ in ('pobj', 'obj')
                                     for token in elaborations for child in token.children)
                    and all(token.pos_ not in ('VERB', 'AUX') or token in elaborations
                            for token in tokens)):
                return True
        # "The kind of things that people ... leave behind." A complete
        # finite relative can define the nominal subject of a spoken fragment.
        return any(token.dep_ == 'relcl' and self._predicate(token) for token in tokens)

    def complete(self, words):
        return not self.incomplete_reasons(' '.join(word.text.strip() for word in words))

    @staticmethod
    def _parenthetical(token):
        return (token.dep_ == 'parataxis' and token.lower_ in ('know', 'see', 'mean')
                and any(child.dep_ == 'nsubj' and child.lower_ in ('you', 'i') for child in token.children)
                and not any(child.dep_ in ('dobj', 'obj', 'ccomp', 'xcomp', 'attr') for child in token.children))

    def boundary_reasons(self, prefix, following):
        if not prefix or len(prefix) >= len(following):
            return ()
        # ASR punctuation may be provisional. Check a following attachment
        # without allowing that period to force the parser to start anew.
        left = ' '.join(word.text.strip() for word in prefix).rstrip('.!?')
        right = ' '.join(word.text.strip() for word in following[len(prefix):])
        boundary = len(left)+1
        doc = self._parse(left+' '+right)
        tail = [token for token in doc if token.idx >= boundary and not token.is_punct]
        first = tuple(token.lower_ for token in tail[:2])
        if any(token.dep_ in ('relcl', 'acl') and token.head.idx < boundary
               for token in tail[:12]):
            return ('relative_clause_continues_phrase',)
        # In the real lecture, spaCy tags the first "as" in "as well as"
        # ADV, so the ADP attachment check alone missed the enumeration.
        if first in (('as', 'well'), ('such', 'as')) or (tail and tail[0].lower_ == 'including'):
            return ('additive_enumeration_continues_phrase',)
        # A revised ASR period must not strand a measured qualifier such as
        # "even shorter, maybe like 70 days". A new finite assertion ("Maybe
        # 70 people will attend") remains a separate sentence.
        if (tail and tail[0].lower_ in ('maybe', 'perhaps', 'about', 'around',
                                       'roughly', 'approximately', 'like')
                and any(token.like_num for token in tail[:6])
                and any(head.idx < boundary for head in tail[0].ancestors)
                and not any(token.idx >= boundary and self._predicate(token)
                            for token in tail if token.pos_ in ('VERB', 'AUX'))):
            return ('numeric_qualifier_continues_phrase',)
        if tail and self._parenthetical(tail[0].head):
            return ('parenthetical_disfluency_continues_phrase',)
        if (tail and tail[0].pos_ in ('ADP', 'PART') and
                any(head.idx < boundary for head in tail[0].ancestors)):
            return ('dependent_continuation_after_punctuation',)
        if tail and tail[0].pos_ in ('AUX', 'VERB'):
            predicate = tail[0].head if tail[0].dep_ in ('aux', 'auxpass', 'cop') else tail[0]
            own_subject = any(child.dep_ in ('nsubj', 'nsubjpass', 'csubj', 'expl')
                              and child.idx >= boundary for child in predicate.children)
            # Real ASR inserted a period before "is doubling rapidly", leaving
            # that predicate behind after translating its nominal subject.
            # Keep a backward-attached predicate; a new question such as
            # "Is that clear?" has its own subject and remains independent.
            if not own_subject and any(head.idx < boundary for head in tail[0].ancestors):
                return ('predicate_continuation_after_punctuation',)
        return ()

    def clause_boundaries(self, words):
        text = ' '.join(word.text.strip() for word in words)
        doc = self._parse(text)
        offsets, position = [], 0
        for word in words:
            offsets.append(position)
            position += len(word.text.strip()) + 1
        boundaries = set()
        for token in doc:
            if token.dep_ not in ('conj', 'parataxis') or not self._predicate(token):
                continue
            if self._parenthetical(token):
                continue
            # A shared-subject coordination ("I'll share ... and then hand
            # it over") must not become a new imperative in translation.
            heads = [token, *[child for child in token.children if child.dep_ in ('aux', 'auxpass', 'cop')]]
            if not any(child.dep_ in ('nsubj', 'nsubjpass', 'csubj', 'csubjpass', 'expl')
                       for head in heads for child in head.children):
                continue
            if any(parent.dep_ in ('relcl', 'acl', 'advcl', 'ccomp', 'xcomp') for parent in token.ancestors):
                continue
            first = min(item.i for item in token.subtree)
            while first and (doc[first-1].pos_ in ('PUNCT', 'CCONJ', 'INTJ') or doc[first-1].lower_ == 'then'):
                first -= 1
            count = sum(offset < doc[first].idx for offset in offsets)
            if 1 < count < len(words) and self.complete(words[:count]):
                boundaries.add(count)
        return boundaries

    def topic_boundaries(self, words):
        """Recognize an observed repeated heading introducing an elaboration.

        Rolling ASR can remove the stop in "...using building blocks. Building
        blocks, including ...". The repeated noun phrase supplies the topic;
        no words are inferred, deleted or rewritten to manufacture a boundary.
        """
        text = ' '.join(word.text.strip() for word in words)
        doc = self._parse(text)
        offsets, position = [], 0
        for word in words:
            offsets.append(position)
            position += len(word.text.strip()) + 1
        boundaries = set()
        for chunk in doc.noun_chunks:
            if not 2 <= len(chunk) <= 6 or chunk.root.pos_ not in ('NOUN', 'PROPN'):
                continue
            after = [token for token in doc[chunk.end:] if not token.is_punct][:2]
            if not after or after[0].lower_ not in ('include', 'includes', 'including'):
                continue
            before = [token.lower_ for token in doc[:chunk.start] if not token.is_punct]
            repeated = [token.lower_ for token in chunk]
            if before[-len(repeated):] != repeated:
                continue
            count = sum(offset < chunk.start_char for offset in offsets)
            if (1 < count < len(words) and self.complete(words[:count])
                    and not self.boundary_reasons(words[:count], words)):
                boundaries.add(count)
        return boundaries

    def close(self):
        self.cache.clear()
        self.nlp = None
