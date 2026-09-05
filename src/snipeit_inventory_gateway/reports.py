from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import GatewayConfig
from .mail_contract import PRODUCT, mail_subject

BRAND_RED = "#FE223C"
BRAND_DARK = "#15171A"
PALE_RED = "#FFF0F2"
LINE = "#D9DDE3"


def _timezone(name: str) -> dt.tzinfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name == "Europe/Moscow":
            return dt.timezone(dt.timedelta(hours=3), "MSK")
        return dt.UTC


@dataclass(frozen=True)
class InventoryRow:
    computer: str
    serial: str
    owner: str
    agent: str
    last_inventory: str
    age_days: int
    status: str
    last_error: str


def _escape(value: Any) -> str:
    return html.escape(str(value or "—"), quote=True)


def _parse_datetime(value: Any) -> dt.datetime | None:
    if isinstance(value, dict):
        value = value.get("datetime") or value.get("date") or value.get("formatted")
    text = str(value or "").strip()
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            parsed = dt.datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.UTC)
            return parsed.astimezone(dt.UTC)
        except ValueError:
            pass
    for pattern in ("%H:%M %d.%m.%Y", "%d.%m.%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(text, pattern).replace(tzinfo=dt.UTC)
        except ValueError:
            pass
    return None


def _custom(asset: dict, handle: str | None) -> Any:
    if not handle:
        return None
    fields = asset.get("custom_fields") or {}
    wanted = handle.casefold()
    for label, value in fields.items():
        field_name = str(value.get("field") or "").casefold() if isinstance(value, dict) else ""
        if str(label).casefold() == wanted or field_name == wanted:
            return value.get("value") if isinstance(value, dict) else value
    return None


def _category_id(asset: dict) -> int | None:
    value = asset.get("category") or asset.get("category_id")
    if isinstance(value, dict):
        value = value.get("id")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def build_inventory_rows(
    assets: list[dict], config: GatewayConfig, current: dt.datetime | None = None
) -> list[InventoryRow]:
    current = current or dt.datetime.now(dt.UTC)
    handles = config.snipeit.custom_field_map
    allowed_categories = set(config.notifications.weekly_category_ids)
    rows: list[InventoryRow] = []
    for asset in assets:
        category = _category_id(asset)
        if allowed_categories and category is not None and category not in allowed_categories:
            continue
        last_success = _parse_datetime(_custom(asset, handles.get("last_success")))
        created = _parse_datetime(asset.get("created_at"))
        reference = last_success or created or current
        age = max(0, int((current - reference).total_seconds() // 86400))
        if last_success is None:
            status = "never"
        elif age >= config.notifications.weekly_critical_days:
            status = "critical"
        elif age >= config.notifications.weekly_stale_days:
            status = "overdue"
        else:
            status = "current"
        assigned = asset.get("assigned_to") or {}
        owner = (
            assigned.get("name") or assigned.get("username") or "Не назначен"
            if isinstance(assigned, dict)
            else str(assigned or "Не назначен")
        )
        rows.append(
            InventoryRow(
                computer=str(asset.get("name") or "Без имени"),
                serial=str(asset.get("serial") or "—"),
                owner=str(owner),
                agent=str(_custom(asset, handles.get("agent_version")) or "—"),
                last_inventory=(
                    last_success.astimezone(_timezone(config.notifications.timezone)).strftime(
                        "%d.%m.%Y %H:%M"
                    )
                    if last_success
                    else "Данные не поступали"
                ),
                age_days=age,
                status=status,
                last_error=str(_custom(asset, handles.get("last_error")) or "—"),
            )
        )
    priority = {"never": 0, "critical": 1, "overdue": 2, "current": 3}
    return sorted(rows, key=lambda row: (priority[row.status], -row.age_days, row.computer))


def _shell(title: str, preheader: str, content: str, logo: bool) -> str:
    logo_html = (
        '<img src="cid:inventory-logo" width="126" alt="EXAMPLE INVENTORY" '
        'style="display:block;border:0;width:126px;height:auto">'
        if logo
        else '<div style="font-size:24px;font-weight:900;color:#FFFFFF">EXAMPLE INVENTORY</div>'
    )
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{_escape(title)}</title></head>
<body style="margin:0;padding:0;background:#F3F4F6;font-family:Arial,Helvetica,sans-serif;color:{BRAND_DARK}">
<div style="display:none;max-height:0;overflow:hidden;opacity:0">{_escape(preheader)}</div>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#F3F4F6"><tr><td align="center" style="padding:24px 10px">
<table role="presentation" width="960" cellspacing="0" cellpadding="0" style="width:100%;max-width:960px;background:#FFFFFF;border-collapse:separate;border-spacing:0;border-radius:14px;overflow:hidden">
<tr><td style="background:{BRAND_DARK};padding:22px 28px;border-bottom:6px solid {BRAND_RED}"><table role="presentation" width="100%"><tr><td>{logo_html}</td><td align="right" style="color:#FFFFFF;font-size:12px;line-height:18px">{PRODUCT}<br><span style="color:#B8BDC5">Автоматическая инвентаризация</span></td></tr></table></td></tr>
<tr><td style="padding:28px">{content}</td></tr>
<tr><td style="padding:18px 28px;background:#F7F7F8;border-top:1px solid {LINE};font-size:11px;line-height:17px;color:#6B7280">Письмо сформировано автоматически системой {PRODUCT}. Ответ на письмо не требуется.</td></tr>
</table></td></tr></table></body></html>"""


def weekly_report(
    rows: list[InventoryRow], config: GatewayConfig, generated_at: dt.datetime, logo: bool
) -> tuple[str, str, str]:
    total = len(rows)
    current_count = sum(row.status == "current" for row in rows)
    overdue = sum(row.status == "overdue" for row in rows)
    critical = sum(row.status in {"critical", "never"} for row in rows)
    never = sum(row.status == "never" for row in rows)
    iso = generated_at.isocalendar()
    subject = mail_subject(
        "weekly-report", f"{iso.year}-W{iso.week:02d} · критично {critical} из {total}"
    )
    labels = [
        ("Всего компьютеров", total, BRAND_DARK),
        ("Актуальные", current_count, "#15803D"),
        (f"Без данных {config.notifications.weekly_stale_days}–{config.notifications.weekly_critical_days - 1} дн.", overdue, "#B45309"),
        (f"Критические {config.notifications.weekly_critical_days}+ дн.", critical, BRAND_RED),
        ("Никогда не выходили", never, "#6B7280"),
    ]
    cards = "".join(
        f'<td width="20%" valign="top" style="padding:0 4px"><div style="border-top:4px solid {color};background:#F7F7F8;padding:14px 10px;min-height:72px"><div style="font-size:26px;font-weight:800;color:{color}">{value}</div><div style="font-size:11px;line-height:15px;color:#4B5563">{_escape(label)}</div></div></td>'
        for label, value, color in labels
    )
    styles = {
        "current": ("Актуально", "#DCFCE7", "#166534"),
        "overdue": ("Требует внимания", "#FEF3C7", "#92400E"),
        "critical": ("Критично", "#FFE4E6", "#BE123C"),
        "never": ("Нет данных", "#E5E7EB", "#374151"),
    }
    body_rows = []
    for row in rows[: config.notifications.weekly_max_rows]:
        label, background, color = styles[row.status]
        body_rows.append(
            "<tr>"
            f'<td style="padding:11px 8px;border-bottom:1px solid {LINE};font-weight:700">{_escape(row.computer)}</td>'
            f'<td style="padding:11px 8px;border-bottom:1px solid {LINE}">{_escape(row.serial)}</td>'
            f'<td style="padding:11px 8px;border-bottom:1px solid {LINE}">{_escape(row.owner)}</td>'
            f'<td style="padding:11px 8px;border-bottom:1px solid {LINE}">{_escape(row.agent)}</td>'
            f'<td style="padding:11px 8px;border-bottom:1px solid {LINE}">{_escape(row.last_inventory)}</td>'
            f'<td align="center" style="padding:11px 8px;border-bottom:1px solid {LINE}">{row.age_days}</td>'
            f'<td style="padding:11px 8px;border-bottom:1px solid {LINE}"><span style="display:inline-block;padding:5px 8px;background:{background};color:{color};font-size:11px;font-weight:700;border-radius:12px">{label}</span></td>'
            f'<td style="padding:11px 8px;border-bottom:1px solid {LINE};color:#6B7280">{_escape(row.last_error)}</td>'
            "</tr>"
        )
    local = generated_at.astimezone(_timezone(config.notifications.timezone))
    table = "".join(body_rows) or '<tr><td colspan="8" style="padding:30px;text-align:center;color:#6B7280">Компьютеры не найдены</td></tr>'
    content = f"""
<h1 style="margin:0 0 6px;font-size:27px;line-height:34px">Еженедельный отчёт по инвентаризации</h1>
<p style="margin:0 0 22px;color:#6B7280;font-size:13px">Сформирован {local.strftime('%d.%m.%Y в %H:%M')} · данные актуальны менее {config.notifications.weekly_stale_days} дней</p>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin-bottom:22px"><tr>{cards}</tr></table>
<div style="padding:13px 16px;margin-bottom:22px;background:{PALE_RED};border-left:5px solid {BRAND_RED};font-size:13px;line-height:20px"><strong>Что требует внимания:</strong> {critical} критичных компьютеров, из них {never} ещё ни разу не передавали данные. Проверьте установку агента, доступ к HTTPS Gateway и резервной почте.</div>
<div style="overflow-x:auto"><table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;font-size:12px;line-height:17px">
<thead><tr style="background:{BRAND_DARK};color:#FFFFFF"><th align="left" style="padding:11px 8px">Компьютер</th><th align="left" style="padding:11px 8px">Серийный номер</th><th align="left" style="padding:11px 8px">Пользователь</th><th align="left" style="padding:11px 8px">Агент</th><th align="left" style="padding:11px 8px">Последние данные</th><th style="padding:11px 8px">Дней</th><th align="left" style="padding:11px 8px">Статус</th><th align="left" style="padding:11px 8px">Последняя ошибка</th></tr></thead><tbody>{table}</tbody></table></div>
"""
    text = (
        f"Еженедельный отчёт по инвентаризации\n\nВсего: {total}\nАктуальные: {current_count}"
        f"\nТребуют внимания: {overdue}\nКритические: {critical}\nДанные не поступали: {never}"
    )
    return subject, text, _shell(subject, text, content, logo)


def computer_report(payload: dict, result: str, logo: bool) -> tuple[str, str, str, str]:
    computer = str(payload.get("computer_name") or "Без имени")
    subject = mail_subject("computer-report", f"{computer} · инвентаризация обновлена")
    inventory = payload.get("inventory") or {}
    custom = inventory.get("custom_fields") or {}
    owner = ""
    if ";owner_changed:" in result:
        owner = result.rsplit("->", 1)[-1]
        result_label = "Данные обновлены, смена пользователя подтверждена"
    elif ";assigned:" in result:
        owner = result.rsplit(":", 1)[-1]
        result_label = "Данные обновлены, пользователь подтверждён"
    elif "identity_unresolved" in result:
        result_label = "Данные обновлены, пользователь не найден в Snipe-IT"
    else:
        result_label = "Данные обновлены, текущий пользователь сохранён"
    fields = [
        ("Компьютер", computer),
        ("Серийный номер", payload.get("serial_number")),
        ("Версия агента", (payload.get("agent") or {}).get("version")),
        ("Производитель", custom.get("manufacturer")),
        ("Модель", custom.get("model")),
        ("Процессор", custom.get("cpu")),
        ("Операционная система", custom.get("os")),
        ("Версия ОС", custom.get("os_version")),
        ("Подтверждённый пользователь", owner or "Без изменений"),
        ("Результат синхронизации", result_label),
    ]
    rows = "".join(
        f'<tr><td width="38%" style="padding:10px 12px;border-bottom:1px solid {LINE};color:#6B7280">{_escape(label)}</td><td style="padding:10px 12px;border-bottom:1px solid {LINE};font-weight:600">{_escape(value)}</td></tr>'
        for label, value in fields
    )
    content = f"""<h1 style="margin:0 0 6px;font-size:27px">Отчёт по компьютеру</h1><p style="margin:0 0 22px;color:#6B7280">Инвентаризация принята Gateway и синхронизирована со Snipe-IT.</p><table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;border-top:4px solid {BRAND_RED};font-size:13px">{rows}</table>"""
    stable = {
        "computer": computer,
        "serial": payload.get("serial_number"),
        "inventory": inventory,
        "confirmed_owner": owner,
    }
    fingerprint = hashlib.sha256(
        json.dumps(stable, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    text = "Отчёт по компьютеру\n\n" + "\n".join(f"{label}: {value or '—'}" for label, value in fields)
    return subject, text, _shell(subject, text, content, logo), fingerprint


def owner_change_report(
    payload: dict, previous: str, current: str, confirmations: int, logo: bool
) -> tuple[str, str, str]:
    computer = str(payload.get("computer_name") or "Без имени")
    subject = mail_subject(
        "owner-change", f"{computer} · {previous or 'не назначен'} → {current}"
    )
    text = (
        f"Подтверждена смена пользователя\n\nКомпьютер: {computer}\n"
        f"Было: {previous or 'Не назначен'}\nСтало: {current}\nПодтверждений: {confirmations}"
    )
    content = f"""<h1 style="margin:0 0 6px;font-size:27px">Подтверждена смена пользователя</h1><p style="margin:0 0 22px;color:#6B7280">Gateway применил изменение только после проверки стабильности учётной записи.</p><table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;font-size:14px"><tr><td style="padding:13px;background:#F7F7F8">Компьютер</td><td style="padding:13px;font-weight:700">{_escape(computer)}</td></tr><tr><td style="padding:13px;background:#F7F7F8">Предыдущий пользователь</td><td style="padding:13px">{_escape(previous or 'Не назначен')}</td></tr><tr><td style="padding:13px;background:{PALE_RED}">Новый пользователь</td><td style="padding:13px;background:{PALE_RED};font-weight:800;color:{BRAND_RED}">{_escape(current)}</td></tr><tr><td style="padding:13px;background:#F7F7F8">Подтверждений</td><td style="padding:13px">{confirmations}</td></tr></table>"""
    return subject, text, _shell(subject, text, content, logo)
