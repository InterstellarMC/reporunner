"""Dockerfile generation from command ledger and detected dependencies."""

from __future__ import annotations

from typing import List, Optional
from backend.sandbox.api.schemas import CommandClass, CommandRequest


def generate_dockerfile(
    base_image: str = "python:3.11-slim",
    command_ledger: Optional[List[CommandRequest]] = None,
    run_cmd: str = "python3 app.py",
    port: Optional[int] = None,
) -> str:
    """Generate a reproducible Dockerfile based on actual successful install steps."""
    lines = [
        f"FROM {base_image}",
        "WORKDIR /workspace",
        "",
        "# Non-root runner user",
        "RUN groupadd -g 10001 runner && useradd -u 10001 -g 10001 -d /home/runner -m runner",
        "",
        "# Copy project source files",
        "COPY . /workspace",
        "RUN chown -R 10001:10001 /workspace /home/runner",
        "",
    ]

    # Collect install/build commands from ledger
    install_commands: List[str] = []
    if command_ledger:
        for req in command_ledger:
            if req.command_class in {CommandClass.INSTALL, CommandClass.BUILD, CommandClass.FETCH}:
                # Strip sudo if present
                clean_cmd = req.command.replace("sudo ", "").strip()
                if clean_cmd and clean_cmd not in install_commands:
                    install_commands.append(clean_cmd)

    if install_commands:
        lines.append("# Setup and dependencies installed during setup")
        for cmd in install_commands:
            lines.append(f"RUN {cmd}")
        lines.append("")
    else:
        # Default dependency install if files present
        lines.append("# Install dependencies if manifests exist")
        lines.append("RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi")
        lines.append("RUN if [ -f package.json ]; then npm install; fi")
        lines.append("")

    if port:
        lines.append(f"EXPOSE {port}")
        lines.append("")

    lines.append("USER runner")
    parts = run_cmd.split()
    cmd_formatted = ", ".join(f'"{p}"' for p in parts)
    lines.append(f"CMD [{cmd_formatted}]")
    lines.append("")

    return "\n".join(lines)
