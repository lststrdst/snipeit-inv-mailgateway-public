from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import uvicorn

from . import __version__
from .config import load_config
from .mail import collect
from .notifications import Notifier
from .queue import EventQueue
from .worker import run_forever, run_once


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="snipeit-inventory-gateway")
    root.add_argument("--config")
    root.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("check-config")
    commands.add_parser("serve")
    worker = commands.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    mail = commands.add_parser("collect-mail")
    mail.add_argument("--dry-run", action="store_true")
    commands.add_parser("health")
    commands.add_parser("weekly-report")
    return root


def main() -> int:
    args = parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    config = load_config(args.config)
    if args.command == "check-config":
        print(f"configuration valid for {config.environment}; gateway {__version__}")
    elif args.command == "serve":
        if args.config:
            import os

            os.environ["SNIPEIT_GATEWAY_CONFIG"] = str(Path(args.config).resolve())
        uvicorn.run(
            "snipeit_inventory_gateway.api:app",
            host=config.api.bind_host,
            port=config.api.bind_port,
            server_header=False,
            date_header=False,
            access_log=False,
            proxy_headers=True,
            forwarded_allow_ips="127.0.0.1",
        )
    elif args.command == "worker":
        run_once(config) if args.once else run_forever(config)
    elif args.command == "collect-mail":
        print(json.dumps(collect(config, args.dry_run), sort_keys=True))
    elif args.command == "health":
        queue = EventQueue(config.queue.path)
        try:
            print(
                json.dumps(
                    {
                        "version": __version__,
                        "environment": config.environment,
                        "queue": queue.counts(),
                    },
                    sort_keys=True,
                )
            )
        finally:
            queue.close()
    elif args.command == "weekly-report":
        queue = EventQueue(config.queue.path)
        try:
            Notifier(config, queue).weekly_health()
        finally:
            queue.close()
    return 0
