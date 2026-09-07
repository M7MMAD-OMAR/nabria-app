"""Windows toast notifications through the system WinRT host."""

import base64
import subprocess
from xml.sax.saxutils import escape

from ..config import APP_ID


def send(summary: str, body: str = "", urgency: str = "normal") -> None:
    xml = ('<toast><visual><binding template="ToastGeneric"><text>'
           + escape(summary) + '</text><text>' + escape(body)
           + '</text></binding></visual></toast>')
    payload = base64.b64encode(xml.encode("utf-8")).decode("ascii")
    script = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime] > $null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{payload}')))
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{APP_ID}').Show($toast)
"""
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
