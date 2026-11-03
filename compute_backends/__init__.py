"""Selectable compute locations for video and live dubbing."""

from .providers import PROVIDERS, provider_description, validate_provider

__all__ = ["PROVIDERS", "provider_description", "validate_provider"]
