"""Guided project setup; no model call is needed to configure HiveMind."""
import argparse
import getpass
import json
from pathlib import Path
import sys

from .harnesses import HARNESS, install, inventory


def add_arguments(parser):
    parser.add_argument('--harness', action='append', choices=(*HARNESS, 'auto', 'all'),
                        help='Select clients; repeat, use auto to detect, or all to generate every adapter')
    parser.add_argument('--scope', choices=('project', 'user'), help='Default: project only. User-wide registration requires explicit --scope user')
    parser.add_argument('--learning', choices=('off', 'free', 'cheap'), help='Set learning for this project only')
    parser.add_argument('--model', help='OpenRouter primary model ID; free mode includes automatic free fallbacks')
    parser.add_argument('--daily-limit', type=int, help='Project request ceiling per rolling 24 hours (1-1000)')
    parser.add_argument('--daily-usd', type=float, help='Explicit project dollar ceiling for cheap mode')
    parser.add_argument('--prompt-price', type=float, help='Cheap mode maximum dollars per million input tokens')
    parser.add_argument('--completion-price', type=float, help='Cheap mode maximum dollars per million output tokens')
    parser.add_argument('--capture', action=argparse.BooleanOptionalAction, default=None,
                        help='Install native activity adapters when available')
    parser.add_argument('--manual-learning', action=argparse.BooleanOptionalAction, default=None,
                        help='Queue activity without automatic model requests')
    parser.add_argument('--set-key', action='store_true', help='Enter/replace a key privately in the terminal, never as an argument')


def _ask(input_fn, prompt, default=''):
    try:
        answer = input_fn(prompt + (f' [{default}]' if default != '' else '') + ': ').strip()
    except EOFError:
        raise ValueError('Setup input closed; rerun interactively or use --no-prompt with explicit choices') from None
    return answer or str(default)


def choose(root, project_id, args, *, interactive=None, input_fn=input, output=sys.stdout):
    from . import assisted
    from .onboarding import interactive_terminal
    interactive = interactive_terminal() if interactive is None else interactive
    cfg = assisted.config(root)
    saved = cfg.get('project_onboarding', {}).get(project_id, {})
    current = assisted.settings(root, project_id)
    detected = [row['id'] for row in inventory() if row['detected']]
    names = getattr(args, 'harness', None) or saved.get('harnesses', detected)
    if 'all' in names:
        names = list(HARNESS)
    elif 'auto' in names:
        names = detected
    mode = getattr(args, 'learning', None) or ('cheap' if current.get('mode') == 'cheap' else
            'free' if assisted.enabled(root, project_id) else 'off')
    # A prior user-scoped choice must never silently enable global activation.
    scope = getattr(args, 'scope', None) or 'project'
    model = getattr(args, 'model', None) or current['model']
    if mode == 'free' and not getattr(args, 'model', None) and model != 'openrouter/free' and not model.endswith(':free'):
        model = 'openrouter/free'
    limit = current['daily_limit'] if getattr(args, 'daily_limit', None) is None else args.daily_limit
    capture = getattr(args, 'capture', None)
    capture = saved.get('capture', mode != 'off') if capture is None else capture
    manual = getattr(args, 'manual_learning', None)
    manual = not current.get('auto_run', True) if manual is None else manual
    costs = {name: getattr(args, name, None) if getattr(args, name, None) is not None else current.get(name)
             for name in ('daily_usd', 'prompt_price', 'completion_price')}
    explicit = any(getattr(args, name, None) is not None for name in
                   ('harness', 'learning', 'model', 'scope', 'daily_limit', 'capture', 'manual_learning',
                    'daily_usd', 'prompt_price', 'completion_price'))
    prompt = interactive and not args.no_prompt and not args.dry_run and (args.configure or not saved) and not explicit
    purpose, taste = '', ''
    if prompt:
        print(f'\nHiveMind project setup: {project_id}', file=output)
        print('Shared memory and checkpoints work through MCP. Native capture varies by client.', file=output)
        print('Clients: ' + ', '.join(HARNESS), file=output)
        print('Detected: ' + (', '.join(detected) or 'none; choose a client or use manual MCP setup'), file=output)
        while True:
            selected = _ask(input_fn, 'Clients (comma separated, auto, all, or none)', ','.join(names) or 'none')
            if selected in {'auto', 'all', 'none'}:
                names = detected if selected == 'auto' else list(HARNESS) if selected == 'all' else []
                break
            names = list(dict.fromkeys(s.strip().lower() for s in selected.split(',')))
            if names and all(name in HARNESS for name in names):
                break
            print('Choose IDs from the list above.', file=output)
        print('Activation: this project only. Shared memory stays in the existing Hive folder.', file=output)
        print('Learning sends redacted activity to OpenRouter. AI taste/style suggestions wait for local review.', file=output)
        while True:
            mode = _ask(input_fn, 'Learning (off/free/cheap)', mode)
            if mode in {'off', 'free', 'cheap'}:
                break
        if mode != 'off':
            if 'capture' not in saved and getattr(args, 'capture', None) is None:
                capture = True
            if mode == 'free' and model != 'openrouter/free' and not model.endswith(':free'):
                model = 'openrouter/free'
            if mode == 'free':
                from .openrouter import FREE_MODELS, is_free_model
                print('Free models (automatic fallback; no paid models):', file=output)
                for number, (ident, label) in enumerate(FREE_MODELS, 1):
                    print(f'  {number}. {label} — {ident}', file=output)
                while True:
                    selected = _ask(input_fn, 'Primary model number or free model ID', model)
                    model = FREE_MODELS[int(selected) - 1][0] if selected.isdigit() and 1 <= int(selected) <= len(FREE_MODELS) else selected
                    if is_free_model(model):
                        break
                    print('Choose a listed number, openrouter/free or a model ID ending in :free.', file=output)
            else:
                model = _ask(input_fn, 'OpenRouter model ID',
                             current['model'] if current.get('mode') == 'cheap' else '')
            limit = int(_ask(input_fn, 'Maximum project requests per 24 hours (shared device cap also applies)', limit))
            if mode == 'cheap':
                for field, label in (('daily_usd', 'Maximum project dollars per 24 hours'),
                                     ('prompt_price', 'Maximum dollars per million input tokens'),
                                     ('completion_price', 'Maximum dollars per million output tokens')):
                    costs[field] = float(_ask(input_fn, label, costs[field] or ''))
            capture = _ask(input_fn, 'Install native activity capture where available? (yes/no)',
                           'yes' if capture else 'no').lower() in {'y', 'yes'}
            manual = _ask(input_fn, 'Model processing (auto/manual)', 'manual' if manual else 'auto') == 'manual'
        purpose = _ask(input_fn, 'Project purpose (optional; Enter to skip)')
        taste = _ask(input_fn, 'Working preference for this project (optional; Enter to skip)')
    if mode == 'off':
        capture = False
    if mode == 'cheap' and not getattr(args, 'model', None) and not prompt and current.get('mode') != 'cheap':
        raise ValueError('Cheap mode requires --model and explicit dollar/price limits')
    if args.set_key and (not interactive or args.dry_run or args.no_prompt):
        raise ValueError('--set-key requires an interactive terminal; keys are never accepted as command arguments')
    return {'harnesses': list(dict.fromkeys(names)), 'scope': scope, 'mode': mode, 'model': model,
            'daily_limit': limit, 'capture': capture, 'auto_run': not manual, **costs,
            'purpose': purpose, 'taste': taste,
            'ask_key': bool(args.set_key or (interactive and not args.no_prompt and not args.dry_run and mode != 'off')),
            'replace_key': bool(args.set_key),
            'persist': bool(saved or prompt or explicit), 'model_calls': 0}


def apply(root, project, project_id, choices, *, skip_register=False, secret_fn=getpass.getpass, output=sys.stdout):
    """Apply per-project choices; the API key lives only in private device storage."""
    from . import assisted
    from .credentials import key_status, save_key
    from .store import atomic_write
    if choices['ask_key']:
        credential = key_status(root)
        print('\nOpenRouter API key (shared by projects using this HiveMind installation)', file=output)
        if credential['available']:
            print('An existing key is available from ' + credential['source'] + '.', file=output)
        else:
            print('Enter a key to enable model learning, or skip it for now.', file=output)
        blank_action = 'keep the existing key' if credential['available'] else 'skip for now'
        # getpass falls back to echoed input on some terminals. Refuse that fallback.
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            key = secret_fn(f'OpenRouter API key (hidden; Enter to {blank_action}): ').strip()
        if key:
            save_key(root, key)
            if credential['source'] == 'environment':
                print('Key saved privately. OPENROUTER_API_KEY remains active; update or remove it to use the saved key.', file=output)
    policy = {key: choices[key] for key in ('mode', 'model', 'daily_limit', 'auto_run',
                                          'daily_usd', 'prompt_price', 'completion_price')}
    if choices['persist']:
        assisted.configure_project(root, project_id, **policy)
    results = install(root, project, project_id, choices['harnesses'], choices['scope'], choices['capture']) if not skip_register else {}
    if choices['persist']:
        cfg = assisted.config(root)
        cfg['project_onboarding'] = {**cfg.get('project_onboarding', {}), project_id:
            {key: choices[key] for key in ('harnesses', 'scope', 'capture')}}
        atomic_write(Path(root) / 'hive.local.json', json.dumps(cfg, indent=2) + '\n')
    from .store import Hive
    from .learning import learning_note
    hive = Hive(root)
    for field, kind in (('purpose', 'decision'), ('taste', 'preference')):
        if not choices[field]:
            continue
        path, text = learning_note(kind, 'onboarding-' + field, choices[field],
                                  'Explicit user answer during project setup', project=project_id, basis='user-stated')
        try:
            revision = hive.read_note(path)['revision']
        except FileNotFoundError:
            revision = 'new'
        hive.write_memory(path, text, revision)
    credential = key_status(root)
    print(f"  Learning: {choices['mode']} ({choices['model']}; project {choices['daily_limit']}/24h; "
          f"device {assisted.settings(root)['daily_limit']}/24h)", file=output)
    if choices['mode'] != 'off':
        policy = assisted.settings(root, project_id)
        if policy['free_only']:
            print('  Free fallback pool: ' + ', '.join(policy['model_pool']), file=output)
        print(f'  Retry policy: {assisted.MAX_ATTEMPTS} attempts total; 5s/10s backoff plus jitter; respects Retry-After.', file=output)
        print('  OpenRouter key: ' + credential['source'] + ('; activity remains queued until key is set' if not credential['available'] else ''), file=output)
        print('  AI progress is provisional. Taste/style and knowledge suggestions require local review.', file=output)
    return results
