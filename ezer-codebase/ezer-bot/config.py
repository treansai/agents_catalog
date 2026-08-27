"""Backward-compatible import for the packaged settings."""

from ezer.config import Settings

Config = Settings

__all__ = ["Config", "Settings"]
