"""A compact deterministic handoff. Reading it never invokes a model or agent."""
import json
import math
import sqlite3

from .context import validate_project
from .sessions import project_path


POLICY = 'Checkpoint checks are historical agent reports. Recent activity and AI summaries are unverified. Inspect Git drift before continuing.'


def build(hive, project, budget_tokens=1200):
    validate_project(project, required=True)
    if type(budget_tokens) is not int or not 512 <= budget_tokens <= 8192:
        raise ValueError('Handoff budget must be 512-8192 estimated tokens')
    folder = project_path(hive.root, project)
    if folder is None:
        raise ValueError('Enroll this project before requesting a handoff')
    packet = hive.session_resume(project)
    current = packet['session']
    source = current
    if packet.get('previous_handoff'):
        source = hive.session_resume(project, packet['previous_handoff']['id'])['session']
    # A newly started or wrapper-only session can describe an interruption, but
    # never replaces the substantive checkpoint that preceded it.
    meaningful = source and (source['checkpoint']['summary'] != 'Session started; no verified handoff recorded yet.' or
                              any(source['checkpoint'][key] for key in ('completed', 'changed_files', 'verification')))
    if not meaningful:
        source = None
    result = {'project': project, 'directory': str(folder), 'goal': current['goal'] if current else None,
              'checkpoint': None, 'recent_activity': [], 'derived_progress': None,
              'evidence_policy': POLICY,
              'budget': {'requested_tokens': budget_tokens, 'estimated_tokens': 0, 'omitted_items': 0,
                         'method': 'UTF-8 JSON bytes / 4; not an exact model tokenizer'}}
    if source:
        cp = source['checkpoint']
        result['checkpoint'] = {'session': source['id'], 'agent': source['agent'], 'revision': source['revision'],
            'at': source['checkpoint_at'], 'summary': cp['summary'], 'status': cp['status'],
            'completed': list(cp['completed']), 'changed_files': list(cp['changed_files']),
            'verification': list(cp['verification']), 'blockers': list(cp['blockers']), 'next_steps': list(cp['next_steps']),
            'git_drift': source['git_drift']['status'], 'workspace': source['git'].get('workspace'),
            'head': source['git_drift'].get('current_head', source['git'].get('head')),
            'branch': source['git_drift'].get('current_branch', source['git'].get('branch')),
            'path': source['path']}
    if current and source and current['id'] != source['id']:
        result['current_session'] = {'id': current['id'], 'status': current['status'],
                                     'blockers': current['checkpoint']['blockers'][:2]}
    from .assisted import recent, latest
    after = source.get('checkpoint_timestamp', 0) if source else 0
    # Recorder failure does not make the authoritative checkpoint unavailable.
    try:
        result['recent_activity'] = recent(hive, project, after_timestamp=after)
        derived = latest(hive, project, after_timestamp=after)
        if derived:
            result['derived_progress'] = {key: derived[key] for key in ('native_session', 'summary', 'through_event', 'created')}
            result['derived_progress']['unverified'] = True
    except (OSError, ValueError, sqlite3.Error) as exc:
        result['recorder_warning'] = 'Local activity unavailable: ' + type(exc).__name__
    cap = budget_tokens * 4
    def size():
        return max(len(json.dumps(result, ensure_ascii=False, indent=2).encode()), len(markdown(result).encode()))
    # Keep blockers and the next action ahead of redundant history and prose.
    while size() > cap - 32:
        if result['derived_progress']:
            result['derived_progress'] = None
        elif len(result['recent_activity']) > 1:
            index = next((i for i, event in enumerate(result['recent_activity']) if event['role'] == 'tool'),
                         next((i for i, event in enumerate(result['recent_activity']) if event['role'] != 'user'), 0))
            result['recent_activity'].pop(index)
        elif result['recent_activity'] and len(result['recent_activity'][0]['text']) > 180:
            result['recent_activity'][0]['text'] = result['recent_activity'][0]['text'][:160] + ' [shortened]'
        elif source and any(result['checkpoint'][key] for key in ('completed', 'changed_files', 'verification')):
            key = next(key for key in ('completed', 'changed_files', 'verification') if result['checkpoint'][key])
            result['checkpoint'][key].pop()
        elif source and len(result['checkpoint']['next_steps']) > 1:
            result['checkpoint']['next_steps'].pop()
        elif source and len(result['checkpoint']['blockers']) > 1:
            result['checkpoint']['blockers'].pop()
        elif source and len(result['checkpoint']['summary']) > 180:
            result['checkpoint']['summary'] = result['checkpoint']['summary'][:160] + ' [shortened]'
        elif result['recent_activity']:
            result['recent_activity'].pop()
        elif result.get('current_session'):
            del result['current_session']
        elif result.get('goal') and len(result['goal']) > 80:
            result['goal'] = result['goal'][:60] + ' [shortened]'
        elif source and any(result['checkpoint'].get(key) for key in ('head', 'branch', 'workspace')):
            for key in ('head', 'branch', 'workspace'):
                result['checkpoint'].pop(key, None)
        else:
            # A 512-token budget cannot fit arbitrary maximum-length identifiers
            # plus an uncut action/blocker. Ask for a larger brief instead of
            # silently cutting the critical action or returning an oversized one.
            raise ValueError('Handoff metadata and critical actions exceed this budget; request a larger --budget')
        result['budget']['omitted_items'] += 1
    for _ in range(3):
        result['budget']['estimated_tokens'] = math.ceil(size() / 4)
    return result


def markdown(result):
    lines = [f"# Handoff: {result['project']}", '', f"Project directory: {result['directory']}"]
    if result['goal']:
        lines += ['', 'Current goal: ' + result['goal']]
    current = result.get('current_session')
    if current:
        lines += ['', 'Current session status: ' + current['status']]
        if current['blockers']:
            lines += ['Current blockers:', *('- ' + item.replace('\n', '\n  ') for item in current['blockers'])]
    cp = result['checkpoint']
    if cp:
        lines += ['', f"Last checkpoint: {cp['agent']} / {cp['session']} r{cp['revision']} ({cp['at']})",
                  f"Reported status: {cp['status']}. Git drift: {cp['git_drift']}."]
        if cp['git_drift'] != 'match':
            lines += ['Inspect the live working tree before relying on this checkpoint.']
        lines += ['', *('> ' + line for line in cp['summary'].splitlines())]
        for key, title in (('blockers', 'Blockers'), ('next_steps', 'Next actions'), ('verification', 'Reported checks'),
                           ('changed_files', 'Reported changed files'), ('completed', 'Reported completed work')):
            if cp[key]:
                lines += ['', title + ':', *('- ' + value.replace('\n', '\n  ') for value in cp[key])]
        lines += ['', 'Full checkpoint: ' + cp['path']]
    else:
        lines += ['', 'No substantive agent checkpoint is available. Inspect the working tree and activity before continuing.']
    if result['recent_activity']:
        lines += ['', 'Activity after the checkpoint (unverified):']
        for event in result['recent_activity']:
            lines += [f"Event {event['id']} / {event['agent']} / {event['role']}:",
                      *('> ' + line for line in event['text'].splitlines())]
    if result['derived_progress']:
        lines += ['', 'AI progress (unverified): ' + result['derived_progress']['summary']]
    if result.get('recorder_warning'):
        lines += ['', result['recorder_warning']]
    lines += ['', POLICY, f"Omitted items: {result['budget']['omitted_items']}. Use a larger --budget or resume the full checkpoint for details."]
    return '\n'.join(lines) + '\n'
