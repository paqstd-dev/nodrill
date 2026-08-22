.. _ref-declaring:

Declared keys
=============

.. currentmodule:: nodrill

What the application knows about its context keys, recorded next to the key and read by the catalogue, the miss message and the fallback report.

declare
-------

.. function:: declare(key, /, *, doc=None, provided_by=(), fallback=None)

   Record what a context key is for, who provides it, and how its fallback is meant.
   Returns ``key`` unchanged, or a class decorator when no key is given.

   :param key: A class, a string name, or a :func:`ref` naming a class.
      Omitted, the call returns a decorator carrying the same metadata, for the class it is placed on.
   :param doc: Free-text prose saying what the key is for, read back through :func:`keys`.
   :param provided_by: The boundaries expected to open a provider for this key, as strings, or one boundary as a bare string.
      A miss for the key names them in its message.
   :param fallback: ``"suspicious"`` to have every :func:`set_default` firing for the class counted, or ``None``.
   :raises TypeError: ``key`` is neither a string, a class nor a ref, a boundary is not a string or names nothing, ``fallback`` is neither ``"suspicious"`` nor ``None``, or ``fallback`` is given for a string key, which has no factory to count.

   The declaration goes into an import-time catalogue and changes no lookup behaviour at all.
   Nothing is constructed, cached or validated, so this is documentation with teeth rather than a container registration.

   .. code-block:: python

      @nodrill.declare(
          doc="Who is acting and how they got here",
          provided_by=("http middleware", "celery worker", "management command"),
          fallback="suspicious",
      )
      @dataclass(frozen=True)
      class Origin:
          ...

   The call form covers keys you do not own and string keys.

   .. code-block:: python

      nodrill.declare("audit", doc="What the request is doing", provided_by="request boundary")
      nodrill.declare(ref("app.models:Origin"), provided_by=("http middleware",))

   A :func:`ref` key is not imported by the call, so a module can declare a key that lives in a module importing it back.
   Such a declaration takes effect once the ref has resolved on its own, which is when its boundaries start reaching the message and its fallback starts counting, and a :func:`resolve_refs` at startup makes that moment startup.
   A ref that resolves to something no lookup could take, a function or a module, has its declaration dropped, and :func:`explain` reports the drop.
   One that resolves to a string keeps its declaration but loses a ``fallback`` marker the same way, since a string key has no factory to count.
   Declaring a key twice keeps the later declaration to take effect, as :func:`set_default` keeps the later factory.
   :func:`isolate` rolls back declarations made inside its block and leaves pre-existing ones visible, while a declaration a module body made during an import inside the block belongs to the module and stays.

keys
----

.. function:: keys()

   Return the declared keys as a read-only mapping of key to :class:`Declaration`, keyed exactly as :func:`use` looks values up.

   For a startup check, an admin page or a test.

   .. code-block:: python

      for key, record in nodrill.keys().items():
          print(key, "—", record.doc)

   The result is a snapshot, and a declaration made after the call does not appear in it.
   The call imports nothing, so a declaration made through a :func:`ref` appears once the ref has resolved, and the catalogue lists what the modules imported so far have declared.

Declaration
-----------

.. class:: Declaration

   The record :func:`keys` holds for one declared key.
   A frozen dataclass, compared by value.

   .. attribute:: key

      The declared key, the string or the class, with a :func:`ref` already resolved to its target.

   .. attribute:: doc

      The free-text prose from the declaration, or ``None``.

   .. attribute:: provided_by

      The boundaries expected to provide the key, as a tuple of strings, possibly empty.

   .. attribute:: fallback

      ``"suspicious"`` when the declaration marked the class's :func:`set_default` factory as one to count, or ``None``.

.. rubric:: See also

:ref:`topics-declaring` explains what a declaration buys operationally.
:func:`set_default` is the fallback a ``"suspicious"`` declaration counts, and :func:`explain` is where the count is reported.
