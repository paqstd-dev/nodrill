.. _howto-carry-an-object-across-a-boundary:

Carry an object across a boundary
=================================

:func:`~nodrill.export` refuses anything JSON has nowhere to put, so a namespace holding a dataclass does not travel as it stands.
:func:`~nodrill.set_codec` registers the pair that turns such a value into something portable and back again.

The codec runs once per namespace, ``dump`` on the way out and ``load`` on the way in, and whatever ``dump`` returns is checked the way any exported value is.
That is the whole contract, and it is what keeps the envelope JSON however clever the codec is inside.

A tagged dataclass codec
------------------------

.. code-block:: python
   :caption: codec.py

   from dataclasses import asdict, dataclass
   from typing import Any

   from nodrill import adopt, export, provider, set_codec, use


   @dataclass
   class Actor:
       id: int
       name: str


   TYPES: dict[str, type] = {Actor.__name__: Actor}


   def dump(values: dict[str, Any]) -> dict[str, Any]:
       """Render a known class as a tagged dict, and leave everything else alone."""
       return {name: _tagged(value) for name, value in values.items()}


   def load(values: dict[str, Any]) -> dict[str, Any]:
       """Rebuild what dump tagged, and leave everything else alone."""
       return {name: _rebuilt(value) for name, value in values.items()}


   def _tagged(value: Any) -> Any:
       name = type(value).__name__
       return {"__type__": name, **asdict(value)} if name in TYPES else value


   def _rebuilt(value: Any) -> Any:
       if isinstance(value, dict) and "__type__" in value:
           fields = {name: item for name, item in value.items() if name != "__type__"}
           return TYPES[value["__type__"]](**fields)
       return value


   def main() -> None:
       set_codec(dump=dump, load=load)          # once, at startup

       with provider("trace", request_id="req-42", actor=Actor(id=7, name="acme")):
           payload = export("trace")
       print(payload["ctx"]["trace"])

       with adopt(payload):
           print(use("trace").actor, use("trace").request_id)


   if __name__ == "__main__":
       main()

Output::

   {'request_id': 'req-42', 'actor': {'__type__': 'Actor', 'id': 7, 'name': 'acme'}}
   Actor(id=7, name='acme') req-42

The tag is a key inside your own namespace, so pick a name your data does not use.
``__type__`` is the usual choice and this recipe would misread a genuine value of that name.

The table is what makes ``load`` safe.
It names the classes this service is willing to build, so a payload asking for something else raises a :exc:`KeyError` instead of constructing whatever it was told to.

With msgspec
------------

``msgspec`` turns structs and dataclasses into builtins for you, and turns them back with the schema checked.
That second half is the reason to reach for it here, since an adopted payload is input and a codec is the last place it passes before your code treats it as context.

This one needs ``pip install msgspec``, which nodrill itself does not.

.. code-block:: python
   :caption: typed.py

   from typing import Any

   import msgspec

   from nodrill import adopt, export, provider, set_codec, use


   class Actor(msgspec.Struct):
       id: int
       name: str


   class Region(msgspec.Struct):
       code: str
       zone: str


   TYPES: dict[str, type] = {"actor": Actor, "region": Region}


   def dump(values: dict[str, Any]) -> dict[str, Any]:
       """Turn every struct in the namespace into builtins, and leave the rest alone."""
       return msgspec.to_builtins(values)


   def load(values: dict[str, Any]) -> dict[str, Any]:
       """Rebuild each attribute the table names, validating it on the way."""
       return {
           name: msgspec.convert(value, TYPES[name]) if name in TYPES else value
           for name, value in values.items()
       }


   def main() -> None:
       set_codec(dump=dump, load=load)

       with provider(
           "trace",
           request_id="req-42",
           actor=Actor(id=7, name="acme"),
           region=Region(code="eu", zone="eu-west-1"),
       ):
           message = msgspec.json.encode(export("trace"))

       print(message.decode())

       with adopt(msgspec.json.decode(message)):
           print(use("trace").actor, use("trace").region.zone)

       forged = b'{"v":1,"ctx":{"trace":{"actor":{"id":"seven","name":"acme"}}}}'
       try:
           with adopt(msgspec.json.decode(forged)):
               pass
       except msgspec.ValidationError as invalid:
           print("refused:", invalid)


   if __name__ == "__main__":
       main()

Output::

   {"v":1,"ctx":{"trace":{"request_id":"req-42","actor":{"id":7,"name":"acme"},"region":{"code":"eu","zone":"eu-west-1"}}}}
   Actor(id=7, name='acme') eu-west-1
   refused: Expected `int`, got `str` - at `$.id`

The last line is the point.
A payload from a broker can say anything, and ``msgspec.convert`` refuses a wrong shape with the path to the field rather than building an ``Actor`` whose ``id`` is a string that fails four frames later.
The table here is keyed by attribute name, so a payload cannot choose a class either, where the tagged recipe lets it name any class that table knows.

``msgspec`` is doing two separate jobs in that program and they are worth keeping apart.

``msgspec.json.encode(export("trace"))`` encodes the envelope, which is plain builtins and needs no codec at all.
   Any encoder works there, and this is where the speed argument lives, since a queue moves whole envelopes.

``dump`` and ``load`` encode the values inside the envelope.
   That is what the codec is for, and it is the only half nodrill knows about.

Encoding needs nothing but the value, while decoding needs the type to decode into, and a namespace on the wire has forgotten every type it held.
Either the payload carries the type, as the tagged recipe does, or the consumer knows it from the shape of the namespace, as this one does.
Nothing the library could do would remove that choice, which is why the codec is yours rather than ours.

With pickle
-----------

.. warning::

   ``load`` runs on whatever arrived, so a codec that unpickles executes whatever the payload says.
   Use this only where the boundary stays inside one trust domain, such as a process pool this program started.
   Never on a broker, an HTTP header, or anything a different deployment can write.

.. code-block:: python

   import pickle
   from base64 import b64decode, b64encode

   set_codec(
       dump=lambda values: {"pickled": b64encode(pickle.dumps(values)).decode()},
       load=lambda values: pickle.loads(b64decode(values["pickled"])),
   )

The base64 is not decoration.
The envelope has to survive :func:`json.dumps` for the queue and header recipes to keep working, and a ``bytes`` does not, so the codec renders its own output as a string like any other portable value.

Both sides also need the same classes importable at compatible versions, which is the cost pickle always has and the reason the envelope's own version field cannot help you here.
The envelope version describes nodrill's format, not your schema.

Notes
-----

The codec is process-wide and set at startup, because both ends of a boundary have to agree on the format and a scope cannot decide that for a payload somebody else will read.
:func:`~nodrill.isolate` deliberately does not clear it, for the same reason it does not clear :func:`~nodrill.annotate_exceptions`.

Each call to ``set_codec`` states the whole codec, so ``set_codec()`` clears both halves.
A service that only produces registers ``dump`` alone, and its consumer, which is usually a different program, registers the ``load`` that matches.

An exception from your codec arrives as itself, since its own message says more about what failed than any wrapper could.
A ``dump`` that returns something other than a mapping of names to values is the one thing the library says for itself.

.. seealso::

   :doc:`/content/howto/carry-context-onto-a-queue` and :doc:`/content/howto/carry-context-into-a-process-pool` for the boundaries this feeds.
   :doc:`/content/topics/concurrency` for what crosses a boundary without a codec at all.
