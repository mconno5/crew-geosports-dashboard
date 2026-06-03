#!/usr/bin/env python3
"""Compatibility wrapper for the GeoSports report pipeline.

The full end-to-end command is now:
    python3 -m geosports build
"""

from geosports.cli import main


if __name__ == "__main__":
    main()
