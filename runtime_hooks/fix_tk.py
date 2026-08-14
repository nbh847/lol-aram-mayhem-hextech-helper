"""PyInstaller runtime hook: 将 Tk 指向随应用分发的 Tcl/Tk 文件。"""
import os
import sys


if getattr(sys, "frozen", False):
    meipass = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    if meipass not in sys.path:
        sys.path.insert(0, meipass)

    tcl_dir = os.path.join(meipass, "tcl", "tcl8.6")
    tk_dir = os.path.join(meipass, "tcl", "tk8.6")

    if os.path.isdir(tcl_dir):
        os.environ["TCL_LIBRARY"] = tcl_dir
    if os.path.isdir(tk_dir):
        os.environ["TK_LIBRARY"] = tk_dir

    if os.path.isdir(meipass):
        os.environ["PATH"] = meipass + os.pathsep + os.environ.get("PATH", "")
        if hasattr(os, "add_dll_directory"):
            _dll_directory = os.add_dll_directory(meipass)
