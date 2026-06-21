#!/usr/bin/env python3
"""skill-review safe acquisition helper.

Downloads a GitHub repo as a tarball and extracts it WITHOUT any git/submodule
machinery, so nothing from the (untrusted) repo ever runs: no `.git`, no hooks,
no submodule config, no symlinks, no device nodes. This is the safe way to obtain
a hostile package before pointing scan.py at it.

Usage:
    python3 fetch.py <repo> [--ref <branch|tag|sha>] [--dest <dir>] [--json]

<repo> accepts:
    owner/name
    https://github.com/owner/name(.git)
    git@github.com:owner/name(.git)

This downloads and extracts ONLY. It never runs, imports, builds, or installs
anything from the fetched repo. Extraction is tar-slip safe, skips non-regular
members, and caps per-file / total size / file count to resist archive bombs.

Requirements: Python 3.6+ (uses f-strings), stdlib only — no third-party packages.
"""
import argparse
import json
import os
import re
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request

API_TARBALL = "https://api.github.com/repos/{owner}/{repo}/tarball/{ref}"
USER_AGENT = "skill-review-fetch/1.0 (+defensive-security-tooling)"

# Caps to resist decompression bombs. Generous enough for real skills/plugins.
MAX_FILE_BYTES = 50 * 1024 * 1024        # 50 MB per extracted file
MAX_TOTAL_BYTES = 500 * 1024 * 1024      # 500 MB extracted total
MAX_FILE_COUNT = 50_000                  # member count cap

# owner/name: GitHub allows alnum, hyphen, underscore, dot in both segments.
_SEGMENT = r"[A-Za-z0-9._-]+"
_OWNER_NAME_RE = re.compile(rf"^({_SEGMENT})/({_SEGMENT})$")


class FetchError(Exception):
    """A user-facing error: print the message, exit nonzero, no traceback."""


def normalize_repo(repo):
    """Normalize the accepted repo forms to 'owner/name'. Raises FetchError."""
    raw = repo.strip()
    spec = raw

    # git@github.com:owner/name(.git)
    m = re.match(r"^git@github\.com:(.+)$", spec)
    if m:
        spec = m.group(1)
    else:
        # https://github.com/owner/name(.git) (also http://, with optional www.)
        m = re.match(r"^https?://(?:www\.)?github\.com/(.+)$", spec)
        if m:
            spec = m.group(1)

    spec = spec.strip("/")

    # Reduce any extra path segments (e.g. /tree/main, /blob/main/x, /pull/3)
    # to just owner/name by taking the first two segments after the host.
    segments = [s for s in spec.split("/") if s]
    if len(segments) >= 2:
        owner, name = segments[0], segments[1]
    else:
        raise FetchError(
            f"could not parse repo {raw!r}; expected owner/name, "
            "https://github.com/owner/name, or git@github.com:owner/name"
        )

    # Strip a trailing .git from the name segment (e.g. owner/name.git).
    if name.endswith(".git"):
        name = name[:-4]

    if not _OWNER_NAME_RE.match(f"{owner}/{name}"):
        raise FetchError(
            f"could not parse repo {raw!r}; expected owner/name, "
            "https://github.com/owner/name, or git@github.com:owner/name"
        )
    # Defensive: reject path-traversal-looking segments outright.
    if owner in (".", "..") or name in (".", ".."):
        raise FetchError(f"invalid repo segments in {raw!r}")
    return owner, name


def download_tarball(owner, name, ref):
    """Download the tarball to a temp file. Returns the temp file path."""
    # URL-encode the ref so an unusual ref (spaces, '#', '?') can't malform the
    # request; '/' stays literal because refs legitimately contain slashes.
    encoded_ref = urllib.parse.quote(ref, safe="/") if ref else ""
    url = API_TARBALL.format(owner=owner, repo=name, ref=encoded_ref)
    if not ref:
        url = url.rstrip("/")  # empty ref -> default branch
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, headers=headers)
    fd, tmp_path = tempfile.mkstemp(prefix="skill-review-", suffix=".tar.gz")
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            with os.fdopen(fd, "wb") as out:
                downloaded = 0
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > MAX_TOTAL_BYTES:
                        raise FetchError(
                            "download exceeds size cap "
                            f"({MAX_TOTAL_BYTES} bytes); aborting"
                        )
                    out.write(chunk)
    except urllib.error.HTTPError as exc:
        _cleanup(tmp_path)
        if exc.code == 404:
            raise FetchError(
                f"repo or ref not found (404): {owner}/{name}"
                + (f"@{ref}" if ref else "")
                + " — check the name/ref, and set GITHUB_TOKEN for private repos"
            )
        if exc.code in (401, 403):
            raise FetchError(
                f"access denied ({exc.code}) for {owner}/{name}; "
                "rate-limited or private — set GITHUB_TOKEN"
            )
        raise FetchError(f"GitHub returned HTTP {exc.code} for {owner}/{name}")
    except urllib.error.URLError as exc:
        _cleanup(tmp_path)
        raise FetchError(f"network error fetching {owner}/{name}: {exc.reason}")
    except FetchError:
        _cleanup(tmp_path)
        raise
    except OSError as exc:
        _cleanup(tmp_path)
        raise FetchError(f"could not write download: {exc}")
    return tmp_path


def _cleanup(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _is_within(base, target):
    """True iff resolved target stays within resolved base directory."""
    base_real = os.path.realpath(base)
    target_real = os.path.realpath(target)
    return target_real == base_real or target_real.startswith(base_real + os.sep)


def safe_extract(tar_path, dest_dir):
    """Extract regular files + dirs only, tar-slip safe, with size/count caps.

    Skips symlinks, hardlinks, device, fifo, and any member escaping dest_dir.
    Returns the extracted top-level directory path. Raises FetchError on a
    bomb / malformed archive.
    """
    os.makedirs(dest_dir, exist_ok=True)
    total_bytes = 0
    file_count = 0
    top_levels = set()

    try:
        tar = tarfile.open(tar_path, mode="r:*")
    except tarfile.TarError as exc:
        raise FetchError(f"could not open tarball: {exc}")

    with tar:
        for member in tar:
            file_count += 1
            if file_count > MAX_FILE_COUNT:
                raise FetchError(
                    f"archive has too many members (> {MAX_FILE_COUNT}); aborting"
                )

            name = member.name
            # Reject absolute paths and any '..' traversal up front.
            if name.startswith("/") or os.path.isabs(name):
                continue
            if ".." in name.split("/"):
                continue

            # Skip everything that is not a plain file or directory: symlinks,
            # hardlinks, char/block devices, fifos. Nothing dangerous is created.
            if not (member.isfile() or member.isdir()):
                continue

            target = os.path.join(dest_dir, name)
            # Final resolved-path containment check (defeats crafted paths).
            if not _is_within(dest_dir, target):
                continue

            parts = name.split("/", 1)
            if parts[0]:
                top_levels.add(parts[0])

            if member.isdir():
                os.makedirs(target, exist_ok=True)
                continue

            # Regular file.
            if member.size > MAX_FILE_BYTES:
                raise FetchError(
                    f"member {name!r} exceeds per-file cap "
                    f"({MAX_FILE_BYTES} bytes); aborting"
                )
            total_bytes += member.size
            if total_bytes > MAX_TOTAL_BYTES:
                raise FetchError(
                    f"extracted size exceeds cap ({MAX_TOTAL_BYTES} bytes); aborting"
                )

            os.makedirs(os.path.dirname(target), exist_ok=True)
            src = tar.extractfile(member)
            if src is None:
                continue
            with src, open(target, "wb") as out:
                while True:
                    chunk = src.read(64 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)

    if not top_levels:
        raise FetchError("archive contained no extractable files")

    # GitHub tarballs have exactly one top-level dir (owner-repo-sha).
    if len(top_levels) == 1:
        return os.path.join(dest_dir, next(iter(top_levels)))
    return dest_dir


def fetch(repo, ref=None, dest=None):
    """Download + safely extract. Returns (extracted_path, owner_name, ref)."""
    owner, name = normalize_repo(repo)
    if dest:
        dest_dir = os.path.abspath(dest)
        os.makedirs(dest_dir, exist_ok=True)
    else:
        dest_dir = tempfile.mkdtemp(prefix=f"skill-review-{owner}-{name}-")

    tar_path = download_tarball(owner, name, ref)
    try:
        extracted = safe_extract(tar_path, dest_dir)
    finally:
        _cleanup(tar_path)
    return os.path.abspath(extracted), f"{owner}/{name}", ref


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Safely download + extract a GitHub repo (tarball, no git) "
                    "for skill-review. Nothing from the repo is executed.",
    )
    parser.add_argument(
        "repo",
        help="owner/name, https://github.com/owner/name, or git@github.com:owner/name",
    )
    parser.add_argument("--ref", help="branch, tag, or commit SHA (default: default branch).")
    parser.add_argument("--dest", help="destination dir (default: a new temp dir).")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON.")
    args = parser.parse_args(argv)

    try:
        path, repo_name, ref = fetch(args.repo, ref=args.ref, dest=args.dest)
    except FetchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    scan_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scan.py")
    if args.json:
        print(json.dumps({"path": path, "repo": repo_name, "ref": ref}, ensure_ascii=False))
    else:
        print(path)
        print(f"next: python3 {scan_py} {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
