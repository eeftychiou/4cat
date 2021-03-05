"""
Initialize Twitter API v2 data source
"""

# An init_datasource function is expected to be available to initialize this
# data source. A default function that does this is available from the
# backend helpers library.
from backend.lib.helpers import init_datasource

# Internal identifier for this data source
DATASOURCE = "twitterv2"
NAME = "Twitter APIv2 (Academic Track)"