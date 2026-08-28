---
name: A distribution or desktop it does not support
about: It fails to install, or the shortcut or the indicator does not work on your desktop
title: ''
labels: ''
---

The most useful report there is. Package names cannot be reasoned about, only
run, and the shortcut portal is implemented unevenly across desktops.

**Which distribution and which desktop**


**What the installer printed**, if that is where it failed:

```sh
scripts/install.sh
```

**If the shortcut does not fire**, this says whether your desktop implements
the portal at all — a missing interface is an answer, not a fault:

```sh
busctl --user introspect org.freedesktop.portal.Desktop \
  /org/freedesktop/portal/desktop | grep -i globalshortcut
```

**If the indicator is in the wrong place or gets covered**, `gtk4-layer-shell`
is probably missing. The log says so on startup.
