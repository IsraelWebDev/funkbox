#!/usr/bin/env python3
"""Launcher: ``python funkbox.py ...`` is ``python -m funkbox ...`` from a checkout."""
import sys

from funkbox.cli import main

sys.exit(main())
