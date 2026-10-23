"""The environment reader behind every start-up variable (spec: docs/architecture/specs/operations.md §1, ADR-0106)."""

from __future__ import annotations

import pytest

from arcviz.envconfig import ConfigError, Env


def test_unset_and_blank_values_give_the_default():
    env = Env({"BLANK": "  "})
    assert env.integer("MISSING", 7) == 7
    assert env.integer("BLANK", 7) == 7
    assert env.number("MISSING", 1.5) == 1.5
    assert env.optional_integer("MISSING") is None
    assert env.optional_number("BLANK") is None
    assert env.text("MISSING", "x") == "x"
    assert env.choice("MISSING", ("a", "b"), "a") == "a"
    env.check()  # nothing recorded


def test_valid_values_are_parsed_and_stripped():
    env = Env({"N": " 12 ", "F": "0.25", "T": " hi ", "C": "JSON"})
    assert env.integer("N", 0) == 12
    assert env.number("F", 1.0, minimum=0.1) == 0.25
    assert env.optional_integer("N") == 12
    assert env.optional_number("F") == 0.25
    assert env.text("T") == "hi"
    assert env.choice("C", ("text", "json"), "text") == "json"
    env.check()


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda e: e.integer("V", 3), "V must be a non-negative integer, got 'abc'."),
        (lambda e: e.integer("V", 3, minimum=1), "V must be an integer of at least 1, got 'abc'."),
        (lambda e: e.integer("V", 3, minimum=1, maximum=9), "V must be an integer from 1 to 9, got 'abc'."),
        (lambda e: e.optional_integer("V"), "V must be a non-negative integer, got 'abc'."),
        (lambda e: e.number("V", 3.0), "V must be a non-negative number, got 'abc'."),
        (lambda e: e.optional_number("V"), "V must be a non-negative number, got 'abc'."),
        (lambda e: e.choice("V", ("x", "y"), "x"), "V must be one of x, y, got 'abc'."),
    ],
)
def test_a_malformed_value_is_recorded_with_variable_format_and_value(call, message):
    env = Env({"V": "abc"})
    call(env)
    assert env.problems == [message]
    with pytest.raises(ConfigError) as caught:
        env.check()
    assert caught.value.problems == (message,)
    assert isinstance(caught.value, ValueError)  # callers that catch ValueError keep working


@pytest.mark.parametrize(
    ("raw", "kwargs", "message"),
    [
        ("-1", {}, "V must be a non-negative integer, got '-1'."),
        ("0", {"minimum": 1}, "V must be an integer of at least 1, got '0'."),
        ("70000", {"minimum": 1, "maximum": 65535}, "V must be an integer from 1 to 65535, got '70000'."),
        ("1.5", {}, "V must be a non-negative integer, got '1.5'."),
        ("1e3", {}, "V must be a non-negative integer, got '1e3'."),
    ],
)
def test_integers_out_of_range_or_not_integral(raw, kwargs, message):
    env = Env({"V": raw})
    assert env.integer("V", 5, **kwargs) == 5  # the default, so parsing goes on
    assert env.problems == [message]


@pytest.mark.parametrize("raw", ["-0.5", "nan", "inf", "-inf", "soon"])
def test_floats_must_be_finite_and_not_below_the_minimum(raw):
    env = Env({"V": raw})
    assert env.number("V", 2.0) == 2.0
    assert len(env.problems) == 1 and env.problems[0].startswith("V must be") and repr(raw) in env.problems[0]


def test_a_float_minimum_and_unit_are_named():
    env = Env({"V": "0.01"})
    env.number("V", 5.0, minimum=0.1, unit="seconds")
    assert env.problems == ["V must be a number of seconds of at least 0.1, got '0.01'."]


def test_every_problem_is_collected_and_reported_together():
    env = Env({"A": "x", "B": "-3", "C": "zzz"})
    env.integer("A", 1)
    env.integer("B", 1)
    env.choice("C", ("on", "off"), "off")
    env.problem("A and B cannot both be set.")
    with pytest.raises(ConfigError) as caught:
        env.check()
    error = caught.value
    assert len(error.problems) == 4
    text = str(error)
    assert text.startswith("Invalid configuration (4 problems):")
    for problem in error.problems:
        assert f"\n  - {problem}" in text


def test_a_single_problem_reads_as_one_line_after_the_heading():
    error = ConfigError(["X must be a non-negative integer, got 'a'."])
    assert str(error) == "Invalid configuration:\n  - X must be a non-negative integer, got 'a'."


def test_config_errors_merge():
    merged = ConfigError.merge([ConfigError(["a"]), ConfigError(["b", "c"])])
    assert merged.problems == ("a", "b", "c")


def test_the_default_environment_is_os_environ(monkeypatch):
    monkeypatch.setenv("ARCVIZ_TEST_ENVCONFIG", "4")
    assert Env().integer("ARCVIZ_TEST_ENVCONFIG", 1) == 4
