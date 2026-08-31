"""桌面通知：Windows 原生 Toast（PowerShell，无第三方依赖）。

监听发现 HR 回复、发送完成等关键事件时弹出系统通知；
非 Windows 或调用失败时静默降级（绝不影响主流程）。
"""

import base64
import subprocess
import sys

_POWERSHELL_TOAST_TEMPLATE = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$title = __TITLE__
$message = __MESSAGE__
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml("<toast><visual><binding template=`"ToastText02`"><text id=`"1`">$title</text><text id=`"2`">$message</text></binding></visual></toast>")
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("OpenJob.JobHunter").Show($toast)
"""


def notify_desktop(title: str, message: str, config: dict | None = None) -> bool:
    """弹出 Windows Toast。config 可传 {"monitor": {"desktop_notify": false}} 关闭。

    返回是否成功弹出；失败一律静默返回 False。
    """
    if sys.platform != "win32":
        return False
    monitor_cfg = (config or {}).get("monitor", {}) if isinstance(config, dict) else {}
    if monitor_cfg.get("desktop_notify") is False:
        return False

    title = str(title or "OpenJob").replace("\n", " ")[:60]
    message = str(message or "").replace("\n", " ")[:180]
    script = _POWERSHELL_TOAST_TEMPLATE.replace("__TITLE__", _ps_quote(title)).replace(
        "__MESSAGE__", _ps_quote(message)
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-EncodedCommand", encoded],
            capture_output=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _ps_quote(value: str) -> str:
    """把任意文本安全地嵌入 PowerShell 单引号字符串。"""
    return "'" + value.replace("'", "''") + "'"
