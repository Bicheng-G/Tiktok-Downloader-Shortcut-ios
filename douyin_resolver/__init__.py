"""Douyin share link -> HTTP-first metadata -> downloadable MP4."""

from .core import ResolverError, download, resolve

__all__ = ["ResolverError", "resolve", "download"]
