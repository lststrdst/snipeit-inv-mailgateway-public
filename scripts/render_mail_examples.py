"""Я формирую локальные примеры писем без сети и закрытой конфигурации."""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path


def preview(subject: str, body: str) -> str:
    header = f"""
<section style="max-width:960px;margin:0 auto;padding:25px 28px 20px;box-sizing:border-box">
  <div style="font:12px Arial,sans-serif;color:#6B7280;margin-bottom:12px">ПРЕДПРОСМОТР · ТЕСТОВЫЕ ДАННЫЕ · Reports</div>
  <h1 style="font:600 19px/28px Arial,sans-serif;margin:0 0 15px;color:#15171A">{html.escape(subject)}</h1>
  <div style="font:13px/23px Arial,sans-serif;color:#4B5563">
    <strong>От:</strong> SnipeIT Inventory Gateway &lt;notification@example.com&gt;<br>
    <strong>Кому:</strong> IT &lt;it@example.com&gt;
  </div>
</section>
"""
    return re.sub(r"(<body[^>]*>)", lambda match: match.group(1) + header, body, count=1)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=root / "docs" / "examples")
    output = parser.parse_args().output
    # Я беру шаблон именно этого checkout, в том числе для публичной редакции.
    sys.path.insert(0, str(root / "src"))
    from snipeit_inventory_gateway.reports import computer_report, owner_change_report

    payload = {
        "computer_name": "LAPTOP-107",
        "serial_number": "DEMO-SERIAL-0107",
        "agent": {"version": "1.0.0"},
        "inventory": {
            "custom_fields": {
                "manufacturer": "Example Devices",
                "model": "Demo Notebook 14",
                "cpu": "Demo CPU, 8 cores",
                "os": "Windows 11 Pro",
                "os_version": "Demo build",
            },
        },
    }
    owner = owner_change_report(payload, "user.old", "user.new", 3, logo=False)
    report = computer_report(payload, "updated;assigned:user.new", logo=False)
    output.mkdir(parents=True, exist_ok=True)
    for slug, rendered in (("owner-change", owner), ("computer-report", report)):
        subject, _, body = rendered[:3]
        (output / f"{slug}.html").write_text(preview(subject, body), encoding="utf-8")
    print("Rendered 2 local mail examples; no messages sent.")


if __name__ == "__main__":
    main()
