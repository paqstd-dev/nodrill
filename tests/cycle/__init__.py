"""The modules the ref() tests import, starting with the cycle ref() exists for.

`context` owns the key and imports the module that reads it, so importing
`context` runs `models` first.  `models` names the key with `ref()` and imports
nothing back, so the cycle never forms.  The `direct_` pair is the same shape
written with a plain import and cannot be imported at all, so the cycle here is
a real one rather than a described one.

`at_import`, `alias` and `reloadable` carry a case each, described where they
stand.
"""
