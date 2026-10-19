'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');

const ROOT_FILES = ['bootstrap.py', 'hive.py', 'requirements.txt', 'requirements-graphify.txt',
  'hivemind.cmd', 'setup.ps1', 'setup.sh', 'install.ps1', 'install.sh', 'README.md', 'AGENTS.md', 'LICENSE'];
const FOLDERS = ['hivemind', 'templates', 'scripts', 'docs', 'examples'];
const MARKER = '.hivemind-npm.json';
const VALUE_OPTIONS = new Set(['--name', '--harness', '--scope', '--learning', '--model',
  '--daily-limit', '--daily-usd', '--prompt-price', '--completion-price', '--tool-profile']);

function inside(base, target) {
  const relative = path.relative(base, target);
  return relative === '' || (!path.isAbsolute(relative) && relative !== '..' && !relative.startsWith('..' + path.sep));
}

function canonical(folder) {
  const suffix = [];
  let parent = path.resolve(folder);
  while (!fs.existsSync(parent)) { suffix.unshift(path.basename(parent)); parent = path.dirname(parent); }
  return path.join(fs.realpathSync(parent), ...suffix);
}

function safePath(base, relative) {
  const target = path.resolve(base, relative);
  if (!inside(base, target)) throw new Error('Installer path escapes its shared home');
  let current = base;
  for (const part of path.relative(base, target).split(path.sep).filter(Boolean)) {
    current = path.join(current, part);
    if (fs.existsSync(current) && fs.lstatSync(current).isSymbolicLink()) {
      throw new Error('Installer will not overwrite a linked path: ' + current);
    }
  }
  return target;
}

function readJson(file) {
  if (fs.statSync(file).size > 2_000_000) throw new Error('Installer metadata is oversized: ' + file);
  try { return JSON.parse(fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, '')); }
  catch { throw new Error('Invalid installer metadata: ' + file); }
}

function atomicWrite(file, data) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temp = file + '.' + crypto.randomUUID() + '.tmp';
  try {
    fs.writeFileSync(temp, data, { flag: 'wx', mode: 0o600 });
    fs.renameSync(temp, file);
  } finally {
    if (fs.existsSync(temp)) fs.unlinkSync(temp);
  }
}

function parseArgs(argv, cwd) {
  const args = [...argv];
  if (args[0] === 'setup') args.shift();
  let home;
  const forwarded = [];
  for (let index = 0; index < args.length; index++) {
    if (args[index] === '--hive-home' || args[index].startsWith('--hive-home=')) {
      home = args[index].startsWith('--hive-home=') ? args[index].slice(12) : args[++index];
      if (!home || home.startsWith('--')) throw new Error('--hive-home requires a directory');
    } else forwarded.push(args[index]);
  }
  let project;
  for (let index = 0; index < forwarded.length; index++) {
    const arg = forwarded[index];
    if (VALUE_OPTIONS.has(arg)) { index++; continue; }
    if (arg.startsWith('-')) continue;
    if (project) throw new Error('Supply only one project folder before the setup options');
    project = arg;
  }
  return { args: forwarded, project: path.resolve(cwd, project || '.'), home: home ? path.resolve(cwd, home) : undefined };
}

function hiveHome(folder) {
  return fs.existsSync(path.join(folder, 'bootstrap.py')) && fs.existsSync(path.join(folder, 'hive.py'));
}

function selectHome(parsed, packageRoot, userHome, env) {
  const registry = path.join(userHome, '.hivemind', 'installation.json');
  const local = path.join(parsed.project, '.hivemind', 'local.json');
  let projectHome;
  if (fs.existsSync(local)) {
    projectHome = readJson(local).hive_home;
    if (typeof projectHome !== 'string' || !path.isAbsolute(projectHome)) throw new Error('Project has an invalid HiveMind home pointer');
    if (!hiveHome(projectHome)) throw new Error('The project\'s shared HiveMind installation is unavailable: ' + projectHome);
  }
  const supplied = parsed.home || env.HIVEMIND_HOME;
  if (supplied && !path.isAbsolute(supplied)) throw new Error('HIVEMIND_HOME must be an absolute directory');
  let home = supplied ? path.resolve(parsed.project, supplied) : projectHome;
  if (home && projectHome && canonical(home) !== canonical(projectHome)) {
    throw new Error('Project already uses ' + projectHome + '; keep that shared home to preserve its memory');
  }
  if (!home && fs.existsSync(path.join(packageRoot, 'hive.local.json')) && hiveHome(packageRoot)) home = packageRoot;
  if (!home && fs.existsSync(registry)) {
    home = readJson(registry).hive_home;
    if (typeof home !== 'string' || !path.isAbsolute(home) || !hiveHome(home)) {
      throw new Error('Registered HiveMind home is unavailable; select an existing installation with --hive-home');
    }
  }
  const legacy = path.join(userHome, 'HiveMind');
  if (!home) home = hiveHome(legacy) ? legacy : path.join(userHome, '.hivemind', 'home');
  home = canonical(home);
  if (inside(parsed.project, home) && !hiveHome(home)) {
    throw new Error('Choose a shared HiveMind home outside the application project');
  }
  if (home.split(path.sep).some(part => part.toLowerCase() === 'node_modules')) {
    throw new Error('The shared HiveMind home cannot live in node_modules');
  }
  const caches = [path.join(userHome, '.npm'), env.npm_config_cache, env.NPM_CONFIG_CACHE,
    env.LOCALAPPDATA && path.join(env.LOCALAPPDATA, 'npm-cache')].filter(Boolean);
  if (caches.some(cache => inside(canonical(cache), home))) throw new Error('The shared HiveMind home cannot live in an npm cache');
  return { home, registry };
}

function backendFiles(source) {
  const files = [];
  function visit(relative) {
    const file = safePath(source, relative);
    const info = fs.lstatSync(file);
    if (info.isDirectory()) {
      for (const name of fs.readdirSync(file).sort()) {
        if (!name.startsWith('.') && name !== '__pycache__') visit(path.join(relative, name));
      }
    } else if (info.isFile() && !/\.(pyc|db|sqlite\d*|key|pem)$/i.test(file)) files.push(relative);
  }
  for (const name of [...ROOT_FILES, ...FOLDERS]) if (fs.existsSync(path.join(source, name))) visit(name);
  for (const name of ['bootstrap.py', 'hive.py', 'requirements.txt']) {
    if (!files.includes(name)) throw new Error('Package is missing ' + name);
  }
  return files;
}

function digest(file) { return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex'); }

function versionCompare(left, right) {
  const parts = value => String(value).split(/[.+-]/).slice(0, 3).map(Number);
  const a = parts(left), b = parts(right);
  for (let index = 0; index < 3; index++) {
    if (!Number.isInteger(a[index]) || !Number.isInteger(b[index])) throw new Error('Invalid installer version metadata');
    if (a[index] !== b[index]) return a[index] - b[index];
  }
  return 0;
}

function installBackend(source, home, preview = false) {
  if (path.resolve(source) === path.resolve(home)) return 'existing checkout';
  const markerPath = safePath(home, MARKER);
  if (hiveHome(home) && !fs.existsSync(markerPath)) return 'existing installation';
  if (preview) {
    if (hiveHome(home)) return 'existing npm-managed installation';
    throw new Error('A preview needs an existing HiveMind installation; run setup once first');
  }
  let previous = { installed: {}, pending: {} };
  if (fs.existsSync(markerPath)) {
    previous = readJson(markerPath);
    if (previous.schema !== 1 || !previous.installed || !previous.pending) throw new Error('Unrecognized npm installation metadata');
  } else if (fs.existsSync(home) && fs.readdirSync(home).length) {
    throw new Error('Shared home is not empty and is not a HiveMind installation: ' + home);
  }
  const version = readJson(path.join(source, 'package.json')).version;
  if (previous.package_version && versionCompare(version, previous.package_version) < 0) {
    if (Object.keys(previous.pending).length) throw new Error('Finish the newer shared-backend update before using this older launcher');
    return 'newer shared backend ' + previous.package_version;
  }
  const files = backendFiles(source);
  const hashes = Object.fromEntries(files.map(name => [name, digest(safePath(source, name))]));
  // Validate every destination before writing. Interrupted copies can resume
  // against the last installed or pending manifest without losing local edits.
  for (const name of new Set([...Object.keys(previous.installed), ...Object.keys(previous.pending), ...files])) {
    if (!ROOT_FILES.includes(name) && !FOLDERS.includes(name.split(/[\\/]/)[0])) {
      throw new Error('Installer metadata refers to a non-software path: ' + name);
    }
    const target = safePath(home, name);
    if (fs.existsSync(target) && ![previous.installed[name], previous.pending[name], hashes[name]].includes(digest(target))) {
      throw new Error('Locally edited backend file preserved: ' + target + '. Update that installation directly.');
    }
  }
  fs.mkdirSync(home, { recursive: true });
  atomicWrite(markerPath, JSON.stringify({ schema: 1, package_version: version, installed: previous.installed, pending: hashes }));
  for (const name of files) {
    const target = safePath(home, name);
    if (!fs.existsSync(target) || digest(target) !== hashes[name]) atomicWrite(target, fs.readFileSync(safePath(source, name)));
  }
  for (const name of new Set([...Object.keys(previous.installed), ...Object.keys(previous.pending)])) {
    if (!(name in hashes)) {
      const target = safePath(home, name);
      if (fs.existsSync(target)) fs.unlinkSync(target);
    }
  }
  atomicWrite(markerPath, JSON.stringify({ schema: 1, package_version: version, installed: hashes, pending: {} }));
  return 'npm-managed installation';
}

function findPython(home, platform, env, spawn) {
  const local = path.join(home, '.venv', platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  const candidates = fs.existsSync(local) ? [[local, []]] : [];
  if (env.HIVEMIND_PYTHON) candidates.push([env.HIVEMIND_PYTHON, []]);
  if (platform === 'win32') for (const version of ['-3', '-3.13', '-3.12', '-3.11']) candidates.push(['py', [version]]);
  candidates.push(['python3', []], ['python', []]);
  for (const [command, args] of candidates) {
    const check = spawn(command, [...args, '-c', 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'],
      { env, stdio: 'ignore', windowsHide: true, timeout: 10000 });
    if (!check.error && check.status === 0) return { command, args };
  }
  throw new Error('Python 3.11+ is required. Install Python or set HIVEMIND_PYTHON to its executable, then rerun setup.');
}

function run(argv, options = {}) {
  const cwd = options.cwd || process.cwd();
  const env = options.env || process.env;
  const source = options.packageRoot || path.resolve(__dirname, '..');
  const userHome = options.userHome || os.homedir();
  const spawn = options.spawn || spawnSync;
  const log = options.log || console.log;
  if (argv.some(arg => ['--help', '-h'].includes(arg))) {
    log('Usage: hivemind setup [PROJECT] [--hive-home DIRECTORY] [setup options]\n\n' +
      'Run in a project folder to open the guided harness, model and API-key setup.\n' +
      'Needs Python 3.11+; its virtual environment and pinned dependencies are prepared automatically.\n' +
      'Memory stays in a persistent shared home, outside node_modules and npm caches.\n' +
      'Options: --configure, --no-prompt, --dry-run, --learning free, --harness NAME,\n' +
      '--capture, --with-semantic, --with-graphify. Other setup options pass through.\n' +
      'Use --hive-home or HIVEMIND_HOME to reuse an existing installation.');
    return 0;
  }
  if (argv.includes('--version')) {
    log(readJson(path.join(source, 'package.json')).version);
    return 0;
  }
  const parsed = parseArgs(argv, cwd);
  if (!fs.existsSync(parsed.project) || !fs.statSync(parsed.project).isDirectory()) throw new Error('Project folder does not exist: ' + parsed.project);
  parsed.project = fs.realpathSync(parsed.project);
  const selected = selectHome(parsed, source, userHome, env);
  const python = findPython(selected.home, options.platform || process.platform, env, spawn);
  const preview = parsed.args.includes('--dry-run');
  const installed = installBackend(source, selected.home, preview);
  log('HiveMind shared home: ' + selected.home + ' (' + installed + ')');
  const result = spawn(python.command, [...python.args, path.join(selected.home, 'bootstrap.py'), ...parsed.args],
    { cwd, env: { ...env, PYTHONUTF8: '1' }, stdio: 'inherit' });
  if (result.error) throw new Error('Could not start Python setup: ' + result.error.message);
  const code = result.status ?? (result.signal === 'SIGINT' ? 130 : 1);
  if (!preview && code === 0) {
    const registry = safePath(path.dirname(selected.registry), path.basename(selected.registry));
    atomicWrite(registry, JSON.stringify({ schema: 1, hive_home: selected.home }) + '\n');
  }
  return code;
}

module.exports = { run, parseArgs, selectHome, backendFiles, installBackend, findPython };
