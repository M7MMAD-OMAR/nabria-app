"""First run: get from "nothing installed" to "it typed my words".

Opened when `config.needs_setup` says so: on a first run, and again whenever
there is no model on disk. The second condition is why it cannot get stuck
marked done while the app is unusable -- it doubles as the repair path when
someone deletes a model.

Five steps and no more, because the whole promise is that there is nothing to
configure: say what you speak, pick a model, fetch it, check the microphone is
heard, learn the shortcut. Everything else has a sensible default and lives in
the settings window for the few who want it.

The styling comes from the shipped palette rather than the desktop theme, for
the same reason the indicator's does: this has to look considered on a bare
Sway session, not only on a fully themed desktop.
"""

from __future__ import annotations

import os
import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from . import audio, config, gpu, models, shortcut, theme  # noqa: E402

# Below this the microphone is under the silence gate and every take would be
# thrown away, so the test has to fail rather than politely report a number.
GOOD_ENOUGH_DBFS = -42.0

STYLE_TEMPLATE = """
window.nabria-setup, .nabria-setup {{ background-color: {surface}; color: {on_surface}; }}
.nabria-title {{ font-size: 20pt; font-weight: 700; }}
.nabria-lede {{ opacity: 0.72; }}
.nabria-card {{
  background-color: {card};
  border: 1px solid {outline};
  border-radius: 14px;
  padding: 14px;
}}
.nabria-card.selected {{ border-color: {primary}; }}
.nabria-choice-name {{ font-weight: 700; }}
.nabria-hint {{ opacity: 0.6; font-size: 10pt; }}
.nabria-good {{ color: {primary}; font-weight: 700; }}
.nabria-bad {{ color: {error}; font-weight: 700; }}
.nabria-key {{
  font-family: monospace;
  background-color: {card};
  border: 1px solid {outline};
  border-radius: 8px;
  padding: 8px 10px;
}}
/* The primary button carries the app's accent rather than the desktop's.
   GTK's stock suggested-action is the system accent -- blue on a default
   install -- which next to a coral indicator reads as two applications. */
.nabria-setup button.suggested-action {{
  background-image: none;
  background-color: {primary};
  color: {surface};
  font-weight: 700;
  border: none;
  border-radius: 10px;
  padding: 8px 18px;
}}
.nabria-setup button.suggested-action:hover {{ background-color: {primary_hover}; }}
.nabria-setup button {{
  background-image: none;
  background-color: {card};
  color: {on_surface};
  border: 1px solid {outline};
  border-radius: 10px;
  padding: 8px 18px;
}}
.nabria-setup progressbar progress {{ background-color: {primary}; }}
.nabria-setup checkbutton radio:checked {{
  background-color: {primary};
  background-image: none;
  border-color: {primary};
  color: {surface};
}}
"""


def install_style(settings: dict) -> None:
    palette = theme.load(config.CONFIG_DIR, bool(settings.get("follow_desktop_palette")))
    theme.add_css(STYLE_TEMPLATE.format(
        surface=theme.to_hex(palette["surface_container"]),
        card=theme.to_hex(palette["card"]),
        outline=theme.to_hex(palette["outline_variant"]),
        primary=theme.to_hex(palette["primary"]),
        primary_hover=theme.to_hex(palette["primary"], lighten=0.12),
        error=theme.to_hex(palette["error"]),
        on_surface=theme.to_hex(palette["on_surface"]),
    ))


class Choice(Gtk.Box):
    """One selectable option, as a card rather than a combo box entry.

    A dropdown would hide exactly the information these choices turn on -- how
    big a download is, whether this machine can run it faster than speech.
    Both choice pages use this: the language page used to re-type the whole
    thing inline, so the two consecutive screens could drift apart.
    """

    def __init__(self, name: str, summary: str = "", *, note: str = "",
                 badge: str = "", trailing: str = ""):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.add_css_class("nabria-card")

        self.radio = Gtk.CheckButton()
        self.radio.set_valign(Gtk.Align.CENTER)
        # The card carries the selection, not just the radio: at this size a
        # 16px dot is easy to miss, and the choice is the whole page.
        self.radio.connect("toggled", self._on_toggled)
        self.append(self.radio)

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        column.set_hexpand(True)

        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label=name, xalign=0)
        title.add_css_class("nabria-choice-name")
        heading.append(title)
        for text, style in ((trailing, "nabria-hint"), (badge, "nabria-good")):
            if not text:
                continue
            label = Gtk.Label(label=text, xalign=0)
            label.add_css_class(style)
            label.add_css_class("nabria-hint")
            heading.append(label)
        column.append(heading)

        if summary:
            body = Gtk.Label(label=summary, xalign=0, wrap=True)
            body.add_css_class("nabria-lede")
            column.append(body)

        # Reserved for the thing that turns a preference into a mistake.
        if note:
            warning = Gtk.Label(label=note, xalign=0, wrap=True)
            warning.add_css_class("nabria-bad")
            warning.add_css_class("nabria-hint")
            column.append(warning)

        self.append(column)

    def _on_toggled(self, button: Gtk.CheckButton) -> None:
        if button.get_active():
            self.add_css_class("selected")
        else:
            self.remove_css_class("selected")


def group(cards: list[Choice]) -> None:
    """Make a list of cards one radio group."""
    for card in cards[1:]:
        card.radio.set_group(cards[0].radio)


class Wizard(Gtk.ApplicationWindow):
    def __init__(self, application: Gtk.Application, settings: dict, on_finished):
        super().__init__(application=application, title="Nabria")
        self.settings = settings
        self.on_finished = on_finished
        self.set_default_size(560, 520)
        self.add_css_class("nabria-setup")
        install_style(settings)

        self.has_gpu = gpu.plan("auto").use_gpu

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT)
        self.stack.set_margin_top(24)
        self.stack.set_margin_bottom(24)
        self.stack.set_margin_start(24)
        self.stack.set_margin_end(24)
        self.set_child(self.stack)

        self.stack.add_named(self._welcome_page(), "welcome")
        self.stack.add_named(self._language_page(), "language")
        self.stack.add_named(self._model_page(), "model")
        self.stack.add_named(self._download_page(), "download")
        self.stack.add_named(self._microphone_page(), "microphone")
        self.stack.add_named(self._shortcut_page(), "shortcut")
        self.stack.set_visible_child_name("welcome")

    # -- scaffolding -------------------------------------------------------

    def _page(self, title: str, lede: str) -> Gtk.Box:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        heading = Gtk.Label(label=title, xalign=0)
        heading.add_css_class("nabria-title")
        page.append(heading)
        if lede:
            subtitle = Gtk.Label(label=lede, xalign=0, wrap=True)
            subtitle.add_css_class("nabria-lede")
            page.append(subtitle)
        return page

    def _buttons(self, page: Gtk.Box, *buttons: Gtk.Widget) -> None:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.set_halign(Gtk.Align.END)
        row.set_margin_top(8)
        for button in buttons:
            row.append(button)
        page.append(row)

    # -- pages -------------------------------------------------------------

    def _welcome_page(self) -> Gtk.Box:
        page = self._page(
            "Just talk.",
            "Press a key, say what you mean, press it again. The words appear "
            "in whatever you were typing into.\n\n"
            "Everything happens on this machine. No account, nothing uploaded.",
        )
        start = Gtk.Button(label="Set up")
        start.add_css_class("suggested-action")
        start.connect("clicked", lambda _b: self.stack.set_visible_child_name("language"))
        self._buttons(page, start)
        return page

    def _language_page(self) -> Gtk.Box:
        page = self._page(
            "What will you speak?",
            "Telling it beats letting it guess. Detection runs per phrase, and "
            "a phrase of near-silence is what it most often gets wrong — "
            "confidently, and in the wrong language.",
        )

        # Preselect from the locale. Someone whose system is already in Arabic
        # should not have to tell us that too.
        locale = (os.environ.get("LC_ALL") or os.environ.get("LANG") or "").lower()
        preferred = "ar" if locale.startswith("ar") else "en" if locale else "auto"

        cards = []
        self.languages: list[tuple[str, Gtk.CheckButton]] = []
        for code, preset in config.LANGUAGE_PRESETS.items():
            card = Choice(preset["label"], preset["summary"])
            cards.append(card)
            self.languages.append((code, card.radio))
            page.append(card)
        group(cards)
        for code, radio in self.languages:
            radio.set_active(code == preferred)

        onward = Gtk.Button(label="Next")
        onward.add_css_class("suggested-action")
        onward.connect("clicked", lambda _b: self._choose_language())
        self._buttons(page, onward)
        return page

    def _choose_language(self) -> None:
        code = next(
            (code for code, radio in self.languages if radio.get_active()), "auto"
        )
        preset = config.LANGUAGE_PRESETS[code]
        self.settings["language"] = code
        # The dialect prompt is the whole reason Arabic works well here, and
        # nobody would ever find it in a config file. Setting it only when it
        # is empty means this never overwrites something the user wrote.
        if preset["vocabulary"] and not str(self.settings.get("vocabulary") or "").strip():
            self.settings["vocabulary"] = preset["vocabulary"]
        config.save(self.settings)
        self.stack.set_visible_child_name("model")

    def _model_page(self) -> Gtk.Box:
        recommended = models.recommended(self.has_gpu)
        page = self._page(
            "Choose how good it should be",
            f"Bigger is more accurate and slower to download. "
            f"{'A discrete GPU was found, so the best one is worth it.' if self.has_gpu else 'No discrete GPU here, so the smaller ones are the practical choice.'}",
        )

        self.choices = []
        for model in models.CATALOG.values():
            card = Choice(
                model.key, model.summary,
                trailing=f"{model.megabytes} MB",
                badge="recommended for this machine" if model.key == recommended.key else "",
                note="No discrete GPU found — this would run slower than you speak."
                     if model.needs_gpu and not self.has_gpu else "",
            )
            card.model = model
            self.choices.append(card)
            page.append(card)
        group(self.choices)
        for card in self.choices:
            card.radio.set_active(card.model.key == recommended.key)

        fetch = Gtk.Button(label="Download")
        fetch.add_css_class("suggested-action")
        fetch.connect("clicked", lambda _b: self._begin_download())
        self._buttons(page, fetch)
        return page

    def _download_page(self) -> Gtk.Box:
        page = self._page("Downloading", "")
        self.progress = Gtk.ProgressBar(show_text=True)
        page.append(self.progress)
        self.download_note = Gtk.Label(label="", xalign=0, wrap=True)
        self.download_note.add_css_class("nabria-hint")
        page.append(self.download_note)

        self.download_next = Gtk.Button(label="Next")
        self.download_next.add_css_class("suggested-action")
        self.download_next.set_sensitive(False)
        self.download_next.connect(
            "clicked", lambda _b: self.stack.set_visible_child_name("microphone")
        )
        self.download_retry = Gtk.Button(label="Try again")
        self.download_retry.set_visible(False)
        self.download_retry.connect("clicked", lambda _b: self._begin_download())
        self._buttons(page, self.download_retry, self.download_next)
        return page

    def _microphone_page(self) -> Gtk.Box:
        page = self._page(
            "Can it hear you?",
            "Press Test and say something for four seconds. This measures the "
            "same level the silence guard uses, so if it passes here, takes "
            "will not be thrown away as silent.",
        )
        self.mic_result = Gtk.Label(label="", xalign=0, wrap=True)
        page.append(self.mic_result)

        test = Gtk.Button(label="Test")
        test.connect("clicked", lambda _b: self._test_microphone())
        skip = Gtk.Button(label="Skip")
        skip.connect(
            "clicked", lambda _b: self.stack.set_visible_child_name("shortcut")
        )
        self._buttons(page, skip, test)
        return page

    def _shortcut_page(self) -> Gtk.Box:
        page = self._page(
            "One last thing: the key",
            "Wayland gives no way for an application to claim a shortcut for "
            "itself, so this part is yours.",
        )
        for line in shortcut.instructions():
            label = Gtk.Label(label=line, xalign=0, wrap=True, selectable=True)
            label.add_css_class("nabria-key")
            page.append(label)

        done = Gtk.Button(label="Done")
        done.add_css_class("suggested-action")
        done.connect("clicked", lambda _b: self._finish())
        self._buttons(page, done)
        return page

    # -- actions -----------------------------------------------------------

    def _selected_model(self) -> models.Model:
        for choice in self.choices:
            if choice.radio.get_active():
                return choice.model
        return models.recommended(self.has_gpu)

    def _begin_download(self) -> None:
        model = self._selected_model()
        if models.installed(config.MODEL_DIR, model):
            # Already fetched, almost always by install.sh. Showing a download
            # page that completes instantly reads as a glitch.
            self.settings["model"] = str(config.MODEL_DIR / model.filename)
            config.save(self.settings)
            self.stack.set_visible_child_name("microphone")
            return
        self.stack.set_visible_child_name("download")
        self.download_next.set_sensitive(False)
        self.download_retry.set_visible(False)
        self.progress.set_fraction(0.0)
        self.progress.set_text(f"{model.key} · 0 / {model.megabytes} MB")
        self.download_note.set_text("")

        def report(done: int, total: int) -> None:
            GLib.idle_add(self._show_progress, model, done, total)

        def work() -> None:
            try:
                path = models.download(model, config.MODEL_DIR, report)
            except models.DownloadError as exc:
                GLib.idle_add(self._download_failed, str(exc))
                return
            GLib.idle_add(self._download_done, model, path)

        threading.Thread(target=work, daemon=True, name="nabria-fetch").start()

    def _show_progress(self, model, done: int, total: int) -> bool:
        self.progress.set_fraction(done / max(total, 1))
        self.progress.set_text(
            f"{model.key} · {done // 1_000_000} / {total // 1_000_000} MB"
        )
        return GLib.SOURCE_REMOVE

    def _download_done(self, model, path) -> bool:
        self.progress.set_fraction(1.0)
        self.progress.set_text("verified")
        self.download_note.set_text(str(path))
        self.settings["model"] = str(path)
        config.save(self.settings)
        self.download_next.set_sensitive(True)
        return GLib.SOURCE_REMOVE

    def _download_failed(self, message: str) -> bool:
        self.progress.set_text("failed")
        self.download_note.set_text(message)
        self.download_retry.set_visible(True)
        return GLib.SOURCE_REMOVE

    def _test_microphone(self) -> None:
        self.mic_result.set_text("listening…")

        def work() -> None:
            try:
                level = audio.measure(4.0)
            except audio.AudioError as exc:
                GLib.idle_add(self._microphone_result, None, str(exc))
                return
            GLib.idle_add(self._microphone_result, level, "")

        threading.Thread(target=work, daemon=True, name="nabria-mic-test").start()

    def _microphone_result(self, level, error: str) -> bool:
        for name in ("nabria-good", "nabria-bad"):
            self.mic_result.remove_css_class(name)
        if error:
            self.mic_result.add_css_class("nabria-bad")
            self.mic_result.set_text(error)
        elif level is not None and level > GOOD_ENOUGH_DBFS:
            self.mic_result.add_css_class("nabria-good")
            self.mic_result.set_text(f"Heard you clearly ({level:.0f} dBFS).")
            self.stack.set_visible_child_name("shortcut")
        else:
            self.mic_result.add_css_class("nabria-bad")
            self.mic_result.set_text(
                f"Barely anything ({level:.0f} dBFS). Check that the right input "
                "is selected and unmuted, then test again."
            )
        return GLib.SOURCE_REMOVE

    def _finish(self) -> None:
        self.settings["setup_done"] = True
        config.save(self.settings)
        self.close()
        self.on_finished()
