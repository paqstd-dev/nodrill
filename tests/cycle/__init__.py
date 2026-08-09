"""Two modules that import each other, which is the cycle ref() exists for.

`context` owns the key and imports the module that reads it, so importing
`context` runs `models` first.  `models` names the key with `ref()` and imports
nothing back, so the cycle never forms.  The `direct_` pair is the same shape
written with a plain import and cannot be imported at all, which is what makes
this a real cycle rather than a description of one.
"""
