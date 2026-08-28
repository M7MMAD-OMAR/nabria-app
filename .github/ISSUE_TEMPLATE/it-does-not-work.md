---
name: It does not work
about: Dictation does nothing, or types nothing, or the app will not start
title: ''
labels: ''
---

**Read the log first — it usually says which of these it is.**

```sh
tail -40 ~/.local/state/nabria/nabria.log
```

Every take is recorded there with its measured level, which separates "the
hotkey did nothing" from "the microphone was silent". Those have completely
different answers and look identical from the outside; this is the single most
common misdiagnosis in the project's history. Paste what it says.

**What happened**


**Your system** — the output of this, as-is:

```sh
echo "$XDG_CURRENT_DESKTOP / $XDG_SESSION_TYPE"; cat /etc/os-release | head -2
nabria status; systemctl --user is-active nabria
```


**How you installed it** — rpm, deb, AUR, the one-line installer, or a clone.
