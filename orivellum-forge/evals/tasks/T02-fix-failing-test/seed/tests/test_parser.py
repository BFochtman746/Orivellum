"""Tests for parse_csv_row. Do NOT modify this file."""
import pytest
from parser import parse_csv_row


def test_basic():
    assert parse_csv_row("a,b,c") == ["a", "b", "c"]


def test_empty_string():
    assert parse_csv_row("") == [""]


def test_single_value():
    assert parse_csv_row("hello") == ["hello"]


def test_trailing_whitespace():
    """Values with trailing spaces should be stripped."""
    result = parse_csv_row("alice , bob , carol ")
    assert result == ["alice", "bob", "carol"], (
        f"Expected stripped values, got: {result}"
    )


def test_leading_whitespace():
    """Values with leading spaces should be stripped."""
    result = parse_csv_row(" x, y, z")
    assert result == ["x", "y", "z"], (
        f"Expected stripped values, got: {result}"
    )
