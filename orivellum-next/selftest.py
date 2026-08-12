#!/usr/bin/env python3
"""Run every invariant test. Exit 0 means the milestone can be marked done."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from tests.test_all import main
raise SystemExit(main())
