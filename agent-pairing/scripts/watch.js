const fs = require('fs');
const path = require('path');
const { execFile, execFileSync } = require('child_process');

const watchDir = process.cwd();

// The watcher has ONE job: wake the agent only when there is actually something
// to review. Every exit is confirmed against git first — a file event that nets
// no git-visible change (edit-then-revert, a touch, a write to a gitignored path,
// a rename fs.watch reported twice) does NOT wake the agent. And the watcher
// never exits on a timer alone: no change, no wake. It runs until a real change
// or until it is stopped.
//
// Two detectors feed one git-confirmed exit:
//   - fs.watch : low latency on normal saves.
//   - git poll : safety net for saves fs.watch misses (in-place rename-replace,
//                which the Edit tool and many editors do).
// Both funnel through checkAndExit(), which exits ONLY if `git status --porcelain`
// differs from the baseline captured when the watcher armed. So git state — not
// the event payload and not the clock — is what decides a wake.

// Directories to ignore (cheap fast-path before the git check). `.pairing` is
// critical: the observer writes its findings there.
const ignoreDirs = [
  '.git',
  'node_modules',
  'target',
  'dist',
  'build',
  '.ralphy',
  '.devlog',
  '.claude',
  '.gemini',
  '.agents',
  '.pairing',
  'tmp',
  'out'
];

// Extensions to ignore (cheap fast-path only; the git check is the real filter).
const ignoreExtensions = [
  '.log',
  '.lock',
  '.lockb',
  '.DS_Store'
];

// Settle window after the first event in a burst, so we check git once the file
// is fully written rather than mid-save, and coalesce a multi-file burst.
const DEBOUNCE_MS = Number(process.env.OBSERVER_DEBOUNCE_MS) || 400;

// git-status poll interval (safety net for saves fs.watch misses). Set 0 to
// disable. Read-only; only ever touches .git/ (ignored), never the work tree.
const POLL_MS = Number(process.env.OBSERVER_POLL_MS) || 25000;

function isIgnored(filename) {
  const parts = filename.split(path.sep);
  if (parts.some(part => ignoreDirs.includes(part))) return true;
  const ext = path.extname(filename);
  if (ignoreExtensions.includes(ext)) return true;
  if (ignoreExtensions.some(ignored => filename.endsWith(ignored))) return true;
  return false;
}

function gitStatusSync() {
  try {
    return execFileSync('git', ['status', '--porcelain'],
      { cwd: watchDir, maxBuffer: 16 * 1024 * 1024 }).toString();
  } catch (e) {
    return null;
  }
}

function gitStatus(cb) {
  execFile('git', ['status', '--porcelain'], { cwd: watchDir, maxBuffer: 16 * 1024 * 1024 },
    (err, stdout) => cb(err ? null : stdout));
}

// Working-tree state at arm time. Captured synchronously so a change that lands
// in the first milliseconds can't slip past an async baseline read.
const baseline = gitStatusSync();
if (baseline === null) {
  console.error('warning: `git status` failed here — wakes require a git repo to confirm changes.');
}

let debounceTimer = null;
let pollTimer = null;

console.log(`Watching ${watchDir} (debounce ${DEBOUNCE_MS}ms, poll ${POLL_MS}ms; exits only on a git-confirmed change)...`);

const watcher = fs.watch(watchDir, { recursive: true }, (event, filename) => {
  if (!filename) return;
  if (isIgnored(filename)) return;
  scheduleCheck(filename);
});

if (POLL_MS > 0) {
  pollTimer = setInterval(() => checkAndExit('git-poll (fs.watch missed a save)'), POLL_MS);
}

// Debounced git check shared by fs.watch events.
function scheduleCheck(reason) {
  if (debounceTimer) clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => checkAndExit(reason), DEBOUNCE_MS);
}

// The only path to a wake: exit IFF the work tree differs from the baseline.
function checkAndExit(reason) {
  gitStatus((s) => {
    if (s === null) return;                          // git unavailable; keep watching
    if (baseline === null || s !== baseline) finish(`Detected change: ${reason}`);
    // else: nothing actually changed — stay idle, do not wake the agent
  });
}

function finish(message) {
  if (debounceTimer) clearTimeout(debounceTimer);
  if (pollTimer) clearInterval(pollTimer);
  watcher.close();
  console.log(message);
  process.exit(0);
}
