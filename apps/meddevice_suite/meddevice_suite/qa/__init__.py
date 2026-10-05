"""Requirement traceability for software validation (ISO 13485 4.1.6, 21 CFR 11.10(a))."""

from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def verifies(*requirement_ids: str) -> Callable[[T], T]:
	"""Mark a test function or test class as verification evidence for requirements.

	The traceability matrix reads these markers from source without importing the
	tests, so the ids must be string literals.
	"""
	if not requirement_ids:
		raise ValueError("verifies() needs at least one requirement id")

	def mark(test: T) -> T:
		test.verifies = (*getattr(test, "verifies", ()), *requirement_ids)
		return test

	return mark
