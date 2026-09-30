"""Bounded task/dependency and file-linked memory selection without inference."""
import hashlib
import json
from pathlib import Path

from .memory import file_paths, eligible, metadata


def prepare(hive, project, task_id, files):
    selected = file_paths(files)
    task = None
    explicit = []
    if task_id:
        current = hive.get_task(task_id)
        spec = current['spec']
        if project and spec['project'].casefold() != project.casefold():
            raise ValueError("Task belongs to a different project")
        project = spec['project']
        selected = file_paths(selected + [name for name in spec.get('files', []) if name not in selected])
        explicit = spec['memory']
        dependencies = []
        for ident in spec['depends_on'][:5]:
            prior = hive.get_task(ident)
            if prior['spec']['project'].casefold() != project.casefold():
                continue
            result = prior.get('result') or {}
            dependencies.append({'id': ident, 'status': prior['status'],
                                 'summary': result.get('summary', '')[:300],
                                 'verification': result.get('verification', [])[:1]})
        task = {'id': task_id, 'project': project, 'title': spec['title'], 'status': current['status'],
                'files': selected[:10], 'dependencies': dependencies, 'details_omitted': len(selected) > 10}
    if not selected and not explicit:
        return task, [], []
    related = relationships(hive, project, selected)
    lookup = set(selected + related)
    linked = []
    hive.index()
    with hive.connect() as c:
        rows = c.execute("SELECT n.path,n.content,m.files FROM notes n JOIN memory_metadata m ON m.path=n.path "
                         "WHERE m.files!='[]' AND m.state!='superseded' AND (?='' OR m.scope='' OR lower(m.scope)=lower(?))",
                         (project, project))
        for row in rows:
            if lookup & set(json.loads(row['files'])) and eligible(row['path'], metadata(row['path'], row['content']), project):
                linked.append(row['path'])
    return task, related[:5], list(dict.fromkeys(explicit + sorted(linked)))[:5]


def relationships(hive, project, files):
    """Use an existing optional static graph, without rebuilding or launching a tool."""
    config_path = hive.root / 'hive.local.json'
    config = json.loads(config_path.read_text(encoding='utf-8-sig')) if config_path.exists() else {}
    repo = config.get('projects', {}).get(project)
    if not repo or project not in config.get('graphify_projects', []) or not files:
        return []
    key = hashlib.sha256(str(Path(repo).resolve()).encode()).hexdigest()[:16]
    index = hive.runtime / 'code-index' / key / 'index.json'
    if not index.exists() or index.stat().st_size > 64 * 1024 * 1024:
        return []
    try:
        graph = json.loads(index.read_text(encoding='utf-8')).get('graph', {})
        nodes = {str(node['id']): node.get('file', node.get('file_path', '')) for node in graph.get('nodes', [])}
        touched = {ident for ident, name in nodes.items() if name in files}
        found = set()
        for edge in graph.get('links', []):
            a, b = str(edge.get('source')), str(edge.get('target'))
            if a in touched and nodes.get(b) and nodes[b] not in files:
                found.add(nodes[b])
            if b in touched and nodes.get(a) and nodes[a] not in files:
                found.add(nodes[a])
        return file_paths(sorted(found)[:5])
    except (OSError, ValueError, KeyError, TypeError):
        return []
