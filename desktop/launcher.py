"""PyInstaller entry point for the desktop application."""

import multiprocessing
import sys


if __name__ == '__main__':
    # Required before importing the app: dpsprep starts worker processes and
    # PyInstaller must divert its special child-process command line early.
    multiprocessing.freeze_support()

    if sys.argv[1:2] == ['--run-dpsprep']:
        from dpsprep.cli import dpsprep

        del sys.argv[1]
        dpsprep()
    else:
        from dpsprep_desktop.app import main

        main()
