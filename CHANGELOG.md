# Changelog

What changed in each release, in the words of somebody using it rather than
somebody writing it. Every published version has a section here, and
`scripts/release.sh` refuses to publish a tag that does not.

## 0.4.6

2026-09-05

- An Arabic transcript now reaches XWayland windows instead of silently landing
  in the clipboard.

## 0.4.5

2026-09-03

- A warning arrives during the take, not after it, when the microphone is not
  being heard.
- A take is kept, with the reason shown, when the GPU refuses to start.
- Non-ASCII transcripts are typed rather than dropped, and a paste that falls
  through to typing says why.
- The paste keystroke no longer reports success while delivering nothing.

## 0.4.4

2026-08-30

- Settings written as text no longer make the app refuse a GPU that is there.
- A GPU known to be absent stops being asked for.

## 0.4.3

2026-08-29

- The settings window takes a dictation itself, for machines where no key can
  be bound.
- Uninstalling removes the shortcut block it added to your compositor config,
  and leaves anything you wrote by hand.
- The desktop's accent colour is read instead of assumed.
- The setup window's layout is fixed, and its pictures redone to match.

## 0.4.2

2026-08-29

- Arabic and English throughout the application, right to left included.
- The wizard binds the global key itself on the desktops where that can be
  done honestly.
- A model already on the machine is used instead of downloaded again.
- Adopting an existing model no longer reports success while doing the wrong
  thing.

## 0.3.1

2026-08-28

- Uninstalling is covered by the test suite.

## 0.3.0

2026-08-28

- Packages for Fedora, Debian and Ubuntu, and a PKGBUILD for Arch.

## 0.2.1

2026-08-28

- Cleanup, no behaviour changes.

## 0.2.0

2026-08-28

The first release anybody could install. A prebuilt engine, so installing does
not mean compiling; a first-run wizard for the model, the microphone and the
shortcut; a settings window; and the indicator drawn as five marks.
