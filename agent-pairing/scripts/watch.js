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
  'out',
  // Build / dependency / runtime trees. All are gitignored (so the content
  // fingerprint already excludes them), but recursively descending into them to
  // set inotify watches exhausts the per-user watch/instance limit (EMFILE) — the
  // reason the original recursive fs.watch crashed here. Pruning them keeps the
  // watch budget on real source dirs; the git poll remains the reliable backstop.
  '.venv',
  'venv',
  '__pycache__',
  '.pytest_cache',
  '.svelte-kit',
  '.mvh',
  '.playwright-mcp',
  'coverage'
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
const POLL_MS = process.env.OBSERVER_POLL_MS !== undefined ? Number(process.env.OBSERVER_POLL_MS) : 4000;

// Cap on the number of per-directory inotify watches we arm. fs.watch on Linux
// consumes a limited per-user resource (fs.inotify.max_user_instances, often 128);
// blowing past it throws EMFILE/ENOSPC and killed the original recursive watcher.
// We stay well under the cap and let the git poll cover any dirs we didn't watch.
const MAX_WATCHES = process.env.OBSERVER_MAX_WATCHES !== undefined ? Number(process.env.OBSERVER_MAX_WATCHES) : 96;

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

// Low-latency layer: a NON-recursive fs.watch on each non-ignored directory,
// walked once at arm time. We deliberately do NOT use `{recursive:true}` — on
// Linux it descends into every subtree (node_modules, .venv, …) and exhausts the
// inotify instance limit (EMFILE), which crashed the original watcher. Ignored
// dirs are pruned before descent, each watch has an 'error' handler so an inotify
// hiccup never crashes the process, and we stop at MAX_WATCHES. Anything not
// covered here (deeper dirs past the cap, dirs created after arm) is caught by the
// git poll below — the reliable backstop. Both funnel through the same
// fingerprint-confirmed checkAndExit(): no wake without a real content change.
const watchers = [];
let watchDegraded = false;

function safeWatch(dir) {
  if (watchers.length >= MAX_WATCHES) { watchDegraded = true; return false; }
  let w;
  try {
    w = fs.watch(dir, { persistent: true }, (event, filename) => {
      // filename is relative to `dir`; the git fingerprint is the real filter, so
      // a cheap ext check is enough to skip obvious churn (a stray trigger that
      // nets no content change simply won't wake us — checkAndExit confirms).
      if (filename && isIgnored(filename)) return;
      scheduleCheck(filename || dir);
    });
  } catch (e) {
    watchDegraded = true;
    return false;
  }
  w.on('error', () => {});  // inotify hiccup — the poll still covers us
  watchers.push(w);
  return true;
}

function armWatches(dir) {
  if (!safeWatch(dir)) return;
  let entries;
  try { entries = fs.readdirSync(dir, { withFileTypes: true }); }
  catch (e) { return; }
  for (const ent of entries) {
    if (!ent.isDirectory()) continue;
    if (ignoreDirs.includes(ent.name)) continue;
    if (watchers.length >= MAX_WATCHES) { watchDegraded = true; return; }
    armWatches(path.join(dir, ent.name));
  }
}

armWatches(watchDir);
if (watchDegraded) {
  console.error(`note: fs.watch coverage capped at ${watchers.length} dirs (MAX_WATCHES=${MAX_WATCHES}); the ${POLL_MS}ms git poll is the backstop for the rest.`);
}

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
  for (const w of watchers) { try { w.close(); } catch (e) {} }
  console.log(message);
  process.exit(0);
}
