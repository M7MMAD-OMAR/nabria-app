"""Shared attribution, bundled locally so opening a window never needs a network."""

from pathlib import Path

from gi.repository import Gtk

from . import i18n


def footer() -> Gtk.Widget:
    link = Gtk.LinkButton(uri="https://sbarah.com")
    link.set_halign(Gtk.Align.CENTER)
    link.set_margin_bottom(12)
    link.set_tooltip_text(i18n.t("brand.credit"))
    row = Gtk.Box(spacing=8)
    row.set_valign(Gtk.Align.CENTER)
    row.append(i18n.label(i18n.t("brand.credit")))
    logo = Gtk.Picture.new_for_filename(str(Path(__file__).parent / "assets/sbarah-logo.png"))
    logo.set_size_request(70, 37)
    logo.set_content_fit(Gtk.ContentFit.CONTAIN)
    row.append(logo)
    link.set_child(row)
    return link
