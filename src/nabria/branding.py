"""Shared text attribution linking to the maker's website."""

from gi.repository import Gtk

from . import i18n


def footer() -> Gtk.Widget:
    link = Gtk.LinkButton(uri="https://sbarah.com", label=i18n.t("brand.credit"))
    link.set_halign(Gtk.Align.CENTER)
    link.set_margin_bottom(12)
    return link
