# Nabria — local voice dictation for Wayland.
#
# Built by scripts/package.sh in a Fedora container, and suitable as-is for
# Copr, which fetches both sources by URL.
#
# Version is checked against src/nabria/__init__.py by tests/test_packaging.py,
# because a spec that says one thing while the source says another is the
# drift this project has already had once.

%global appid    com.sbarah.Nabria
%global engine   engine-v1.9.3-1
%global debug_package %{nil}

Name:           nabria
Version:        0.4.0
Release:        1%{?dist}
Summary:        Local voice dictation — press a key, speak, the words are typed

License:        MIT
URL:            https://github.com/M7MMAD-OMAR/nabria-app
Source0:        %{url}/releases/download/v%{version}/nabria.tar.gz
Source1:        %{url}/releases/download/%{engine}/whisper-server-linux-x86_64

# The bundled engine is a compiled x86_64 binary, so this package is not noarch
# even though every line of Nabria itself is Python.
ExclusiveArch:  x86_64

Requires:       python3 >= 3.10
Requires:       python3-gobject
Requires:       python3-cairo
Requires:       gtk4
# pw-record is the only way to record; wl-clipboard is how the transcript is
# delivered; wtype sends the paste keystroke.
Requires:       pipewire-utils
Requires:       wl-clipboard
Requires:       wtype
# The bundled engine links the Vulkan loader. It runs on the CPU without a
# driver, but it will not start without the loader itself -- measured on a
# minimal Debian 12, where it is absent on machines that never had a GPU driver.
Requires:       vulkan-loader

# Recommends, deliberately not Requires. Without it the indicator falls back to
# an ordinary window that a fullscreen app can cover -- worse, but working --
# and there are distributions that do not package it at all. A hard dependency
# would make the package uninstallable there rather than merely degraded.
Recommends:     gtk4-layer-shell

%description
Press a key, say what you mean, press it again, and the words appear in
whatever you were typing into. Everything happens on your machine: no account,
no cloud, nothing uploaded, and it works with the network off.

Arabic is first-class, including spoken dialect. English and the other ninety
or so languages Whisper knows work too.

The transcription model is downloaded on first launch and chosen to suit your
hardware, because the largest model is only the right one where there is a
discrete GPU.

%prep
%autosetup -n nabria

%build
# Nothing to compile. The engine arrives built; see scripts/release-engine.sh.

%install
. packaging/layout.sh
stage_nabria %{buildroot} . %{SOURCE1}

%files
%license LICENSE
%doc README.md
%{_bindir}/nabria
%{_prefix}/lib/nabria/
%{_libexecdir}/nabria/
%{_userunitdir}/app-%{appid}.service
%{_userunitdir}/nabria.service
%{_datadir}/applications/%{appid}.desktop

%post
# The unit ships enabled-by-default via its [Install] section, but a --user
# unit cannot be enabled for users who do not exist yet at install time, so
# this is left to the user: `systemctl --user enable --now nabria`.
%systemd_user_post app-%{appid}.service

%preun
%systemd_user_preun app-%{appid}.service

%changelog
* Fri Aug 28 2026 Nabria <noreply@github.com>
- See the release notes on GitHub; this file does not restate the version.
