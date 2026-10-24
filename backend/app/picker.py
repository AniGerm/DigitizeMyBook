from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def pick_path(directories: bool = True) -> str | None:
    zenity = shutil.which("zenity")
    if zenity:
        cmd = [zenity, "--file-selection", "--title=Quelle wählen"]
        if directories:
            cmd.append("--directory")
        else:
            cmd.extend(["--multiple", "--separator=\n"])
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None
    kdialog = shutil.which("kdialog")
    if kdialog:
        flag = "--getexistingdirectory" if directories else "--getopenfilename"
        result = subprocess.run([kdialog, flag, str(Path.home())], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    if directories:
        chosen = filedialog.askdirectory()
    else:
        chosen = filedialog.askopenfilenames(
            filetypes=[
                ("Bücher", "*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.webp"),
                ("Alle Dateien", "*.*"),
            ]
        )
        chosen = "\n".join(chosen) if chosen else ""
    root.destroy()
    return chosen or None
