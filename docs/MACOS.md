# macOS — analysis

**The blocker is a bill, not a build.** Everything technical here is tractable
and some of it is easier than Linux. What is not tractable without spending
money is *distributing* it: macOS ties the two permissions this application
cannot work without to the app's code signature, and a code signature that
macOS trusts requires the Apple Developer Program at **$99 a year**. That cost
is stated first because it decides whether any of the rest is worth starting,
and it was ruled out earlier in this project for the CI question.

## Why the signature is not optional here

Two permissions are needed, both granted per-app by the user in System
Settings, and both recorded by TCC — Apple's permissions database — against the
app's **code identity**, not its path or its name.

| | needs | why |
|---|---|---|
| hearing a global hotkey | Input Monitoring | listening for keys the app did not receive |
| typing the text into another app | Accessibility | posting events into a process you do not own |

The consequence for an unsigned build is not "an extra warning on first run".
It is that the identity changes whenever the binary is rebuilt or re-signed, so
macOS treats it as a different application and the permission the user granted
no longer applies to it. Every update becomes: open System Settings, find the
app, remove it from two lists, add it back. That is not a rough edge — it is
the update path, and it is worse than having no update path.

With a Developer ID certificate the identity is stable across builds, and the
permissions survive an update the way they do for every other application.

Notarisation itself costs nothing extra once enrolled, and Apple's own forums
are explicit that not being enrolled is the same as not having paid.

## What would actually be easier than Linux

Worth recording, because it is the reverse of what people assume:

| | |
|---|---|
| **the engine** | whisper.cpp's Metal backend on Apple Silicon is fast and, unlike Vulkan on Linux, needs no driver decision at all. `gpu.py` — 249 lines of Vulkan enumeration written to *refuse* integrated cards — has no counterpart to write. There is one GPU and it is the right one |
| **the hotkey** | Carbon's `RegisterEventHotKey` claims a system-wide shortcut without the Accessibility permission. Only the *typing* half needs it |
| **the overlay** | an `NSPanel` that is non-activating, always on top and ignores mouse events is a handful of properties. The same job as the layer surface, without the protocol |
| **audio** | one CoreAudio API on every Mac, rather than PipeWire, PulseAudio and ALSA and a guess about which is running |

## What would be harder

| | |
|---|---|
| **GTK 4** | it runs on macOS through the Quartz backend, and it looks like a GTK application on a Mac, which is to say wrong. The wizard is the first thing a user sees. This is the same delivery problem as Windows plus an aesthetic one that Windows does not have |
| **the container story** | none — the same gap as Windows, and stated once, in [WINDOWS.md](WINDOWS.md#the-risk-that-is-being-accepted). It will stop being true for both platforms at the same time or for neither |
| **the clipboard borrow** | `inject.py` captures the previous clipboard contents with their MIME type and restores them 1.5 s later. `NSPasteboard` can do it, but its change-counter semantics are not the same as the "stand down if anything was copied meanwhile" rule the Linux side implements, and that rule exists so a newer copy is never destroyed |

## One command, and updates

Homebrew, and it is genuinely good here:

```
brew install --cask nabria
brew upgrade --cask
```

A cask is a Ruby file in a public tap listing the download URL, a checksum and
the app bundle name. The same shape as the AUR `PKGBUILD` this project already
ships, and — like Copr and the AUR — the first submission needs a person with
an account.

But a cask points at a `.dmg` or `.zip`, and Gatekeeper still evaluates what is
inside it. Homebrew solves the *finding and updating*; it does not solve the
signature. The $99 comes first or the cask installs something the user cannot
grant permissions to.

## Recommendation

**Not until there is a Developer ID**, and that is a purchase decision rather
than an engineering one.

If the account is bought, the order is: Metal engine build → CoreAudio recorder
→ `RegisterEventHotKey` → Accessibility-based injection → sign, notarise, cask.
The GTK question can be deferred behind a command-line-only first release the
same way the Windows plan defers it, and on macOS that deferral is more
attractive, because a GTK wizard on a Mac is the weakest part of the whole idea
and a Terminal command is not pretending to be anything.

What would change the answer without spending anything: nothing found. Ad-hoc
signing, self-signed certificates and `spctl` overrides all produce an identity
that changes on rebuild, which is the exact failure described above.

Windows is planned in [WINDOWS.md](WINDOWS.md), where the equivalent blocker —
SmartScreen on an unsigned installer — is a warning the user can click through
rather than a permission they cannot grant.
