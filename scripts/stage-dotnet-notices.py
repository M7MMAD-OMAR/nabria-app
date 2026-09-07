"""Ship notices from the exact runtime packs selected by dotnet restore."""

import json
import shutil
from pathlib import Path

root = Path(__file__).resolve().parent.parent
assets = json.loads((root / "windows/Nabria.Desktop/obj/project.assets.json").read_text())
destination = root / "dist/Nabria/licenses/dotnet"
for framework in assets["project"]["frameworks"].values():
    for dependency in framework.get("downloadDependencies", []):
        name = dependency["name"].lower()
        if name not in {"microsoft.netcore.app.runtime.win-x64",
                        "microsoft.windowsdesktop.app.runtime.win-x64"}:
            continue
        version = dependency["version"].strip("[]").split(",")[0].strip()
        source = next(Path(folder) / name / version for folder in assets["packageFolders"]
                      if (Path(folder) / name / version).is_dir())
        target = destination / f"{name}-{version}"
        target.mkdir(parents=True, exist_ok=True)
        notices = [path for path in source.iterdir()
                   if path.is_file() and ("license" in path.name.lower() or "notice" in path.name.lower())]
        if not notices:
            raise RuntimeError(f"No license found in {source}")
        for path in notices:
            shutil.copy2(path, target / path.name)
