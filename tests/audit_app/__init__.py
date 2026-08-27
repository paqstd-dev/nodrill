"""A small application with three entry points, one of which cannot see what it reads.

Written to be representative rather than flattering.  The broken entry point
fails the way a real one does, by reading a key a boundary above it never
opened, and the two working ones read the same key through different shapes
so a contract has something to say about each.
"""
