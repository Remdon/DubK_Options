"""
Connectors Module - External Service Connections

This module handles connections to:
- OpenBB API server
- Other external data sources
"""

from .openbb_server import OpenBBAPIServer

__all__ = [
    'OpenBBAPIServer',
]
