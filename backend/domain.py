"""Fixed, fail-closed enterprise-domain boundary; never configured by a model.

The first slice uses a bounded task taxonomy, authorized source verification and
an output gate. Unknown intents are clarified instead of opening a general chat.
This is not a semantic accuracy claim for all natural-language inputs.
"""
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

POLICY = json.loads((Path(__file__).resolve().parent.parent / 'policy/domain.json').read_text('utf-8'))
VERSION = POLICY['version']
REFUSAL = POLICY['refusal']
CLARIFICATION = POLICY['clarification']
VALIDATION_FAILURE = POLICY['validation_failure']


def normalize(text):
    text = unicodedata.normalize('NFKC', text).casefold()
    text = ''.join(c for c in text if unicodedata.category(c) != 'Cf')
    return re.sub(r'\s+', ' ', text).strip()


def matches(text, patterns):
    normalized = normalize(text)
    compact = normalized.replace(' ', '')
    return any(re.search(p, normalized, re.I) or re.search(p, compact, re.I) for p in patterns)


def categories(text):
    text = normalize(text)
    return tuple(k for k, terms in POLICY['categories'].items() if any(term in text for term in terms))


@dataclass(frozen=True)
class Decision:
    status: str
    question: str = ''
    categories: tuple = ()
    mixed: bool = False
    help_topic: str = ''
    reason: str = ''

    @property
    def allowed(self):
        return self.status == 'allowed'

    def public(self):
        return {'status': self.status, 'effective_question': self.question,
                'mixed': self.mixed, 'reason': self.reason, 'policy_version': VERSION}


def assess(question):
    if not isinstance(question, str) or not normalize(question):
        return Decision('clarify', reason='missing_business_context')
    if matches(question, POLICY['injection_patterns']):
        return Decision('out_of_scope', reason='policy_override')
    # Split independently requested tasks; never forward rejected parts upstream.
    parts = [p.strip(' ,，:：') for p in re.split(
        r'[?？。;；\n]+|(?:另外|顺便|同时|以及|并且|再帮我|然后|和(?=宇宙|黑洞|娱乐|天气|讲|写))', question) if p.strip(' ,，:：')]
    accepted, labels, blocked, uncertain, help_topic = [], set(), False, False, ''
    for part in parts:
        if matches(part, POLICY['outside_patterns']):
            blocked = True
            continue
        found = categories(part)
        if not found:
            uncertain = True
            continue
        normalized = normalize(part)
        if any(word in normalized for word in POLICY['system_patterns']):
            topic = next((k for k, v in POLICY['help_topics'].items()
                          if any(term in normalized for term in v['terms'])), '')
            if topic:
                # Only a single help topic is handled by this small initial slice.
                if accepted and help_topic != topic:
                    return Decision('clarify', reason='multiple_system_tasks')
                help_topic = topic
        accepted.append(part)
        labels.update(found)
    if not accepted:
        return Decision('out_of_scope' if blocked else 'clarify', reason='outside_domain' if blocked else 'missing_business_context')
    if uncertain:
        # An unrecognized subtask must not be silently converted into an answer.
        return Decision('clarify', reason='ambiguous_subtask')
    return Decision('allowed', '？'.join(accepted) + '？', tuple(sorted(labels)), blocked, help_topic, 'business_task')


def safe_assess(question):
    try:
        return assess(question)
    except Exception:
        return Decision('clarify', reason='classification_unavailable')


def help_answer(decision):
    if decision.help_topic:
        return POLICY['help_topics'][decision.help_topic]['answer']
    return ''


def safe_source(decision, text):
    if not decision.allowed or not isinstance(text, str):
        return False
    if matches(text, POLICY['injection_patterns']) or matches(text, POLICY['outside_patterns']):
        return False
    return bool(set(categories(text)) & set(decision.categories))


def filter_evidence(decision, evidence):
    return [c for c in evidence if safe_source(decision, c.get('text', ''))]


def output_allowed(decision, answer, citations):
    if not decision.allowed or not isinstance(answer, str):
        return False
    body = answer.removesuffix('\n\n' + REFUSAL)
    if help_answer(decision) and body == help_answer(decision):
        return not citations
    if matches(body, POLICY['injection_patterns']) or matches(body, POLICY['outside_patterns']):
        return False
    if not citations:
        return False
    for c in citations:
        quote = c.get('quote', '')
        if not isinstance(quote, str) or not quote or quote not in c.get('text', ''):
            return False
        if not safe_source(decision, quote) or quote not in body:
            return False
    # This version publishes verified excerpts, never arbitrary model prose.
    remaining = body
    for c in sorted(citations, key=lambda c: len(c['quote']), reverse=True):
        remaining = remaining.replace(f'[{c.get("source_id", "")}] {c["quote"]}', '')
    remaining = re.sub(r'^资料中“[^\n]{1,80}”存在不同规定，当前证据无法确定哪份优先。请向制度负责人确认适用版本。', '', remaining)
    for prefix in ('本地证据摘录（未调用生成模型）：', '根据已授权资料，以下原文直接提供依据：',
                   '来源存在冲突，无法确定哪份优先，请确认适用版本。'):
        remaining = remaining.replace(prefix, '')
    return not remaining.strip()


def safe_output_allowed(decision, answer, citations):
    try:
        return output_allowed(decision, answer, citations)
    except Exception:
        return False


def status_answer_allowed(status, answer):
    # Error and progress states cannot carry arbitrary legacy/model prose.
    if status in {'queued', 'retrieving', 'waiting_quota', 'generating'}:
        return answer == ''
    if answer in {CLARIFICATION, VALIDATION_FAILURE}:
        return status in {'clarify', 'validation_failed'}
    return answer in POLICY['status_messages'].get(status, [])
