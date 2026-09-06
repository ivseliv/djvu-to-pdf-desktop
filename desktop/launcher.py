"""PyInstaller entry point for the desktop application."""

import multiprocessing


if __name__ == "__main__":
    # Required before importing the app: dpsprep starts worker processes and
    # PyInstaller must divert its special child-process command line early.
    multiprocessing.freeze_support()

    from dpsprep_desktop.app import main

    main()
