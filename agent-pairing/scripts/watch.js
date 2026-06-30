const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { execFileSync } = require('child_process');

const watchDir = process.cwd();

// The watcher has ONE job: wake the agent only when there is actually something
// to review. Every exit is confirmed against a CONTENT FINGERPRINT first — a file
// event that nets no content change (edit-then-revert, a touch, a write to a
// gitignored path, a rename fs.watch reported twice) does NOT wake the agent. And
// the watcher never exits on a timer alone: no change, no wake. It runs until a real
// change or until it is stopped.
//
// Two detectors feed one fingerprint-confirmed exit:
//   - fs.watch : low latency on normal saves.
//   - poll     : safety net for saves fs.watch misses (in-place rename-replace,
//                which the Edit tool and many editors do).
// Both funnel through checkAndExit(), which exits ONLY if the content fingerprint
// differs from the baseline captured when the watcher armed. The fingerprint hashes
// the content of every NON-IGNORED file — tracked AND untracked — listed by
// `git ls-files -oc --exclude-standard`. This is deliberately not `git status`:
// status can't see content edits to untracked files (untracked is binary `??`) and
// collapses untracked directories, so on a tree whose work is not yet committed it
// silently MISSES most saves. Hashing content fixes that while keeping the contract:
// content, not the event payload and not the clock, is what decides a wake.

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
const DEBOUNCE_MS = process.env.OBSERVER_DEBOUNCE_MS !== undefined ? Number(process.env.OBSERVER_DEBOUNCE_MS) : 400;

// git-status poll interval (safety net for saves fs.watch misses). Set 0 to
// disable. Read-only; only ever touches .git/ (ignored), never the work tree.
const POLL_MS = process.env.OBSERVER_POLL_MS !== undefined ? Number(process.env.OBSERVER_POLL_MS) : 25000;

function isIgnored(filename) {
  const parts = filename.split(path.sep);
  if (parts.some(part => ignoreDirs.includes(part))) return true;
  const ext = path.extname(filename);
  if (ignoreExtensions.includes(ext)) return true;
  if (ignoreExtensions.some(ignored => filename.endsWith(ignored))) return true;
  return false;
}

// Hash content up to this size per file; above it, fall back to size+mtime (a huge
// file is almost certainly build output that slipped through .gitignore anyway).
const MAX_HASH_BYTES = 8 * 1024 * 1024;

// All non-ignored files (tracked + untracked), listed individually and honoring
// .gitignore / .git/info/exclude / global excludes. Unlike `git status`, ls-files
// never collapses untracked directories and lists every untracked file by path.
function listFiles() {
  try {
    const out = execFileSync('git', ['ls-files', '-oc', '--exclude-standard', '-z'],
      { cwd: watchDir, maxBuffer: 64 * 1024 * 1024 }).toString();
    return out.split('\0').filter(Boolean);
  } catch (e) {
    return null;
  }
}

// A content fingerprint of the work tree: path + size + content of every non-ignored
// file. Catches new files, deletions, and content edits to tracked OR untracked
// files; an edit-then-revert hashes identically and does NOT wake.
function fingerprintSync() {
  const files = listFiles();
  if (files === null) return null;
  const h = crypto.createHash('sha1');
  for (const f of files.sort()) {
    if (isIgnored(f)) continue;                      // defensive extra fast-path ignores
    h.update(f); h.update('\0');
    let st;
    try { st = fs.statSync(path.join(watchDir, f)); }
    catch (e) { h.update('MISSING\0'); continue; }   // raced deletion
    if (!st.isFile()) { h.update('NONFILE\0'); continue; }
    h.update(String(st.size)); h.update('\0');
    if (st.size <= MAX_HASH_BYTES) {
      try { h.update(fs.readFileSync(path.join(watchDir, f))); }
      catch (e) { h.update(String(st.mtimeMs)); }    // raced write; mtime is the proxy
    } else {
      h.update(String(st.mtimeMs));
    }
    h.update('\n');
  }
  return h.digest('hex');
}

// Work-tree fingerprint at arm time. Captured synchronously so a change that lands
// in the first milliseconds can't slip past an async baseline read.
const baseline = fingerprintSync();
if (baseline === null) {
  console.error('warning: `git ls-files` failed here — wakes require a git repo to confirm changes.');
}

let debounceTimer = null;
let pollTimer = null;

console.log(`Watching ${watchDir} (debounce ${DEBOUNCE_MS}ms, poll ${POLL_MS}ms; exits only on a content change to a non-ignored file)...`);

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

// The only path to a wake: exit IFF the content fingerprint differs from the baseline.
function checkAndExit(reason) {
  const s = fingerprintSync();
  if (s === null) return;                            // git unavailable; keep watching
  if (baseline === null || s !== baseline) finish(`Detected change: ${reason}`);
  // else: nothing actually changed — stay idle, do not wake the agent
}

function finish(message) {
  if (debounceTimer) clearTimeout(debounceTimer);
  if (pollTimer) clearInterval(pollTimer);
  watcher.close();
  console.log(message);
  process.exit(0);
}
