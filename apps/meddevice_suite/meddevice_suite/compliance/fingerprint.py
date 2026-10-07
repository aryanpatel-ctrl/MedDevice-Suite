"""Content fingerprints shared by electronic signatures and the audit trail.

A fingerprint is the SHA-256 of a canonical JSON rendering, so the same content
always gives the same fingerprint and any change gives a different one.
"""

import hashlib
import json

import frappe
from frappe import _
from frappe.model.document import Document

# Fields that change without the record's content changing.
VOLATILE_FIELDS = frozenset(
	{"modified", "modified_by", "_user_tags", "_comments", "_assign", "_liked_by", "_seen"}
)


def record_content(doc: Document) -> dict:
	"""The record's content, child rows included, without bookkeeping fields."""
	return without_volatile_fields(doc.as_dict(no_private_properties=True, convert_dates_to_str=True))


def without_volatile_fields(value):
	if isinstance(value, dict):
		return {
			key: without_volatile_fields(item) for key, item in value.items() if key not in VOLATILE_FIELDS
		}
	if isinstance(value, list):
		return [without_volatile_fields(item) for item in value]
	return value


def sha256(value) -> str:
	canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)
	return hashlib.sha256(canonical.encode()).hexdigest()


def plain_text(value: str | None) -> str | None:
	"""A reason or comment that goes into a fingerprint, refused if Frappe would rewrite it.

	Frappe strips anything that looks like HTML from a text field when it is stored, after
	the fingerprint is taken: "from <L-001> to <L-002>" would be stored as "from  to ",
	losing what the person wrote and breaking the fingerprint. Refusing < and > keeps every
	reason exactly as written.
	"""
	if value and ("<" in value or ">" in value):
		frappe.throw(_("Write the reason without the characters < and >."))
	return value


def chain_head(
	doctype: str, link_field: str, hash_field: str, reference: tuple[str, str]
) -> frappe._dict | None:
	"""The newest entry of a record's chain: the one no other entry names as the one before it.

	Found by the links rather than by time, which a clock change could reorder, and read with a
	lock, so that two transactions adding to the same chain at once wait for each other and the
	second sees the first's entry instead of an old snapshot.
	"""
	entry = frappe.qb.DocType(doctype)
	successor = frappe.qb.DocType(doctype).as_("successor")
	heads = (
		frappe.qb.from_(entry)
		.left_join(successor)
		.on(successor[link_field] == entry.name)
		.select(entry.name, entry[hash_field])
		.where(entry.reference_doctype == reference[0])
		.where(entry.reference_name == reference[1])
		.where(successor.name.isnull())
		.orderby(entry.creation, order=frappe.qb.desc)
		.limit(1)
		.for_update()
		.run(as_dict=True)
	)
	return heads[0] if heads else None
