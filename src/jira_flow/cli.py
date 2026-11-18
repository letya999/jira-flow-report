import argparse
import functools
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

from .config import load_config, parse_overrides
from .report import run_report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Jira flow reports from a team YAML or explicit parameters")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "build", "config"):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path, help="Team YAML; optional when --set supplies all required settings")
        command.add_argument("--set", action="append", default=[], metavar="PATH=VALUE", help="Override any setting; lists/objects use JSON")
        if name == "run":
            command.add_argument("--env-file", type=Path, default=Path(".env"))
        if name == "build":
            command.add_argument("--input", type=Path, required=True, help="Exported issues.json; no Jira connection")
    serve = commands.add_parser("serve")
    serve.add_argument("--directory", type=Path, required=True)
    serve.add_argument("--port", type=int, default=0, help="0 selects an available loopback port")
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            directory = args.directory.resolve(strict=True)
            if not directory.is_dir() or not (directory/"report.html").is_file():
                raise ValueError("Directory must contain report.html")
            handler = functools.partial(SimpleHTTPRequestHandler, directory=str(directory))
            with ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
                print(f"http://127.0.0.1:{server.server_port}/report.html", flush=True)
                server.serve_forever()
        else:
            overrides = parse_overrides(args.set)
            if args.command == "config":
                print(yaml.safe_dump(load_config(args.config, overrides), allow_unicode=True, sort_keys=False))
            else:
                directory = run_report(args.config, overrides, getattr(args, "input", None), getattr(args, "env_file", ".env"))
                metrics = json.loads((directory/"metrics.json").read_text(encoding="utf-8"))
                print(json.dumps({"output": str(directory), "issues": metrics["cohort_size"],
                                  "delivered": metrics["throughput"]["unique"], "lead_n": metrics["lead"]["stats"]["n"],
                                  "estimated_n": metrics["accuracy"]["n"]}, ensure_ascii=False), flush=True)
    except (ValueError, RuntimeError, OSError, yaml.YAMLError) as exc:
        parser.exit(2, "Error: " + str(exc) + "\n")
    except KeyboardInterrupt:
        return 130
    return 0
