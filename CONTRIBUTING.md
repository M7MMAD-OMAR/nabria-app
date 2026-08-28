# Contributing

## Before anything else: run the checks

```sh
scripts/check.sh
```

Lint, the test suite, and the installer inside clean Ubuntu, Debian and Fedora
containers. It needs `podman` (or `docker`) and nothing else — no network, no
GitHub account, no CI run. CI calls this same script, so a green run here is a
green run there.

```sh
scripts/check.sh --quick      # lint and tests only, a few seconds
scripts/check.sh --packages   # install the built .rpm and .deb in containers
scripts/check.sh --engine     # build the engine and transcribe for real
scripts/check.sh --all        # every one of the above
```

**Run the script, not `pytest` alone.** Every packaging bug this project has
had was found by a container and none by reading the code: a typelib in a
separate package, a library path Debian uses and Fedora does not, `pw-record`
living somewhere else on Arch, an engine linking `libgomp`. Package names
cannot be reasoned about, only run.

## What this is, and what it will not become

The product is **"just talk"**: press a key, speak, press it again, the words
appear. That sentence is load-bearing. Live partial transcription,
note-taking, summarising, translation, an assistant, a tray menu, cloud sync
and a plugin system are explicit non-goals, and a pull request adding one will
be declined however good the code is. `PLAN.md` records why for each.

**A feature that needs explaining does not go in.** The best contribution is
usually the one that removes a thing somebody has to know.

Things that are always welcome:

- **A distribution that does not work.** The single most useful report there
  is. Say which, and paste what `scripts/install.sh` printed.
- **A desktop that does not work** — the shortcut portal is implemented
  unevenly across compositors and only some are verified.
- **Translations.** Everything is in `src/nabria/i18n.py`; see below.
- **A transcript that came out wrong**, with the audio if you have it
  (`keep_audio` in settings keeps every take).

## Writing the code

Read `CLAUDE.md` first. It carries the architecture and, more importantly, the
invariants — the things that look like arbitrary constants and are not.

**Comments explain *why*, and cite the measurement or the failure that forced
the code to be that way.** A comment restating what the line does is noise
here; the reason a threshold is -42 and not -40 is the most valuable thing on
the page. Several of the invariants exist only because a comment recorded the
bug that produced them, and they would have been reintroduced otherwise.

**No performance figures.** How fast anything runs belongs to the machine
running it, so a number measured on one laptop does not go in the README, the
site, a source comment or a commit message — it will be read as a property of
the software, and it is wrong for every reader whose hardware differs. State
requirements instead. What each model *needs* is the same everywhere.

**Nothing is lost, by design.** The transcript is written to disk before it is
typed; a failed injection falls back to the clipboard; a take that fails to
transcribe keeps its audio. Any change that could drop a user's words needs a
very good reason.

**No silently degraded state.** If something cannot work, say so in the log
and in the interface. The one time this project shipped a quiet fallback — an
indicator that silently stopped being a layer surface — it cost more debugging
than every loud failure put together.

## Adding a language

`src/nabria/i18n.py` holds every user-facing string in every language. There is
no gettext and no `.mo` files: at this size a dict costs one file and touches
nothing in packaging.

1. Add your code to `LANGUAGES`, and to `RTL` if the script runs right to left.
2. Add your translation to every entry in `STRINGS`. `tests/test_i18n.py` will
   tell you which ones you missed, which fields you dropped, and which strings
   you left in English.
3. Run `scripts/check.sh --quick`.

Three rules that only reveal themselves once a right-to-left language is
selected, all explained at the top of that file:

- `i18n.ltr()` around anything from outside the string table — paths, device
  names, engine errors, key names, any number with a sign or a unit.
- **Not** around bare digits. They take direction from the text around them
  already, and an isolate moves them to the wrong side of their unit.
- **Never** around text a user will copy: the isolate characters travel into
  the clipboard, where they are invisible in an editor and fatal to whatever
  parses the file they land in.

Use `i18n.label()` rather than `Gtk.Label` for anything with a start edge.

## Screenshots

`scripts/screenshots.py`, never by hand. It builds each window inside a profile
created seconds earlier, so what is published is what a new user sees — and so
that no microphone name, transcript or vocabulary prompt from the author's own
machine reaches a public repository. `git archive` would carry them into every
release tarball.

## Commits

Say what changed and *why it was wrong before*. If a bug is being fixed,
describe the failure it produced, not the lines that moved — the commit log is
the only place some of this project's reasoning survives.

Sign off with the usual `Co-Authored-By:` if a tool helped.

## Releases

Maintainers only, and in this order:

```sh
scripts/package.sh            # builds nabria.rpm and nabria.deb in containers
scripts/check.sh --packages   # installs them on clean Fedora, Debian, Ubuntu
scripts/release.sh vX.Y.Z     # tarball, installer, rpm, deb
```

`__version__` in `src/nabria/__init__.py` is the only place the version is
written; `release.sh` refuses a tag that disagrees with it. An **engine**
release must be a prerelease, or it steals `releases/latest` from the app and
the one-line installer starts 404ing. `docs/PACKAGING.md` has the rest.
