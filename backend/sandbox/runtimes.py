"""Runtime choices owned by the application, never supplied by repository text."""
RUNTIMES = {
    "python": ("python:3.11-slim", ["python", "-c", "import types,json,ssl,unittest; assert hasattr(types,'ModuleType')"]),
    "node": ("node:20-slim", ["node", "--version"]),
    "go": ("golang:1-bookworm", ["go", "version"]),
    "rust": ("rust:1-slim", ["rustc", "--version"]),
    "java": ("maven:3.9-eclipse-temurin-21", ["mvn", "--version"]),
    "dotnet": ("mcr.microsoft.com/dotnet/sdk:8.0", ["dotnet", "--version"]),
}


def detect_stack(root):
    # Runtime manifests take precedence over incidental helper scripts.
    for stack, names in (("node", ["package.json"]), ("python", ["pyproject.toml", "requirements.txt", "setup.py"]),
                         ("go", ["go.mod"]), ("rust", ["Cargo.toml"]), ("java", ["pom.xml"])):
        if any((root / name).is_file() for name in names):
            return stack
    if any(root.glob("*.sln")) or any(root.glob("*.csproj")):
        return "dotnet"
    if (root / "index.html").is_file():
        return "node"
    if any(root.glob("*.py")):
        return "python"
    return "unknown"
