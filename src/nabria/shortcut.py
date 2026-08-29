"""Telling the user how to bind the key on whatever they are running.

There is no cross-desktop way for a Wayland application to claim a global
shortcut. `org.freedesktop.portal.GlobalShortcuts` is the intended answer and
is implemented unevenly, so until that is settled the honest thing is to detect
the compositor and hand over the exact line to paste, rather than a paragraph
about where the keyboard settings live.

Detection is by environment variable only -- no processes are inspected. It is
allowed to be wrong: the fallback is a generic instruction, not an error.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import i18n

TOGGLE = "toggle"
CANCEL = "cancel"
SETTINGS = "settings"


def command(action: str = TOGGLE) -> str:
    return f"nabria {action}"


def detect() -> str:
    """hyprland | sway | niri | kde | gnome | "" """
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return "hyprland"
    if os.environ.get("NIRI_SOCKET"):
        return "niri"
    if os.environ.get("SWAYSOCK"):
        return "sway"
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or "").lower()
    for name in ("hyprland", "niri", "sway", "kde", "gnome"):
        if name in desktop:
            return name
    return ""


# Where the lines go, relative to the config directory, for the compositors
# whose configuration is a flat file that can be appended to. Deliberately not
# niri: its binds live *inside* a `binds {}` block, so appending at the end
# produces a file that parses and does nothing, which is worse than printing
# the lines and letting someone paste them in the right place.
CONFIG_FILES = {
    "hyprland": Path("hypr/hyprland.conf"),
    "sway": Path("sway/config"),
}

# How each of them pulls in another file. Followed when looking for an
# existing binding, because split configurations are the norm rather than the
# exception -- this project's own author runs one -- and a search that only
# reads the top-level file reports "not bound" for a key that is bound, then
# appends a second binding for it.
INCLUDES = {"hyprland": "source", "sway": "include"}

# Wrapped around the block so it can be found and removed later. Detection
# does *not* use these -- it looks for the command, so that a line somebody
# pasted by hand counts as bound. They are here for the removal, and for
# anyone reading their own config and wondering what put this there.
MARKER = "# nabria dictation shortcuts"
MARKER_END = "# end nabria dictation shortcuts"

# How deep to follow includes. Three is past every layout seen in the wild,
# and the visited set is what actually stops a cycle -- this is a guard
# against a pathological file, not against a normal one.
MAX_INCLUDE_DEPTH = 3


def _config_dir() -> Path:
    """`$XDG_CONFIG_HOME`, the same way `config.py` resolves it.

    A bare `~/.config` was wrong for anyone who moves their configuration --
    they would get a brand new file at a path their compositor never reads,
    and a message saying it worked.
    """
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def config_file() -> Path | None:
    """The compositor's configuration file, if it is really there.

    **Only if it exists.** Creating one at the default path is the wrong
    answer to "this user keeps their config somewhere else": a generated
    config, an include-only layout, a different directory -- in every one of
    those the new file is ignored and the wizard says the key is bound when it
    is not. Verified on the machine this was written on, which has no
    `hyprland.conf` at all and binds the key from a generated file.

    None also for KDE and GNOME, where the answer is a settings dialog, and
    for niri, where the right place is inside a block rather than at the end.
    """
    relative = CONFIG_FILES.get(detect())
    if relative is None:
        return None
    path = _config_dir() / relative
    return path if path.is_file() else None


def _read(path: Path) -> str:
    """Text, whatever the file's encoding turns out to be.

    `errors="replace"`, because a configuration file with one Latin-1 comment
    in it is somebody's real config and not an error condition -- and
    `UnicodeDecodeError` is a `ValueError`, so it slips past every `except
    OSError` between here and the interface and reaches the user as a button
    that visibly does nothing.
    """
    return path.read_bytes().decode("utf-8", errors="replace")


def _included(path: Path, kind: str, depth: int, seen: set[Path]) -> list[Path]:
    """Files this one pulls in, recursively."""
    directive = INCLUDES.get(kind)
    if directive is None or depth <= 0:
        return []
    found = []
    for line in _read(path).splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # `source = a/b.conf` and `include a/b.conf` -- the separator differs
        # and neither compositor cares about the spacing.
        head, _, rest = stripped.partition("=" if directive == "source" else " ")
        if head.strip() != directive:
            continue
        target = (rest or "").strip().strip('"\'')
        if not target:
            continue
        expanded = Path(target).expanduser()
        pattern = expanded if expanded.is_absolute() else path.parent / expanded
        # Globs, because `source = ~/.config/hypr/conf.d/*.conf` is a normal
        # thing to write and reading it literally finds nothing.
        for candidate in sorted(pattern.parent.glob(pattern.name)):
            if candidate.is_file() and candidate not in seen:
                seen.add(candidate)
                found.append(candidate)
                found.extend(_included(candidate, kind, depth - 1, seen))
    return found


def already_bound(path: Path) -> bool:
    """Whether this configuration already binds the key, however it came to.

    Checks for the command rather than for our own marker, so a line somebody
    pasted by hand counts -- that was the only way to do it until now, so most
    existing installations are exactly that case. Follows includes, because
    finding nothing in the top-level file of a split configuration and
    appending a second binding is how a compositor ends up warning about a
    duplicate shortcut.
    """
    wanted = command(TOGGLE)
    try:
        files = [path, *_included(path, detect(), MAX_INCLUDE_DEPTH, {path})]
    except OSError:
        return False
    for candidate in files:
        try:
            if wanted in _read(candidate):
                return True
        except OSError:
            continue
    return False


def snippet() -> str:
    """The block to append, markers included."""
    return "\n".join([MARKER, *instructions()[1:], MARKER_END]) + "\n"


def bind(path: Path | None = None) -> Path:
    """Append the shortcut lines. Returns the file written.

    Someone else's configuration file, often one they have spent years on, so
    the whole design is in what it does not do to it.

    **The write is atomic**: the new contents are assembled in memory, written
    to a temporary file beside the original, flushed to disk and renamed over
    it. Appending in place is not safe here -- a full disk or a quota gives a
    half-written line, so the file ends `bind = CTR`, the compositor reloads
    it, and the caller is told the write failed. Measured, not imagined: it
    reproduces exactly under `RLIMIT_FSIZE`. With a rename, either the whole
    block lands or the file is untouched.

    **The backup is written once.** A second run must not copy the file over
    its own backup -- that is how the pristine original is lost, and the
    second run is exactly the case where it is wanted.
    """
    path = path or config_file()
    if path is None:
        raise OSError("no configuration file for this desktop")

    existing = _read(path)
    backup = path.with_suffix(path.suffix + ".nabria-backup")
    if not backup.exists():
        backup.write_bytes(path.read_bytes())
        # The copy of a 0600 config must not be world-readable; `exec-once`
        # lines do sometimes carry something private.
        backup.chmod(path.stat().st_mode & 0o7777)

    # A file not ending in a newline would otherwise get the marker welded
    # onto the end of whatever its last line happens to be: comments out a
    # working bind, adds one that never parses. The commonest hand-edited
    # file there is.
    separator = "" if not existing or existing.endswith("\n") else "\n"
    updated = existing + separator + "\n" + snippet()

    temporary = path.with_suffix(path.suffix + ".nabria-new")
    try:
        with temporary.open("w", encoding="utf-8") as sink:
            sink.write(updated)
            sink.flush()
            os.fsync(sink.fileno())
        temporary.chmod(path.stat().st_mode & 0o7777)
        # Follows a symlink deliberately: dotfiles are commonly symlinked into
        # a repository, and replacing the link with a regular file would take
        # the file out of their repository without telling them.
        os.replace(temporary, path.resolve())
    finally:
        temporary.unlink(missing_ok=True)
    return path


def _without_block(text: str) -> str | None:
    """The text with our fenced block removed, or None if it was not there.

    Line-based and fence-based: everything from `MARKER` to `MARKER_END`
    inclusive, plus the blank line `bind` puts before it so that removing and
    re-adding does not accumulate blank lines. Nothing outside the fence is
    read, let alone rewritten -- a binding somebody pasted by hand looks
    exactly like ours from the outside and is theirs to keep.
    """
    lines = text.splitlines(keepends=True)
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == MARKER)
        end = next(
            i for i in range(start, len(lines)) if lines[i].strip() == MARKER_END
        )
    except StopIteration:
        # An opening fence with no closing one is somebody's half-edited file,
        # and guessing where the block ends there means deleting their lines.
        return None
    while start > 0 and not lines[start - 1].strip():
        start -= 1
    return "".join(lines[:start] + lines[end + 1:])


def unbind(path: Path | None = None) -> list[Path]:
    """Remove the block `bind` wrote. Returns the files changed, if any.

    Uninstalling used to leave the lines behind, so the key went on being
    bound to a command that no longer existed -- harmless, in that pressing it
    did nothing, and untidy in someone else's configuration file, which is not
    a good place to leave litter.

    Includes are searched as well as the top-level file. `bind` only ever
    writes to the top level, but configurations get reorganised between then
    and now, and a block that has been moved into an included file is still
    ours to take back.

    Same write as `bind`: temporary file, fsync, rename. A half-removed block
    is a worse outcome than one left in place.
    """
    path = path or config_file()
    if path is None:
        return []
    try:
        files = [path, *_included(path, detect(), MAX_INCLUDE_DEPTH, {path})]
    except OSError:
        files = [path]

    changed = []
    for candidate in files:
        try:
            existing = _read(candidate)
        except OSError:
            continue
        updated = _without_block(existing)
        if updated is None or updated == existing:
            continue
        temporary = candidate.with_suffix(candidate.suffix + ".nabria-new")
        try:
            with temporary.open("w", encoding="utf-8") as sink:
                sink.write(updated)
                sink.flush()
                os.fsync(sink.fileno())
            temporary.chmod(candidate.stat().st_mode & 0o7777)
            os.replace(temporary, candidate.resolve())
        except OSError:
            continue
        finally:
            temporary.unlink(missing_ok=True)
        changed.append(candidate)
    return changed


def instructions() -> list[str]:
    """Lines to show the user, the first of which is the sentence.

    Only that first line is translated. Everything after it is configuration
    to be pasted verbatim -- translating `bindsym` would be a bug that looks
    like a translation -- so those lines are literal in every language, and
    the wizard isolates them so a right-to-left page cannot reorder them.
    """
    where = detect()
    if where == "hyprland":
        return [
            i18n.t("shortcut.hyprland", path=i18n.ltr("~/.config/hypr/hyprland.conf")),
            f"bind = CTRL ALT, Q, exec, {command(TOGGLE)}",
            f"bind = CTRL ALT SHIFT, Q, exec, {command(CANCEL)}",
            f"bind = CTRL ALT, W, exec, {command(SETTINGS)}",
        ]
    if where == "sway":
        return [
            i18n.t("shortcut.sway", path=i18n.ltr("~/.config/sway/config")),
            f"bindsym Ctrl+Alt+q exec {command(TOGGLE)}",
            f"bindsym Ctrl+Alt+Shift+q exec {command(CANCEL)}",
        ]
    if where == "niri":
        return [
            i18n.t("shortcut.niri", path=i18n.ltr("~/.config/niri/config.kdl")),
            f'Ctrl+Alt+Q {{ spawn "nabria" "{TOGGLE}"; }}',
            f'Ctrl+Alt+Shift+Q {{ spawn "nabria" "{CANCEL}"; }}',
        ]
    if where == "kde":
        return [i18n.t("shortcut.kde"), command(TOGGLE)]
    if where == "gnome":
        return [i18n.t("shortcut.gnome"), command(TOGGLE)]
    return [i18n.t("shortcut.generic"), command(TOGGLE)]


def _cli() -> int:
    """`python3 -m nabria.shortcut --unbind` -- used by scripts/install.sh.

    A subcommand rather than shell in the installer: the block is fenced by
    constants defined here, and a sed expression in another file would be a
    second copy of them that nothing keeps in step.
    """
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--unbind":
        for path in unbind():
            print(f"removed the shortcut block from {path}")
        return 0
    print("usage: python3 -m nabria.shortcut --unbind", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
