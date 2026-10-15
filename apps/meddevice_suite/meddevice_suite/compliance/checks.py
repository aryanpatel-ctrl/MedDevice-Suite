"""The checks every module of the app shares: the QA role, whether a user holds it, and the guard
that refuses an action with a message.

One definition, so a change to who counts as QA, or to how a refused action is reported, is made
once and reaches every module. A module whose helper behaves differently keeps it, with a comment
saying why, and is listed in the allow-list of qa/test_one_definition.py.
"""

import frappe

QA_ROLE = "Quality Manager"


def require(condition, message: str, exception: type[Exception] = frappe.ValidationError) -> None:
	if not condition:
		frappe.throw(message, exception)


def is_qa(user: str | None = None) -> bool:
	return QA_ROLE in frappe.get_roles(user or frappe.session.user)
