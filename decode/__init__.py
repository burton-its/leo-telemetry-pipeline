"""Decode stage: turn RawFrames from Ingestion into DecodedFrames.

Each satellite is described by a SatelliteSpec in decode/registry.py.
To add a satellite, write a function bytes -> dict and register it there.
"""