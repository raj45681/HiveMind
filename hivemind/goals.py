"""Reviewable task graphs and deterministic, bounded execution via existing workers."""
import asyncio
import json
import os
import shutil
import socket
import time
import uuid

from .models import GoalSpec, GoalNode, TaskSpec, ROUTES
from .memory import file_paths
from .store import utc


def create(hive, spec):
    goal = GoalSpec.model_validate(spec)
    if not goal.tasks:
        duration = max(30, min(900, goal.max_seconds // 3))
        goal.tasks = [GoalNode(key='plan', title='Plan: ' + goal.title[:140], objective=goal.objective,
                               project=goal.project, agent=goal.agent, kind='analysis', access='read',
                               acceptance=['Identify affected files, relevant decisions, and a concrete implementation plan'], max_seconds=duration),
                      GoalNode(key='implement', title='Implement: ' + goal.title[:140], objective=goal.objective,
                               project=goal.project, agent=goal.agent, kind='implementation', access='write', depends_on=['plan'],
                               acceptance=['Implement the goal and report concrete verification evidence'], max_seconds=duration),
                      GoalNode(key='review', title='Review: ' + goal.title[:140], objective='Review the completed change: ' + goal.objective[:3900],
                               project=goal.project, agent=goal.agent, kind='review', access='read', depends_on=['implement'],
                               acceptance=['Review the integrated change and identify unresolved issues'], max_seconds=duration)]
    if len(goal.tasks) > goal.max_tasks:
        raise ValueError('Execution task budget is smaller than the proposed graph')
    for node in goal.tasks:
        node.project = goal.project
        node.files = file_paths(node.files)
        for name in node.memory:
            hive.note_path(name)
    payload = goal.model_dump_json()
    with hive.connect(write=True) as c:
        row = c.execute('SELECT * FROM goals WHERE project=? AND key=?', (goal.project, goal.key)).fetchone()
        if row:
            if row['spec'] != payload:
                raise ValueError('Goal key already exists with different contents; choose a new key')
            ident = row['id']
        else:
            ident = 'GOAL-' + uuid.uuid4().hex[:16]
            c.execute('INSERT INTO goals(id,project,key,spec,status) VALUES (?,?,?,?,?)',
                      (ident, goal.project, goal.key, payload, 'draft'))
        hive._export(c)
    return publish(hive, ident)


def row_for(c, ident):
    row = c.execute('SELECT * FROM goals WHERE id=?', (ident,)).fetchone()
    if not row:
        raise ValueError('Goal not found')
    return row


def activate(hive, ident, expected_revision):
    with hive.connect(write=True) as c:
        row = row_for(c, ident)
        if row['revision'] != expected_revision or row['status'] != 'draft':
            raise ValueError('Read the current draft before activating it')
        spec = json.loads(row['spec'])
        mapping = {node['key']: 'TASK-' + uuid.uuid4().hex[:12] for node in spec['tasks']}
        for node in spec['tasks']:
            data = {key: value for key, value in node.items() if key != 'key'}
            data['depends_on'] = [mapping[key] for key in node['depends_on']]
            task = TaskSpec.model_validate(data)
            if any(len(item) > 800 for item in task.acceptance):
                raise ValueError('Keep acceptance entries within 800 characters')
            c.execute("INSERT INTO tasks(id,spec,agent,status,created,updated) VALUES(?,?,?,'pending',?,?)",
                      (mapping[node['key']], task.model_dump_json(), task.agent or ROUTES[task.kind], utc(), utc()))
            hive._event(c, mapping[node['key']], 'created', 'goal ' + ident)
        c.execute("UPDATE goals SET status='active',revision=revision+1,task_map=? WHERE id=?", (json.dumps(mapping), ident))
        hive._export(c)
    return publish(hive, ident)


def status(hive, ident):
    with hive.connect() as c:
        row = row_for(c, ident)
        goal = json.loads(row['spec'])
        mapping, integrated = json.loads(row['task_map']), json.loads(row['integrated'])
        nodes = []
        for node in goal['tasks']:
            task = hive._decode(c.execute('SELECT * FROM tasks WHERE id=?', (mapping[node['key']],)).fetchone()) if mapping else None
            nodes.append({'key': node['key'], 'task': mapping.get(node['key']), 'title': node['title'],
                          'agent': node['agent'] or ROUTES[node['kind']], 'access': node['access'],
                          'depends_on': node['depends_on'], 'status': task['status'] if task else 'draft',
                          'integration_recorded': node['key'] in integrated})
        by_key = {node['key']: node for node in nodes}
        for node in nodes:
            if node['status'] != 'pending':
                continue
            dependencies = [by_key[key] for key in node['depends_on']]
            if any(dep['status'] in {'failed', 'blocked'} for dep in dependencies):
                node['status'] = 'dependency_blocked'
            elif all(dep['status'] == 'done' for dep in dependencies):
                if any(dep['access'] == 'write' and not dep['integration_recorded'] for dep in dependencies):
                    node['status'] = 'awaiting_integration'
                else:
                    node['status'] = 'ready'
        all_done = nodes and all(node['status'] == 'done' for node in nodes)
        writes_pending = any(node['access'] == 'write' and not node['integration_recorded'] for node in nodes)
        state = 'awaiting_integration' if all_done and writes_pending else 'completed' if all_done else row['status']
        graph = ['flowchart TD']
        for index, node in enumerate(nodes):
            graph.append(f'  n{index}["{node["key"]}: {node["status"]}"]')
        indices = {node['key']: index for index, node in enumerate(nodes)}
        for node in nodes:
            for dep in node['depends_on']:
                graph.append(f'  n{indices[dep]} --> n{indices[node["key"]]}')
        return {'id': ident, 'project': goal['project'], 'title': goal['title'], 'status': state, 'revision': row['revision'],
                'nodes': nodes, 'plan': goal if row['status'] == 'draft' else None, 'mermaid': '\n'.join(graph),
                'limits': {key: goal[key] for key in ('max_tasks', 'max_seconds', 'max_parallel')},
                'usage': {'launches': row['launches'], 'seconds': round(row['seconds'], 2)},
                'runner': 'active' if row['runner'] and row['runner_until'] > time.time() else 'stale' if row['runner'] else None}


def integrate(hive, ident, key, expected_revision, source):
    if not source.strip() or len(source) > 500:
        raise ValueError('Record how the completed write was reviewed and integrated')
    with hive.connect(write=True) as c:
        row = row_for(c, ident)
        if row['revision'] != expected_revision:
            raise ValueError('Goal changed; read the latest revision')
        mapping = json.loads(row['task_map'])
        if key not in mapping:
            raise ValueError('Unknown goal node')
        task = hive._decode(c.execute('SELECT * FROM tasks WHERE id=?', (mapping[key],)).fetchone())
        if task['status'] != 'done' or task['spec']['access'] != 'write':
            raise ValueError('Only completed write tasks can be marked integrated')
        recorded = json.loads(row['integrated'])
        recorded[key] = {'source': source, 'recorded': utc()}
        c.execute('UPDATE goals SET integrated=?,revision=revision+1 WHERE id=?', (json.dumps(recorded), ident))
        hive._export(c)
    return publish(hive, ident)


def claim_ready(c, task_id):
    for row in c.execute("SELECT task_map,integrated FROM goals WHERE status='active'"):
        mapping = json.loads(row['task_map'])
        if task_id not in mapping.values():
            continue
        integrated = json.loads(row['integrated'])
        task = c.execute('SELECT spec FROM tasks WHERE id=?', (task_id,)).fetchone()
        for dependency in json.loads(task['spec'])['depends_on']:
            prior = c.execute('SELECT spec FROM tasks WHERE id=?', (dependency,)).fetchone()
            key = next((key for key, ident in mapping.items() if ident == dependency), '')
            if json.loads(prior['spec'])['access'] == 'write' and key not in integrated:
                return False
    return True


def release(hive, ident, expected_revision, source):
    if not source.strip() or len(source) > 500:
        raise ValueError('Record why the interrupted runner can be released')
    with hive.connect(write=True) as c:
        row = row_for(c, ident)
        if row['revision'] != expected_revision or not row['runner'] or row['runner_until'] > time.time():
            raise ValueError('Only a reviewed stale runner can be released at its current revision')
        for task in json.loads(row['task_map']).values():
            current = c.execute('SELECT status,lease FROM tasks WHERE id=?', (task,)).fetchone()
            if current['status'] == 'running' and current['lease'] > time.time():
                raise ValueError('A worker still has a valid lease')
        hive._expire(c)
        hive._event(c, ident, 'goal_runner_released', source)
        c.execute('UPDATE goals SET runner=NULL,runner_until=NULL,revision=revision+1 WHERE id=?', (ident,))
    return publish(hive, ident)


async def run(hive, ident, config, execute=False, max_tasks=None, max_seconds=None, max_parallel=None):
    for value in (max_tasks, max_seconds, max_parallel):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
            raise ValueError('Execution overrides must be positive integers')
    current = status(hive, ident)
    if not execute:
        return {**current, 'will_launch_model': False, 'note': 'Activate a reviewed draft, then pass --execute to use agent accounts.'}
    if current['status'] not in {'active', 'completed', 'awaiting_integration'}:
        raise ValueError('Activate the reviewed goal before executing it')
    if current['runner']:
        raise ValueError('A goal runner is active or stale; inspect it before starting another')
    limits = current['limits']
    count = min(max_tasks or limits['max_tasks'], limits['max_tasks'] - current['usage']['launches'])
    seconds = min(max_seconds or limits['max_seconds'], limits['max_seconds'] - current['usage']['seconds'])
    parallel = min(max_parallel or limits['max_parallel'], limits['max_parallel'])
    if count <= 0 or seconds < 1 or parallel < 1:
        return {**current, 'stopped': 'execution_budget_exhausted'}
    owner = socket.gethostname() + ':' + str(os.getpid()) + ':' + uuid.uuid4().hex[:8]
    with hive.connect(write=True) as c:
        row = row_for(c, ident)
        if row['runner']:
            raise ValueError('Another goal runner acquired ownership')
        count = min(max_tasks or limits['max_tasks'], limits['max_tasks'] - row['launches'])
        seconds = min(max_seconds or limits['max_seconds'], limits['max_seconds'] - row['seconds'])
        if count <= 0 or seconds < 1:
            return {**current, 'stopped': 'execution_budget_exhausted'}
        # Reserve the entire time allowance before dispatch. An abruptly lost
        # controller retains its reservation instead of silently resetting usage.
        c.execute('UPDATE goals SET runner=?,runner_until=?,seconds=seconds+? WHERE id=?',
                  (owner, time.time() + seconds + 10, seconds, ident))
    from .transport import Local
    from .worker import run_task
    api, running, results = Local(hive.root), set(), []
    start, launched, stopped, attempted = time.monotonic(), 0, 'no_ready_tasks', set()
    try:
        while time.monotonic() - start < seconds:
            current = status(hive, ident)
            ready = [node for node in current['nodes'] if node['status'] == 'ready']
            busy = {getattr(item, '_hive_task', '') for item in running}
            for node in ready:
                if node['task'] in busy or node['task'] in attempted or len(running) >= parallel or launched >= count:
                    continue
                task = hive.get_task(node['task'])
                agent = node['agent']
                if agent not in {'codex', 'grok', 'antigravity'} or not (config.get('agents', {}).get(agent) or shutil.which({'codex': 'codex', 'grok': 'grok', 'antigravity': 'agy'}[agent])):
                    stopped = 'adapter_unavailable:' + agent
                    continue
                if task['spec']['machine'] not in {'any', config.get('machine', socket.gethostname())}:
                    stopped = 'assigned_to_another_machine'
                    continue
                remaining = max(1, seconds - (time.monotonic() - start))
                with hive.connect(write=True) as c:
                    c.execute('UPDATE goals SET launches=launches+1 WHERE id=? AND runner=?', (ident, owner))
                future = asyncio.create_task(asyncio.wait_for(run_task(hive.root, api, node['task'], config), timeout=remaining))
                future._hive_task = node['task']
                running.add(future)
                attempted.add(node['task'])
                launched += 1
            if not running:
                stopped = 'task_budget_exhausted' if launched >= count else stopped
                break
            finished, running = await asyncio.wait(running, timeout=max(.01, seconds - (time.monotonic() - start)), return_when=asyncio.FIRST_COMPLETED)
            for future in finished:
                try:
                    results.append(future.result())
                except Exception as exc:
                    results.append({'task': future._hive_task, 'error': type(exc).__name__})
            if not finished:
                stopped = 'time_budget_exhausted'
                break
    finally:
        for future in running:
            future.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)
        with hive.connect(write=True) as c:
            c.execute('UPDATE goals SET seconds=seconds+?,runner=NULL,runner_until=NULL WHERE id=? AND runner=?',
                      (time.monotonic() - start - seconds, ident, owner))
    return {**publish(hive, ident), 'results': results, 'stopped': stopped, 'launched_this_run': launched}


def publish(hive, ident):
    """Persist a generated Obsidian view only during explicit state changes."""
    from .store import atomic_write
    view = status(hive, ident)
    lines = ['# ' + view['title'], '', f"Goal: {ident}", f"Status: {view['status']}", '',
             '[[Home|Task overview]] · [[Review|Memory review]]', '', '```mermaid', view['mermaid'], '```', '',
             '## Tasks', '']
    for node in view['nodes']:
        name = f"[[04-Tasks/{node['task']}|{node['key']}]]" if node['task'] else node['key']
        lines.append(f"- {name}: {node['status']} ({node['agent']}, {node['access']})")
    limits = view['limits']
    lines += ['', '## Execution limits', '',
              f"At most {limits['max_tasks']} launches and {limits['max_seconds']} seconds across goal runs; "
              f"at most {limits['max_parallel']} workers at once.",
              f"Recorded launches: {view['usage']['launches']}; seconds: {view['usage']['seconds']}", '',
              'Creation and activation do not start agent sessions. Run with --execute only when you intend to use agent accounts. '
              'Completed write tasks stay in their execution worktrees until reviewed and integrated. '
              'Dependencies on those writes wait for an explicit goal-integrated record.', '']
    if view['plan']:
        lines += ['## Proposed plan', '', '```json', json.dumps(view['plan'], ensure_ascii=False, indent=2), '```', '']
    atomic_write(hive.vault / '04-Tasks' / 'Goals' / (ident + '.md'), '\n'.join(lines))
    view['path'] = '04-Tasks/Goals/' + ident + '.md'
    return view
