"""Local voice dictation for Wayland: speak, and the text is typed."""

# The application's version, and the only place it is written down.
# scripts/release.sh refuses to publish a tag that disagrees with this, the
# way scripts/release-engine.sh derives its release name from engine/VERSION.
# Before that check existed this said 0.1.0 while v0.2.0 was tagged, and
# nothing anywhere noticed.
__version__ = "0.2.0"
