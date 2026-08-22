"""declare(), keys(), and what a declaration changes for errors and fallbacks."""

import importlib
import pickle
from pathlib import Path

import pytest

import nodrill
from nodrill import (
    Declaration,
    NoProviderError,
    declare,
    isolate,
    keys,
    provider,
    ref,
    set_default,
    use,
)


class Origin:
    pass


class Session:
    pass


class Base:
    pass


class Sub(Base):
    pass


class TestDeclareForms:
    def test_the_call_form_returns_the_class(self) -> None:
        """declare(cls, ...) hands the class back, as set_default() does."""
        assert declare(Origin, doc="who is acting") is Origin

    def test_the_call_form_returns_the_string(self) -> None:
        """declare('name', ...) hands the name back."""
        assert declare("audit", doc="the audit trail") == "audit"

    def test_the_decorator_form_returns_the_class(self) -> None:
        """A bare-metadata declare() decorates a class and returns it."""

        @declare(doc="who is acting", provided_by=("http middleware",))
        class Decorated:
            pass

        assert keys()[Decorated].provided_by == ("http middleware",)

    def test_the_call_form_returns_the_ref(self) -> None:
        """declare(ref(...)) returns the ref itself, unresolved."""
        late = ref("nodrill_nonexistent.module:Nothing")
        assert declare(late, doc="never imported") is late

    def test_a_bare_string_names_one_boundary(self) -> None:
        """provided_by accepts a single boundary without the tuple."""
        declare(Origin, provided_by="http middleware")
        assert keys()[Origin].provided_by == ("http middleware",)

    def test_declaring_twice_keeps_the_later_declaration(self) -> None:
        """A second declare() for the same key wins, as set_default() does."""
        declare(Origin, doc="first")
        declare(Origin, doc="second")
        assert keys()[Origin].doc == "second"


class TestDeclareValidation:
    def test_an_instance_is_rejected(self) -> None:
        """A key must be a string, a class or a ref."""
        with pytest.raises(TypeError, match="expects a string name, a class, or a ref"):
            declare(Origin())  # type: ignore[call-overload]  # pyright: ignore[reportArgumentType]

    def test_a_non_string_doc_is_rejected(self) -> None:
        """A doc carries prose or nothing."""
        with pytest.raises(TypeError, match=r"declare\(doc=\.\.\.\) expects a string"):
            declare(Origin, doc=7)  # type: ignore[call-overload]  # pyright: ignore[reportArgumentType]

    def test_an_unknown_fallback_is_rejected(self) -> None:
        """The fallback marker accepts 'suspicious' and nothing else."""
        with pytest.raises(TypeError, match="accepts 'suspicious' or None"):
            declare(Origin, fallback="quiet")  # type: ignore[call-overload]  # pyright: ignore[reportArgumentType]

    def test_a_fallback_on_a_string_key_is_rejected(self) -> None:
        """A string key has no set_default() factory, so there is nothing to count."""
        with pytest.raises(TypeError, match="applies to class keys"):
            declare("audit", fallback="suspicious")  # type: ignore[call-overload]  # pyright: ignore[reportCallIssue]

    def test_a_non_string_boundary_is_rejected(self) -> None:
        """Every entry of provided_by names a boundary as a string."""
        with pytest.raises(TypeError, match=r"provided_by=\.\.\.\) expects strings"):
            declare(Origin, provided_by=("http middleware", 3))  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]

    def test_a_blank_boundary_is_rejected(self) -> None:
        """An empty boundary would render a sentence naming nothing."""
        with pytest.raises(TypeError, match="names nothing"):
            declare(Origin, provided_by="")

    def test_an_explicit_none_is_rejected(self) -> None:
        """declare(None) is a mistake to report, not the decorator form."""
        with pytest.raises(TypeError, match="got NoneType"):
            declare(None)  # type: ignore[call-overload]  # pyright: ignore[reportArgumentType]

    def test_a_scalar_provided_by_is_rejected_with_the_crafted_message(self) -> None:
        """A non-iterable provided_by gets the declare() wording, not tuple()'s."""
        with pytest.raises(TypeError, match=r"provided_by=\.\.\.\) expects strings"):
            declare(Origin, provided_by=5)  # type: ignore[call-overload]  # pyright: ignore[reportArgumentType]

    def test_the_decorator_form_validates_eagerly(self) -> None:
        """A typo is reported at the declare() call, not at some later application."""
        with pytest.raises(TypeError, match="accepts 'suspicious' or None"):
            declare(fallback="bogus")  # type: ignore[call-overload]  # pyright: ignore[reportArgumentType]


class TestKeys:
    def test_the_catalogue_is_keyed_as_use_looks_up(self) -> None:
        """A class key and a string key sit under exactly what use() takes."""
        declare(Origin, doc="who is acting")
        declare("audit", doc="the audit trail")
        assert keys()[Origin].key is Origin
        assert keys()["audit"].key == "audit"

    def test_the_record_carries_what_was_declared(self) -> None:
        """A Declaration holds the key, the doc, the boundaries and the fallback."""
        declare(Origin, doc="who", provided_by=("edge",), fallback="suspicious")
        record = keys()[Origin]
        assert record == Declaration(Origin, "who", ("edge",), "suspicious")

    def test_the_record_is_frozen(self) -> None:
        """A catalogue entry cannot be edited in place."""
        declare(Origin)
        with pytest.raises(AttributeError):
            keys()[Origin].doc = "rewritten"  # type: ignore[misc]  # pyright: ignore[reportAttributeAccessIssue]

    def test_the_mapping_is_read_only(self) -> None:
        """keys() hands out a view no caller can write through."""
        declare(Origin)
        with pytest.raises(TypeError):
            keys()[Session] = keys()[Origin]  # type: ignore[index]  # pyright: ignore[reportIndexIssue]

    def test_the_mapping_is_a_snapshot(self) -> None:
        """A declaration made after the call does not appear in the earlier view."""
        declare(Origin)
        snapshot = keys()
        declare(Session)
        assert Session not in snapshot
        assert Session in keys()


class TestDeclaredErrors:
    def test_a_declared_class_key_names_its_boundaries(self) -> None:
        """The miss lists the boundaries straight from provided_by."""
        declare(Origin, provided_by=("http middleware", "celery worker"))
        with pytest.raises(NoProviderError) as exc_info:
            use(Origin)
        message = str(exc_info.value)
        assert "Origin is declared as provided by http middleware, celery worker." in message
        assert "Hint: did you forget `with provider(Origin(...))`?" in message
        assert exc_info.value.provided_by == ("http middleware", "celery worker")

    def test_a_declared_string_key_names_its_boundaries(self) -> None:
        """The string arm of the message gains the same sentence."""
        declare("audit", provided_by=("request boundary",))
        with pytest.raises(NoProviderError) as exc_info:
            use("audit")
        message = str(exc_info.value)
        assert "'audit' is declared as provided by request boundary." in message
        assert "Hint: did you forget `with provider('audit')`?" in message

    def test_a_declaration_without_boundaries_keeps_the_ordinary_hint(self) -> None:
        """Only provided_by changes the message, since only it has boundaries to name."""
        declare(Origin, doc="who is acting")
        with pytest.raises(NoProviderError) as exc_info:
            use(Origin)
        message = str(exc_info.value)
        assert "declared as provided by" not in message
        assert "Hint: did you forget `with provider(Origin(...))`?" in message

    def test_the_subclass_hint_and_the_boundaries_stack(self) -> None:
        """An active subclass and the declared boundaries are both worth naming."""
        declare(Base, provided_by=("http middleware",))
        with provider(Sub()), pytest.raises(NoProviderError) as exc_info:
            use(Base)
        message = str(exc_info.value)
        assert "Sub is active and subclasses Base" in message
        assert "Base is declared as provided by http middleware." in message

    def test_the_did_you_mean_hint_still_runs_for_a_declared_string(self) -> None:
        """A typo suggestion and the declared boundaries stack."""
        declare("audit", provided_by=("request boundary",))
        with provider("audit"), pytest.raises(NoProviderError) as exc_info:
            use("audot")
        assert "Did you mean 'audit'?" in str(exc_info.value)

    def test_the_error_pickles_with_its_boundaries(self) -> None:
        """provided_by survives the round trip a worker boundary puts it through."""
        declare(Origin, provided_by=("http middleware",))
        with pytest.raises(NoProviderError) as exc_info:
            use(Origin)
        clone = pickle.loads(pickle.dumps(exc_info.value))
        assert clone.provided_by == ("http middleware",)
        assert str(clone) == str(exc_info.value)

    def test_an_error_from_an_older_release_still_answers(self) -> None:
        """One unpickled without the field falls back to the class default."""
        with pytest.raises(NoProviderError) as exc_info:
            use(Origin)
        aged = pickle.loads(pickle.dumps(exc_info.value))
        del aged.__dict__["provided_by"]
        assert aged.provided_by == ()


class TestUndeclaredUnchanged:
    def test_an_undeclared_class_key_reads_exactly_as_today(self) -> None:
        """The promise is byte for byte, not roughly."""
        with pytest.raises(NoProviderError) as exc_info:
            use(Session)
        assert str(exc_info.value) == (
            "use(Session): no active provider for Session. No providers are active. "
            "Hint: did you forget `with provider(Session(...))`? A fallback can be "
            "registered with `set_default(Session, ...)`. Run under `with "
            "nodrill.debug():` to find out whether the value is open on another "
            "thread or task."
        )

    def test_an_undeclared_string_key_reads_exactly_as_today(self) -> None:
        """The string arm is pinned the same way."""
        with pytest.raises(NoProviderError) as exc_info:
            use("audit")
        assert str(exc_info.value) == (
            "use('audit'): no active provider for 'audit'. No providers are active. "
            "Hint: did you forget `with provider('audit')`? Run under `with "
            "nodrill.debug():` to find out whether the value is open on another "
            "thread or task."
        )


class TestSuspiciousFallback:
    def test_a_suspicious_fallback_is_counted(self) -> None:
        """Every set_default() firing for the declared class lands in the count."""
        declare(Origin, fallback="suspicious")
        set_default(Origin, Origin)
        use(Origin)
        use(Origin)
        assert "the 'suspicious' fallback for Origin has fired 2 times." in nodrill.explain()

    def test_one_firing_reads_in_the_singular(self) -> None:
        """A count of one prints as one time."""
        declare(Origin, fallback="suspicious")
        set_default(Origin, Origin)
        use(Origin)
        assert "has fired 1 time." in nodrill.explain()

    def test_the_count_reports_under_debug_mode_too(self) -> None:
        """The line sits above the ledger when recording is on."""
        declare(Origin, fallback="suspicious")
        set_default(Origin, Origin)
        with nodrill.debug():
            use(Origin)
            report = nodrill.explain()
        assert "the 'suspicious' fallback for Origin has fired 1 time." in report
        assert "no provider block is open" in report

    def test_nothing_fired_prints_nothing(self) -> None:
        """A declared fallback that never ran stays out of the report."""
        declare(Origin, fallback="suspicious")
        set_default(Origin, Origin)
        assert "suspicious" not in nodrill.explain()

    def test_a_call_site_default_is_not_a_firing(self) -> None:
        """Only the registered factory counts, since only it hides a missing provider."""
        declare(Origin, fallback="suspicious")
        assert use(Origin, default=None) is None
        assert "suspicious" not in nodrill.explain()

    def test_a_provided_value_is_not_a_firing(self) -> None:
        """A hit never touches the catalogue."""
        declare(Origin, fallback="suspicious")
        set_default(Origin, Origin)
        with provider(Origin()):
            use(Origin)
        assert "suspicious" not in nodrill.explain()

    def test_redeclaring_without_the_fallback_stops_the_count(self) -> None:
        """The later declaration wins, and takes its firings with it."""
        declare(Origin, fallback="suspicious")
        set_default(Origin, Origin)
        use(Origin)
        declare(Origin)
        use(Origin)
        assert "suspicious" not in nodrill.explain()

    def test_an_undeclared_fallback_is_not_counted(self) -> None:
        """A plain set_default() stays as quiet as it always was."""
        declare(Session, fallback="suspicious")
        set_default(Origin, Origin)
        use(Origin)
        assert "Origin" not in nodrill.explain()

    def test_a_declaration_without_the_marker_never_counts(self) -> None:
        """A doc-only declaration leaves the fallback as quiet as an undeclared one."""
        declare(Origin, doc="who is acting")
        set_default(Origin, Origin)
        use(Origin)
        assert "suspicious" not in nodrill.explain()

    def test_counting_still_works_after_a_restore(self) -> None:
        """The gate keeps reading the tables isolate() restored in place."""
        declare(Origin, fallback="suspicious")
        set_default(Origin, Origin)
        with isolate():
            pass
        use(Origin)
        assert "has fired 1 time." in nodrill.explain()


class TestRefDeclarations:
    def test_a_pending_ref_is_not_listed(self) -> None:
        """Nothing is imported by declare() or keys(), so the entry waits."""
        declare(ref("nodrill_nonexistent.module:Nothing"), doc="never imported")
        assert all(getattr(record, "doc", None) != "never imported" for record in keys().values())

    def test_a_resolved_ref_is_listed_under_its_target(self) -> None:
        """Once the ref resolves on its own, the declaration lands on the class."""
        late = ref("nodrill:Namespace")
        declare(late, doc="the namespace key")
        hash(late)
        assert keys()[nodrill.Namespace].doc == "the namespace key"

    def test_a_resolved_ref_reaches_the_error_message(self) -> None:
        """A miss after resolution names the declared boundaries."""
        late = ref(f"{__name__}:Session")
        declare(late, provided_by=("worker boundary",))
        hash(late)
        with pytest.raises(NoProviderError) as exc_info:
            use(Session)
        assert "Session is declared as provided by worker boundary." in str(exc_info.value)

    def test_a_suspicious_ref_starts_counting_once_resolved(self) -> None:
        """The count begins when the declaration lands, not before."""
        late = ref(f"{__name__}:Origin")
        declare(late, fallback="suspicious")
        set_default(Origin, Origin)
        use(Origin)
        assert "suspicious" not in nodrill.explain()
        hash(late)
        use(Origin)
        assert "has fired 1 time." in nodrill.explain()

    def test_a_ref_resolving_to_a_string_drops_the_fallback(self) -> None:
        """A string key's fallback has nothing to count, so it is stripped and reported."""
        late = ref(f"{__name__}:AUDIT_NAME")
        declare(late, fallback="suspicious")
        hash(late)
        assert keys()[AUDIT_NAME].fallback is None
        assert "has nothing to count" in nodrill.explain()

    def test_two_pending_refs_absorb_independently(self) -> None:
        """Resolving one ref does not force or lose the other."""
        first = ref(f"{__name__}:Origin")
        declare(first, doc="the first")
        declare(ref("nodrill_nonexistent.module:Nothing"), doc="the second")
        hash(first)
        catalogue = keys()
        assert catalogue[Origin].doc == "the first"
        assert all(record.doc != "the second" for record in catalogue.values())

    def test_a_ref_resolving_to_a_function_is_dropped_and_reported(self) -> None:
        """A target use() could never look up is refused, and explain() says so."""
        late = ref("nodrill:use")
        declare(late, doc="not a key")
        hash(late)
        catalogue = keys()
        assert all(record.doc != "not a key" for record in catalogue.values())
        assert "declaration was dropped" in nodrill.explain()

    def test_a_ref_resolving_to_an_unhashable_target_is_dropped(self) -> None:
        """An unhashable target is refused before it can poison the catalogue."""
        late = ref(f"{__name__}:PORTS")
        declare(late, doc="a dict")
        late.resolve()
        catalogue = keys()
        assert all(record.doc != "a dict" for record in catalogue.values())
        assert "resolved to a dict" in nodrill.explain()
        with pytest.raises(NoProviderError):
            use(Session)

    def test_a_resolved_pending_entry_does_not_overwrite_a_later_declaration(self) -> None:
        """A direct declare() lands over a ref declaration that resolved before it."""
        late = ref(f"{__name__}:Session")
        declare(late, doc="through the ref")
        hash(late)
        declare(Session, doc="direct and later")
        assert keys()[Session].doc == "direct and later"

    def test_a_dead_ref_stops_costing_scans(self) -> None:
        """The second read skips the scan, since nothing resolved in between."""
        declare(ref("nodrill_nonexistent.module:Nothing"), doc="never")
        keys()
        assert all(record.doc != "never" for record in keys().values())


AUDIT_NAME = "audit trail"

PORTS = {"http": 80}


class TestIsolate:
    def test_declarations_survive_into_the_block(self) -> None:
        """Configuration made outside is visible inside, as a default is."""
        declare(Origin, doc="who is acting")
        with isolate():
            assert keys()[Origin].doc == "who is acting"

    def test_declarations_made_inside_are_rolled_back(self) -> None:
        """The block's own declarations end with it."""
        with isolate():
            declare(Origin, doc="who is acting")
        assert Origin not in keys()

    def test_a_redeclaration_inside_is_rolled_back_to_the_outer_one(self) -> None:
        """The outer declaration comes back exactly."""
        declare(Origin, doc="outer")
        with isolate():
            declare(Origin, doc="inner")
        assert keys()[Origin].doc == "outer"

    def test_firings_inside_are_rolled_back(self) -> None:
        """One test's counted traffic never reaches another test's report."""
        declare(Origin, fallback="suspicious")
        set_default(Origin, Origin)
        with isolate():
            set_default(Origin, Origin)
            use(Origin)
            assert "has fired 1 time." in nodrill.explain()
        assert "suspicious" not in nodrill.explain()

    def test_a_pending_ref_made_inside_is_rolled_back(self) -> None:
        """A block's pending declaration is the block's to forget."""
        with isolate():
            late = ref(f"{__name__}:Session")
            declare(late, doc="inner")
        hash(late)
        assert Session not in keys()

    def test_a_module_body_declaration_survives_the_block_that_imported_it(self) -> None:
        """A declaration belongs to the module that keeps it, exactly as a ref does."""
        with isolate():
            module = importlib.import_module("tests.imported_declarations")
        assert keys()[module.ImportScope].provided_by == ("test boundary",)

    def test_a_module_body_pending_ref_survives_the_block_that_imported_it(self) -> None:
        """The pending list holds what an import created, past the block's end."""
        module = importlib.import_module("tests.imported_declarations")
        hash(module.PENDING_AT_IMPORT)
        assert keys()[module.LateScope].doc == "pending since this module imported"

    def test_a_module_declaration_survives_a_redeclaration_inside_the_block(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Rolling back the block's override brings the module's declaration back."""
        # A module of its own, so no other test can have imported it first.
        source = (
            "from nodrill import declare\n"
            "\n"
            "\n"
            '@declare(doc="from the module body")\n'
            "class Fresh:\n"
            "    pass\n"
        )
        (tmp_path / "declared_fresh_module.py").write_text(source)
        monkeypatch.syspath_prepend(str(tmp_path))
        with isolate():
            module = importlib.import_module("declared_fresh_module")
            declare(module.Fresh, doc="override")
            assert keys()[module.Fresh].doc == "override"
        assert keys()[module.Fresh].doc == "from the module body"

    def test_a_pending_declaration_absorbed_inside_the_block_stays_absorbed(self) -> None:
        """Pre-block configuration whose ref resolved in the block does not re-land later."""
        late = ref(f"{__name__}:Session")
        declare(late, doc="from the ref")
        with isolate():
            hash(late)
            assert keys()[Session].doc == "from the ref"
        assert keys()[Session].doc == "from the ref"
        declare(Session, doc="later and direct")
        assert keys()[Session].doc == "later and direct"
