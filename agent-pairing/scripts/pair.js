#!/usr/bin/env bun
// Pairing lifecycle CLI. One deterministic subcommand per protocol step, so the
// Observer/Executor never run compound shell for setup or round bookkeeping.
// `.pairing/run.json` is the single source of truth — no symlinks, no
// timestamp-guessed paths. Run `pair.js help` for the command list.
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const cwd = process.cwd();
const RUN_JSON = path.join(cwd, '.pairing', 'run.json');

function git(args, allowFail) {
  try {
    return execFileSync('git', args, { cwd, maxBuffer: 64 * 1024 * 1024 }).toString().trim();
  } catch (e) {
    if (allowFail) return null;
    die(`git ${args.join(' ')} failed: ${e.message}`);
  }
}

function die(msg, code) { console.error(`error: ${msg}`); process.exit(code || 1); }

function readRun(required) {
  try { return JSON.parse(fs.readFileSync(RUN_JSON, 'utf8')); }
  catch (e) {
    if (required) die(`no readable ${path.relative(cwd, RUN_JSON)} — run \`pair.js init\` first`);
    return null;
  }
}

function writeRun(run) { fs.writeFileSync(RUN_JSON, JSON.stringify(run, null, 2) + '\n'); }

function pidAlive(pid) {
  if (!pid) return false;
  try { process.kill(pid, 0); return true; } catch (e) { return false; }
}

function findings(run) {
  let text = '';
  try { text = fs.readFileSync(path.join(cwd, run.paths.findings), 'utf8'); } catch (e) {}
  const lines = text.split('\n');
  return {
    open: lines.filter(l => /\bOPEN\b/.test(l) && !/\bRESOLVED\b/.test(l)),
    resolved: lines.filter(l => /\bRESOLVED\b/.test(l)),
  };
}

// Snapshot the work tree without touching it. Empty string on a clean tree.
function snapshot() { return git(['stash', 'create'], true) || ''; }

function lastReviewBase(run) {
  try { return fs.readFileSync(path.join(cwd, run.paths.last_review), 'utf8').trim(); }
  catch (e) { return ''; }
}

function killWatcher(run) {
  if (run && pidAlive(run.watcher_pid)) {
    try { process.kill(run.watcher_pid, 'SIGTERM'); console.log(`stopped watcher pid ${run.watcher_pid}`); } catch (e) {}
  }
}

function printDelta(base, label) {
  console.log(`diff base (${label}): ${base || 'HEAD (no snapshot; clean tree at capture)'}`);
  const diff = git(['diff', '--name-status', base || 'HEAD'], true);
  console.log(diff ? `changed:\n${diff}` : 'changed: (none tracked)');
  const untracked = git(['ls-files', '-o', '--exclude-standard'], true);
  console.log(untracked ? `untracked (read these in full):\n${untracked}` : 'untracked: (none)');
  return Boolean(diff || untracked);
}

const commands = {
  init(args) {
    const force = args.includes('--force');
    const targets = args.filter(a => !a.startsWith('--'));
    if (git(['rev-parse', '--is-inside-work-tree'], true) !== 'true')
      die('not a git work tree — the pairing loop needs one (the watcher and audits are git-based)');
    const prev = readRun();
    if (prev && prev.status === 'running' && !force) {
      die(`a run is already active (id ${prev.id}, round ${prev.round}` +
        `${pidAlive(prev.watcher_pid) ? `, watcher pid ${prev.watcher_pid} alive` : ''}). ` +
        'Resume it (see SKILL.md), `pair.js finish` it, or re-init with --force.', 2);
    }
    killWatcher(prev); // reap a stale watcher from any previous run
    for (const t of targets) {
      if (git(['check-ignore', '-q', t], true) !== null)
        console.error(`warning: target '${t}' is gitignored — the watcher and diffs cannot see it; ` +
          'expect no wakes for edits there (fall back to explicit end-of-run review).');
    }
    const d = new Date(), p = n => String(n).padStart(2, '0');
    const id = `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
    const dir = path.join('.pairing', 'runs', id);
    fs.mkdirSync(path.join(cwd, dir), { recursive: true });
    const paths = {
      dir,
      task: path.join(dir, 'task.md'),
      findings: path.join(dir, 'findings.md'),
      last_review: path.join(dir, 'last-review'),
    };
    fs.writeFileSync(path.join(cwd, paths.findings), '');
    const base = snapshot();
    fs.writeFileSync(path.join(cwd, paths.last_review), base + '\n');
    const run = { id, status: 'running', round: 0, run_base: base, pending_base: null,
      executor: null, watcher_pid: null, paths };
    writeRun(run);
    // Keep .pairing/ out of commits WITHOUT touching the tracked .gitignore:
    // .git/info/exclude is local-only and never staged. (If an old run already
    // added `.pairing/` to a tracked .gitignore, leave that line alone.)
    const gitDir = git(['rev-parse', '--git-dir']);
    const exclude = path.resolve(cwd, gitDir, 'info', 'exclude');
    let excl = ''; try { excl = fs.readFileSync(exclude, 'utf8'); } catch (e) {}
    if (!excl.split('\n').includes('.pairing/')) {
      fs.mkdirSync(path.dirname(exclude), { recursive: true });
      fs.appendFileSync(exclude, (excl && !excl.endsWith('\n') ? '\n' : '') + '.pairing/\n');
    }
    console.log(JSON.stringify(run, null, 2));
    console.log(`\nnext: write the task brief to ${paths.task}, spawn the Executor, arm the watcher.`);
  },

  status() {
    const run = readRun(true);
    console.log(JSON.stringify(run, null, 2));
    console.log(`watcher: ${pidAlive(run.watcher_pid) ? `alive (pid ${run.watcher_pid})` : 'not running'}`);
    const f = findings(run);
    console.log(`findings: ${f.open.length} OPEN, ${f.resolved.length} RESOLVED`);
  },

  'audit-begin'(args) {
    const run = readRun(true);
    if (run.status !== 'running') die(`run is '${run.status}', not running`);
    // Capture the new baseline BEFORE the audit. Anything the Executor saves
    // while the Observer reads lands after this snapshot and shows up in the
    // NEXT delta — the machinery, not Observer paranoia, holds that property.
    run.pending_base = snapshot();
    writeRun(run);
    const full = args.includes('--full');
    const base = full ? run.run_base : lastReviewBase(run);
    const any = printDelta(base, full ? 'whole run' : 'since last review');
    if (!any) console.log('note: delta is empty — likely an edit-then-revert; re-arm and go idle.');
    console.log(`\nwhen findings are recorded, run: pair.js audit-end`);
  },

  'audit-end'() {
    const run = readRun(true);
    if (run.pending_base === null) die('no pending audit — run `pair.js audit-begin` first');
    fs.writeFileSync(path.join(cwd, run.paths.last_review), run.pending_base + '\n');
    run.pending_base = null;
    writeRun(run);
    console.log('last-review advanced to the audit-begin snapshot.');
  },

  round() {
    const run = readRun(true);
    if (run.round >= 3) {
      console.error(`convergence cap reached (round ${run.round}/3) — finish with open findings noted.`);
      process.exit(2);
    }
    run.round += 1;
    writeRun(run);
    const f = findings(run);
    console.log(`round ${run.round}/3. task: ${run.paths.task}, findings: ${run.paths.findings}`);
    console.log(f.open.length ? `OPEN findings for the respawn brief:\n${f.open.join('\n')}` : 'no OPEN findings recorded.');
  },

  finish() {
    const run = readRun(true);
    run.status = 'done';
    run.executor = null;
    run.pending_base = null;
    writeRun(run);
    killWatcher(run);
    const f = findings(run);
    console.log(`run ${run.id} done after round ${run.round}: ${f.resolved.length} RESOLVED, ${f.open.length} OPEN.`);
    if (f.open.length) console.log(f.open.join('\n'));
    console.log('terminal state: static audit clean — runtime unverified (list what was not statically verifiable).');
  },

  'executor-ready'() {
    const run = readRun(true);
    run.executor = 'ready_to_finish';
    writeRun(run);
    console.log('signalled ready_to_finish; now run: pair.js executor-wait');
  },

  async 'executor-wait'(args) {
    const i = args.indexOf('--timeout');
    const timeoutS = i >= 0 ? Number(args[i + 1]) : 150;
    const deadline = Date.now() + timeoutS * 1000;
    for (;;) {
      const run = readRun(true);
      if (run.status === 'done') { console.log('observer marked the run done — exit now.'); process.exit(0); }
      const open = findings(run).open;
      if (open.length) {
        run.executor = null;
        writeRun(run);
        console.log(`new OPEN findings — resolve them, then signal ready again:\n${open.join('\n')}`);
        process.exit(3);
      }
      if (Date.now() >= deadline) {
        console.log(`no observer verdict within ${timeoutS}s — exit; the observer's round backstop covers this.`);
        process.exit(4);
      }
      await new Promise(r => setTimeout(r, 5000));
    }
  },

  help() {
    console.log(`pair.js <command> — agent-pairing lifecycle (run from the workspace root)
  init [--force] [target...]   kickoff: preconditions, run dir, run.json, info/exclude; warns on gitignored targets
  status                       dump run.json, watcher liveness, findings tally
  audit-begin [--full]         capture snapshot, print delta to audit (--full: whole run for the final pass)
  audit-end                    advance last-review to the audit-begin snapshot (call AFTER findings are recorded)
  round                        bump round; exit 2 at the 3-round cap; prints OPEN findings for the respawn brief
  finish                       mark done, clear executor flag, stop the watcher
  executor-ready               (Executor) signal ready_to_finish; wakes the Observer for the final audit
  executor-wait [--timeout s]  (Executor) poll for verdict; exit 0=done, 3=new OPEN findings, 4=timeout`);
  },
};

(async () => {
  const [cmd, ...args] = process.argv.slice(2);
  const fn = commands[cmd || 'help'];
  if (!fn) die(`unknown command '${cmd}' — see \`pair.js help\``);
  await fn(args);
})();
