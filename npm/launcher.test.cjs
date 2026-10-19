'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const launcher = require('./launcher.cjs');

function write(file, text) { fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, text); }
function hash(file) { return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex'); }
function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'Hive npm '));
  t.after(() => {
    assert.equal(fs.realpathSync(path.dirname(root)), fs.realpathSync(os.tmpdir()));
    assert.ok(path.basename(root).startsWith('Hive npm '));
    fs.rmSync(root, { recursive: true, force: true });
  });
  const f = { root, source: path.join(root, 'Package'), project: path.join(root, 'My App'),
    home: path.join(root, 'Shared Hive'), user: path.join(root, 'User') };
  for (const directory of [f.source, f.project, f.user]) fs.mkdirSync(directory);
  for (const file of ['bootstrap.py', 'hive.py', 'requirements.txt', 'hivemind/__init__.py', 'templates/vault/START.md']) write(path.join(f.source, file), file);
  write(path.join(f.source, 'package.json'), '{"version":"0.1.0"}');
  f.registry = path.join(f.user, '.hivemind/installation.json');
  f.options = { cwd: f.project, packageRoot: f.source, userHome: f.user, env: {}, platform: 'linux', log() {} };
  return f;
}
function home(folder) { write(path.join(folder, 'bootstrap.py'), 'old bootstrap'); write(path.join(folder, 'hive.py'), 'old hive'); }

test('parses setup paths with spaces and forwards Python options without shell quoting', t => {
  const f = fixture(t);
  const args = ['setup', '--model', 'provider/model:free', f.project, '--hive-home=' + f.home, '--no-prompt'];
  const parsed = launcher.parseArgs(args, f.root);
  assert.equal(parsed.project, f.project);
  assert.equal(parsed.home, f.home);
  assert.deepEqual(parsed.args, ['--model', 'provider/model:free', f.project, '--no-prompt']);
});

test('help and version do not create a home or require Python', t => {
  const f = fixture(t);
  const options = { ...f.options, spawn() { assert.fail('Must not start Python'); } };
  assert.equal(launcher.run(['setup', '--help'], options), 0);
  assert.equal(launcher.run(['--version'], options), 0);
  assert.equal(fs.existsSync(f.home), false);
  assert.equal(fs.existsSync(f.registry), false);
});

test('an enrolled project retains its home and rejects accidental memory splitting', t => {
  const f = fixture(t);
  home(f.home);
  write(path.join(f.project, '.hivemind/local.json'), JSON.stringify({ hive_home: f.home }));
  const parsed = launcher.parseArgs(['setup'], f.project);
  assert.equal(launcher.selectHome(parsed, f.source, f.user, {}).home, f.home);
  assert.throws(() => launcher.selectHome({ ...parsed, home: path.join(f.root, 'Other') }, f.source, f.user, {}), /already uses/);
});

test('missing project installations are not silently replaced with empty memory', t => {
  const f = fixture(t);
  write(path.join(f.project, '.hivemind/local.json'), JSON.stringify({ hive_home: f.home }));
  assert.throws(() => launcher.selectHome({ project: f.project }, f.source, f.user, {}), /unavailable/);
});

test('reuses a configured source checkout before making a new installation', t => {
  const f = fixture(t);
  write(path.join(f.source, 'hive.local.json'), '{}');
  assert.equal(launcher.selectHome({ project: f.project }, f.source, f.user, {}).home, f.source);
});

test('registered and legacy shared homes are reused; fresh homes stay outside projects', t => {
  const f = fixture(t);
  assert.equal(launcher.selectHome({ project: f.project }, f.source, f.user, {}).home, path.join(f.user, '.hivemind/home'));
  const legacy = path.join(f.user, 'HiveMind');
  home(legacy);
  assert.equal(launcher.selectHome({ project: f.project }, f.source, f.user, {}).home, legacy);
  home(f.home);
  write(f.registry, JSON.stringify({ hive_home: f.home }));
  assert.equal(launcher.selectHome({ project: f.project }, f.source, f.user, {}).home, f.home);
});

test('refuses cache, node_modules and new in-project runtime homes', t => {
  const f = fixture(t);
  for (const target of [path.join(f.project, 'shared'), path.join(f.root, 'node_modules/shared'), path.join(f.user, '.npm/home')]) {
    assert.throws(() => launcher.selectHome({ project: f.project, home: target }, f.source, f.user, {}), /outside|node_modules|npm cache/);
  }
});

test('only software is copied; upgrades preserve vault, database and credentials', t => {
  const f = fixture(t);
  write(path.join(f.source, 'vault/private.md'), 'do not copy');
  write(path.join(f.source, 'runtime/secrets/openrouter.json'), 'do not copy');
  launcher.installBackend(f.source, f.home);
  assert.equal(fs.existsSync(path.join(f.home, 'vault')), false);
  assert.equal(fs.existsSync(path.join(f.home, 'runtime')), false);
  for (const name of ['vault/my-memory.md', 'runtime/hivemind.db', 'runtime/secrets/openrouter.json', 'hive.local.json']) write(path.join(f.home, name), 'preserve ' + name);
  write(path.join(f.source, 'bootstrap.py'), 'updated software');
  launcher.installBackend(f.source, f.home);
  assert.equal(fs.readFileSync(path.join(f.home, 'bootstrap.py'), 'utf8'), 'updated software');
  for (const name of ['vault/my-memory.md', 'runtime/hivemind.db', 'runtime/secrets/openrouter.json', 'hive.local.json']) assert.equal(fs.readFileSync(path.join(f.home, name), 'utf8'), 'preserve ' + name);
});

test('an existing unmanaged checkout is never overwritten by an npm cache copy', t => {
  const f = fixture(t);
  home(f.home);
  assert.equal(launcher.installBackend(f.source, f.home), 'existing installation');
  assert.equal(fs.readFileSync(path.join(f.home, 'bootstrap.py'), 'utf8'), 'old bootstrap');
});

test('an old project launcher cannot downgrade a newer shared backend', t => {
  const f = fixture(t);
  write(path.join(f.source, 'package.json'), '{"version":"0.2.0"}');
  write(path.join(f.source, 'bootstrap.py'), 'new backend');
  launcher.installBackend(f.source, f.home);
  write(path.join(f.source, 'package.json'), '{"version":"0.1.0"}');
  write(path.join(f.source, 'bootstrap.py'), 'old backend');
  assert.match(launcher.installBackend(f.source, f.home), /newer shared backend/);
  assert.equal(fs.readFileSync(path.join(f.home, 'bootstrap.py'), 'utf8'), 'new backend');
});

test('local backend edits are preserved and validation completes before writes', t => {
  const f = fixture(t);
  launcher.installBackend(f.source, f.home);
  write(path.join(f.source, 'bootstrap.py'), 'new release');
  write(path.join(f.home, 'hive.py'), 'local edit');
  assert.throws(() => launcher.installBackend(f.source, f.home), /Locally edited/);
  assert.equal(fs.readFileSync(path.join(f.home, 'hive.py'), 'utf8'), 'local edit');
  assert.equal(fs.readFileSync(path.join(f.home, 'bootstrap.py'), 'utf8'), 'bootstrap.py');
});

test('an interrupted software copy resumes from its pending manifest', t => {
  const f = fixture(t);
  launcher.installBackend(f.source, f.home);
  const marker = path.join(f.home, '.hivemind-npm.json');
  const data = JSON.parse(fs.readFileSync(marker));
  write(path.join(f.source, 'bootstrap.py'), 'next bootstrap');
  write(path.join(f.source, 'hive.py'), 'next hive');
  data.pending = { ...data.installed, 'bootstrap.py': hash(path.join(f.source, 'bootstrap.py')), 'hive.py': hash(path.join(f.source, 'hive.py')) };
  write(marker, JSON.stringify(data));
  write(path.join(f.home, 'bootstrap.py'), 'next bootstrap');
  launcher.installBackend(f.source, f.home);
  assert.equal(fs.readFileSync(path.join(f.home, 'hive.py'), 'utf8'), 'next hive');
  assert.deepEqual(JSON.parse(fs.readFileSync(marker)).pending, {});
});

test('metadata cannot authorize deleting memory or secrets', t => {
  const f = fixture(t);
  launcher.installBackend(f.source, f.home);
  const note = path.join(f.home, 'vault/private.md');
  write(note, 'keep this');
  const marker = path.join(f.home, '.hivemind-npm.json');
  const data = JSON.parse(fs.readFileSync(marker));
  data.installed['vault/private.md'] = hash(note);
  write(marker, JSON.stringify(data));
  assert.throws(() => launcher.installBackend(f.source, f.home), /non-software path/);
  assert.equal(fs.readFileSync(note, 'utf8'), 'keep this');
});

test('linked destination directories cannot redirect software writes elsewhere', t => {
  const f = fixture(t);
  launcher.installBackend(f.source, f.home);
  const outside = path.join(f.root, 'Outside');
  write(path.join(outside, '__init__.py'), 'must not change');
  fs.renameSync(path.join(f.home, 'hivemind'), path.join(f.home, 'module-backup'));
  fs.symlinkSync(outside, path.join(f.home, 'hivemind'), process.platform === 'win32' ? 'junction' : 'dir');
  assert.throws(() => launcher.installBackend(f.source, f.home), /linked path/);
  assert.equal(fs.readFileSync(path.join(outside, '__init__.py'), 'utf8'), 'must not change');
});

test('the wizard inherits the terminal and successful setup remembers the shared home', t => {
  const f = fixture(t);
  const calls = [];
  const logs = [];
  const spawn = (command, args, options) => { calls.push({ command, args, options }); return { status: 0 }; };
  const code = launcher.run(['setup', '--hive-home', f.home, '--learning', 'free'], {
    ...f.options, env: { OPENROUTER_API_KEY: 'fixture-secret' }, spawn, log: message => logs.push(message) });
  assert.equal(code, 0);
  const wizard = calls.at(-1);
  assert.equal(wizard.options.stdio, 'inherit');
  assert.equal(wizard.options.cwd, f.project);
  assert.deepEqual(wizard.args, [path.join(f.home, 'bootstrap.py'), '--learning', 'free']);
  assert.equal(wizard.options.env.OPENROUTER_API_KEY, 'fixture-secret');
  assert.equal(logs.join('').includes('fixture-secret'), false);
  assert.equal(JSON.parse(fs.readFileSync(f.registry)).hive_home, f.home);
});

test('Python errors and cancelled setup propagate without saving a bad default', t => {
  const f = fixture(t);
  const spawn = (command, args) => ({ status: args.includes('-c') ? 0 : 17 });
  assert.equal(launcher.run(['setup', '--hive-home', f.home], { ...f.options, spawn }), 17);
  assert.equal(fs.existsSync(f.registry), false);
});

test('missing Python fails before installing any software', t => {
  const f = fixture(t);
  assert.throws(() => launcher.run(['setup', '--hive-home', f.home], {
    ...f.options, spawn: () => ({ error: new Error('ENOENT') }) }), /Python 3.11/);
  assert.equal(fs.existsSync(f.home), false);
});

test('dry runs do not update software, metadata or the remembered home', t => {
  const f = fixture(t);
  launcher.installBackend(f.source, f.home);
  const marker = path.join(f.home, '.hivemind-npm.json');
  const before = fs.readFileSync(marker, 'utf8');
  write(path.join(f.source, 'bootstrap.py'), 'updated source');
  assert.equal(launcher.run(['setup', '--hive-home', f.home, '--dry-run'], { ...f.options, spawn: () => ({ status: 0 }) }), 0);
  assert.equal(fs.readFileSync(marker, 'utf8'), before);
  assert.equal(fs.readFileSync(path.join(f.home, 'bootstrap.py'), 'utf8'), 'bootstrap.py');
  assert.equal(fs.existsSync(f.registry), false);
});
