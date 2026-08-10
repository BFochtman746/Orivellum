"""Runnable entry point: python -m orivellum.api.main"""
from __future__ import annotations

import os

import uvicorn

from orivellum.api.app import app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
