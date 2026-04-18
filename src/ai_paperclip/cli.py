"""AI Paperclip CLI — thin client.

All command logic lives server-side at /api/cli/execute.
The CLI handles config, serve management, and ingest locally;
everything else is sent to the server.
"""

import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

import click
import requests

from . import DATA_DIR, PAPERS_DIR, __version__

DIM = "\033[2m"
BOLD = "\033[1m"
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"

DEFAULT_SERVER = "http://localhost:7719"
PID_FILE = DATA_DIR / "server.pid"
CONFIG_FILE = DATA_DIR / "config.json"


def _get_server_url() -> str:
    if os.environ.get("AIPAPER_SERVER"):
        return os.environ["AIPAPER_SERVER"]
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text())
            if cfg.get("server_url"):
                return cfg["server_url"]
        except Exception:
            pass
    return DEFAULT_SERVER


def _server_running() -> bool:
    try:
        resp = requests.get(f"{_get_server_url()}/health", timeout=2)
        return resp.status_code == 200
    except Exception:
        return False


def _execute(command: str, raw: str = ""):
    """Send a command to the server and print the output."""
    if not _server_running():
        click.echo(f"{RED}[error] Server is not running.{RESET}", err=True)
        click.echo(f"{DIM}Start with: aipaper serve start{RESET}", err=True)
        sys.exit(1)

    url = f"{_get_server_url()}/api/cli/execute"
    try:
        resp = requests.post(
            url,
            json={"command": command, "raw": raw},
            timeout=300,
        )
    except requests.ConnectionError:
        click.echo(f"{RED}[error] Could not reach the server.{RESET}", err=True)
        sys.exit(1)
    except requests.Timeout:
        click.echo(f"{RED}[error] Request timed out.{RESET}", err=True)
        sys.exit(1)

    if resp.status_code != 200:
        click.echo(f"{RED}[error] Server returned {resp.status_code}{RESET}", err=True)
        sys.exit(1)

    data = resp.json()
    output = data.get("output", "")
    if output:
        click.echo(output)
    elapsed = data.get("elapsed_ms")
    result_id = data.get("result_id")
    if elapsed is not None:
        label = f"{elapsed}ms" if elapsed < 1000 else f"{elapsed / 1000:.1f}s"
        if result_id:
            click.echo(f"{DIM}[{label}, saved to {result_id}]{RESET}")
        else:
            click.echo(f"{DIM}[{label}]{RESET}")


class _AIPaperGroup(click.Group):
    """Click group that forwards unrecognized subcommands to the server."""

    def get_command(self, ctx, cmd_name):
        rv = super().get_command(ctx, cmd_name)
        if rv is not None:
            return rv
        return self._make_proxy(cmd_name)

    @staticmethod
    def _make_proxy(cmd_name):
        @click.command(cmd_name, context_settings={
            "ignore_unknown_options": True, "allow_extra_args": True
        })
        @click.argument("args", nargs=-1, type=click.UNPROCESSED)
        def proxy(args):
            _execute(cmd_name, shlex.join(args))
        return proxy

    def resolve_command(self, ctx, args):
        cmd_name = args[0] if args else None
        if cmd_name and not cmd_name.startswith("-"):
            cmd = self.get_command(ctx, cmd_name)
            if cmd is not None:
                return cmd_name, cmd, args[1:]
        return super().resolve_command(ctx, args)


@click.group(cls=_AIPaperGroup, invoke_without_command=True)
@click.version_option(version=__version__)
@click.pass_context
def cli(ctx):
    """AI Paperclip — search 800K+ AI/ML papers from the command line.

    \b
    Quick start:
        aipaper serve start
        aipaper search "transformer architecture"
        aipaper cat 2401.12345/meta.json
        aipaper grep "attention" --field content
    """
    if ctx.invoked_subcommand is None:
        _show_dashboard()


def _show_dashboard():
    click.echo(f"\n  {BOLD}AI Paperclip{RESET} v{__version__}")
    click.echo(f"  {'─' * 40}")

    if _server_running():
        try:
            resp = requests.get(f"{_get_server_url()}/health", timeout=2)
            data = resp.json()
            click.echo(f"  {GREEN}●{RESET} Server running at {_get_server_url()}")
            click.echo(f"  {DIM}Papers: {data.get('indexed_papers', '?'):,}  "
                       f"Pulled: {data.get('pulled_papers', '?'):,}  "
                       f"Trigram: {data.get('trigram_indexed', '?'):,}{RESET}")
        except Exception:
            click.echo(f"  {GREEN}●{RESET} Server running")
    else:
        click.echo(f"  {RED}●{RESET} Server not running")
        click.echo(f"  {DIM}Start with: aipaper serve start{RESET}")

    click.echo(f"\n  {BOLD}Commands{RESET}")
    click.echo(f'    {CYAN}search{RESET}    Hybrid search          aipaper search "chain of thought"')
    click.echo(f'    {CYAN}grep{RESET}      Regex (trigram-fast)   aipaper grep "attention" --field content')
    click.echo(f'    {CYAN}read{RESET}      Paper details          aipaper read 2401.12345')
    click.echo(f'    {CYAN}cat{RESET}       Read file              aipaper cat 2401.12345/meta.json')
    click.echo(f'    {CYAN}head{RESET}      Preview file           aipaper head -40 2401.12345/content.lines')
    click.echo(f'    {CYAN}ls{RESET}        List files             aipaper ls 2401.12345/sections/')
    click.echo(f'    {CYAN}scan{RESET}      Multi-keyword find     aipaper scan 2401.12345/content.lines "RLHF" "PPO"')
    click.echo(f'    {CYAN}map{RESET}       LLM analysis           aipaper map "What method was used?"')
    click.echo(f'    {CYAN}bash{RESET}      Shell on papers        aipaper bash \'cat 2401.12345/content.lines | grep -i attention\'')
    click.echo(f'    {CYAN}ask-image{RESET} VLM figure analysis    aipaper ask-image 2401.12345 "What does this show?"')
    click.echo(f'    {CYAN}sql{RESET}       Query database         aipaper sql "SELECT title FROM papers ..."')
    click.echo(f'    {CYAN}related{RESET}   Citation graph          aipaper related 2401.12345')
    click.echo(f'    {CYAN}trending{RESET}  Recent hot papers      aipaper trending --days 7')
    click.echo(f'    {CYAN}from{RESET}      Chain on results       aipaper from <rid> grep "term"')
    click.echo(f'    {CYAN}results{RESET}   Saved result sets      aipaper results --list')
    click.echo()


# ── Serve management (local) ─────────────────────────────────────

@cli.group()
def serve():
    """Manage the AI Paperclip server."""
    pass


@serve.command(name="start")
@click.option("--port", "-p", default=7719)
@click.option("--foreground", "-f", is_flag=True)
def serve_start(port, foreground):
    """Start the server (all commands route through it)."""
    if _server_running():
        click.echo(f"{YELLOW}Server already running.{RESET}")
        return

    if foreground:
        click.echo(f"{GREEN}Starting server on port {port}...{RESET}")
        import uvicorn
        uvicorn.run("ai_paperclip.server:app", host="127.0.0.1", port=port, log_level="info")
    else:
        log_file = DATA_DIR / "server.log"
        cmd = [
            sys.executable, "-m", "uvicorn",
            "ai_paperclip.server:app",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--log-level", "info",
        ]
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
        with open(log_file, "w") as lf:
            proc = subprocess.Popen(cmd, stdout=lf, stderr=lf, env=env, start_new_session=True)
        PID_FILE.write_text(str(proc.pid))
        click.echo(f"{GREEN}Server started{RESET} (pid {proc.pid}, port {port})")

        time.sleep(1.5)
        if _server_running():
            click.echo(f"{GREEN}●{RESET} Healthy")
        else:
            click.echo(f"{YELLOW}Starting up... (check {log_file}){RESET}")


@serve.command(name="stop")
def serve_stop():
    """Stop the server."""
    if PID_FILE.exists():
        pid = int(PID_FILE.read_text().strip())
        try:
            os.kill(pid, signal.SIGTERM)
            click.echo(f"{GREEN}Server stopped{RESET} (pid {pid})")
        except ProcessLookupError:
            click.echo(f"{DIM}Server was not running.{RESET}")
        PID_FILE.unlink(missing_ok=True)
    else:
        click.echo(f"{YELLOW}No PID file found.{RESET}")


@serve.command(name="status")
def serve_status():
    """Check server health."""
    try:
        resp = requests.get(f"{_get_server_url()}/health", timeout=2)
        if resp.status_code == 200:
            data = resp.json()
            click.echo(f"{GREEN}●{RESET} Server running at {_get_server_url()}")
            click.echo(f"  Papers: {data.get('indexed_papers', '?'):,}")
            click.echo(f"  Pulled: {data.get('pulled_papers', '?'):,}")
            click.echo(f"  Trigram: {data.get('trigram_indexed', '?'):,}")
            if PID_FILE.exists():
                click.echo(f"  PID: {PID_FILE.read_text().strip()}")
            return
    except Exception:
        pass
    click.echo(f"{RED}●{RESET} Server not running")
    click.echo(f"{DIM}Start with: aipaper serve start{RESET}")


@serve.command(name="restart")
@click.option("--port", "-p", default=7719)
def serve_restart(port):
    """Restart the server."""
    if PID_FILE.exists():
        pid = int(PID_FILE.read_text().strip())
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(1)
        except ProcessLookupError:
            pass
        PID_FILE.unlink(missing_ok=True)

    log_file = DATA_DIR / "server.log"
    cmd = [
        sys.executable, "-m", "uvicorn",
        "ai_paperclip.server:app",
        "--host", "127.0.0.1",
        "--port", str(port),
        "--log-level", "info",
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
    with open(log_file, "w") as lf:
        proc = subprocess.Popen(cmd, stdout=lf, stderr=lf, env=env, start_new_session=True)
    PID_FILE.write_text(str(proc.pid))
    click.echo(f"{GREEN}Server restarted{RESET} (pid {proc.pid}, port {port})")
    time.sleep(1.5)
    if _server_running():
        click.echo(f"{GREEN}●{RESET} Healthy")


# ── Ingest commands (local, long-running) ─────────────────────────

@cli.group()
def ingest():
    """Bulk data ingestion pipeline."""
    pass


@ingest.command(name="arxiv")
@click.option("--resume/--no-resume", default=True)
def ingest_arxiv(resume):
    """Harvest all AI/ML paper metadata from arXiv OAI-PMH."""
    from . import bulk

    click.echo(f"{BOLD}arXiv OAI-PMH Harvester{RESET}")
    click.echo(f"{DIM}Safe to Ctrl+C and resume later.{RESET}\n")

    count = [0]
    def cb(event, data):
        if event == "batch":
            count[0] += 1
            cs = data.get("complete_size", "?")
            cursor = data.get("cursor", "?")
            click.echo(f"  Batch {count[0]}: {data['total_ai']:,} AI / {data['total_harvested']:,} total — {cursor}/{cs}")
        elif event == "rate_limited":
            click.echo(f"  {YELLOW}Rate limited, waiting {data['wait']}s...{RESET}")
        elif event == "complete":
            click.echo(f"\n  {GREEN}Complete!{RESET} {data['total_ai']:,} AI papers from {data['total_harvested']:,} total")

    try:
        bulk.harvest_arxiv(callback=cb, resume=resume)
    except KeyboardInterrupt:
        click.echo(f"\n{YELLOW}Interrupted. Progress saved — resume with: aipaper ingest arxiv{RESET}")


@ingest.command(name="s2")
@click.option("--batch-size", "-b", default=500)
def ingest_s2(batch_size):
    """Enrich papers with Semantic Scholar citation data."""
    from . import bulk

    click.echo(f"{BOLD}Semantic Scholar Enrichment{RESET}\n")
    def cb(event, data):
        if event == "start":
            click.echo(f"  Papers to enrich: {data['total_unenriched']:,}")
        elif event == "batch":
            click.echo(f"  {data['progress']}% — enriched: {data['enriched']:,}")
        elif event == "complete":
            click.echo(f"\n  {GREEN}Done!{RESET} Enriched: {data['enriched']:,}")

    bulk.enrich_s2_batch(batch_size=batch_size, callback=cb)


@ingest.command(name="embed")
@click.option("--model", "-m", default="all-MiniLM-L6-v2")
@click.option("--batch-size", "-b", default=256)
def ingest_embed(model, batch_size):
    """Build vector embeddings for semantic search."""
    from . import bulk

    click.echo(f"{BOLD}Building Embeddings{RESET}\n")
    def cb(event, data):
        if event == "loading_model":
            click.echo(f"  Model: {data['model']} ({data['total_papers']:,} papers)")
        elif event == "encoding":
            click.echo(f"  Encoding {data['total']:,} papers...")
        elif event == "progress":
            click.echo(f"  {data['progress']}% ({data['encoded']:,}/{data['total']:,})")
        elif event == "complete":
            click.echo(f"\n  {GREEN}Done!{RESET} {data['total_embedded']:,} papers, {data['file_size_mb']} MB")

    bulk.build_embeddings(batch_size=batch_size, model_name=model, callback=cb)


@ingest.command(name="trigrams")
def ingest_trigrams():
    """Build trigram index from all pulled papers for fast grep."""
    from . import trigram as tri, filesystem

    papers_dir = PAPERS_DIR
    if not papers_dir.exists():
        click.echo(f"{YELLOW}No pulled papers. Pull some first.{RESET}")
        return

    click.echo(f"{BOLD}Building Trigram Index{RESET}\n")
    conn = tri.get_conn()
    count = 0
    for paper_d in sorted(papers_dir.iterdir()):
        content_path = paper_d / "content.lines"
        if content_path.exists():
            tri.index_paper(conn, paper_d.name, content_path.read_text())
            count += 1
            if count % 100 == 0:
                conn.commit()
                click.echo(f"  {count} papers indexed...")

    conn.commit()
    s = tri.stats(conn)
    click.echo(f"\n  {GREEN}Done!{RESET} {s['indexed_papers']:,} papers, {s['unique_trigrams']:,} trigrams")


@ingest.command(name="abstracts")
def ingest_abstracts():
    """Index all paper abstracts into trigram DB for corpus-wide grep."""
    from . import trigram as tri, db as database

    click.echo(f"{BOLD}Indexing Abstracts into Trigram DB{RESET}\n")
    papers_conn = database.get_conn()

    def cb(event, data):
        if event == "start":
            click.echo(f"  Papers to index: {data['total']:,} (already indexed: {data['already_indexed']:,})")
        elif event == "progress":
            click.echo(f"  {data['indexed']:,}/{data['total']:,} indexed...")
        elif event == "complete":
            click.echo(f"\n  {GREEN}Done!{RESET} Indexed {data['indexed']:,} abstracts")

    indexed = tri.index_all_abstracts(papers_conn, callback=cb)
    s = tri.stats(tri.get_conn())
    click.echo(f"  Trigram DB: {s['indexed_papers']:,} papers, {s['unique_trigrams']:,} trigrams")


@ingest.command(name="status")
def ingest_status():
    """Show ingestion pipeline status."""
    _execute("stats")


# ── Config (local) ───────────────────────────────────────────────

@cli.command(name="config")
@click.option("--url", help="Set server URL")
@click.option("--api-key", help="Set Anthropic API key (for map/ask-image LLM features)")
def config_cmd(url, api_key):
    """Configure settings."""
    cfg = {}
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
    changed = False
    if url:
        cfg["server_url"] = url.rstrip("/")
        changed = True
        click.echo(f"{GREEN}Server URL set: {url}{RESET}")
    if api_key:
        cfg["anthropic_api_key"] = api_key
        changed = True
        click.echo(f"{GREEN}Anthropic API key set{RESET}")
    if changed:
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    else:
        click.echo(f"  Server:  {_get_server_url()}")
        has_key = "set" if cfg.get("anthropic_api_key") else "not set"
        click.echo(f"  API key: {has_key} (for map/ask-image)")
        click.echo(f"  Config:  {CONFIG_FILE}")
        click.echo(f"  PID:     {PID_FILE}")


def main():
    cli()


if __name__ == "__main__":
    main()
