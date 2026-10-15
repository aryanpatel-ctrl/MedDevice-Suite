"""Indexes this app's hot paths need on tables it does not define (URS-PLT-021).

The official apps are never edited (rules.md section 1), so an index the app needs on ERPNext's
tables, or on a column it adds to them, is configuration: applied by `apply_configuration` on
install and every migrate, idempotent, and made the way that outlives an ERPNext upgrade.

- A column Frappe types as a short string (a link, a name): a Property Setter marks the field
  `search_index`, and the index is made under the name Frappe gives a field's own index
  (`<field>_index`). Frappe's schema sync, which runs whenever an upgrade changes the record
  type, drops a one-column index on a field its definition does not mark as indexed
  (frappe/database/schema.py); the Property Setter is part of that definition, kept in the database
  across upgrades and applied on top of whatever the new ERPNext version ships, so the sync keeps
  the index and makes it again if it is ever missing. A bare `add_index` would be dropped by the
  next sync of the record type and come back only at the end of the migrate.
- A file column (Attach, stored as text): MariaDB indexes text only by a prefix, which a field's
  `search_index` cannot express, and Frappe's schema sync never adds or drops an index on a text
  column. The index is made directly on the first `FILE_PREFIX` characters of the address, followed
  by the record's name: two columns, so Frappe does not also record it as a field's search index
  (which it does for a one-column index made outside a migrate, and which a text field cannot
  hold). A file address is far shorter than the prefix, so a lookup by address is exact.
"""

import frappe
from frappe.custom.doctype.property_setter.property_setter import make_property_setter

# ERPNext columns a hot path reads by value, which ERPNext does not index.
SEARCHED: tuple[tuple[str, str], ...] = (
	# A lot's newest incoming inspection: the QA release and Lots Awaiting Release (My Work).
	("Quality Inspection", "batch_no"),
	# What the document being posted takes of each lot: first expiry, first out, on every issue.
	("Serial and Batch Bundle", "voucher_no"),
)
# Characters of a file's address indexed; an address is "/private/files/<name>", far shorter.
FILE_PREFIX = 255


def evidence_columns() -> list[tuple[str, str]]:
	"""The file columns `compliance.evidence_files` looks a file up by, on every save of a file and
	every check of whether it may be changed or deleted."""
	from meddevice_suite.compliance.evidence_files import EVIDENCE

	return [(doctype, field) for doctype, field, _when in EVIDENCE]


def file_index_name(field: str) -> str:
	return f"{field}_file_index"


def apply_indexes() -> None:
	for doctype, field in SEARCHED:
		index_field(doctype, field)
	for doctype, field in evidence_columns():
		frappe.db.add_index(
			doctype, [f"`{field}`({FILE_PREFIX})", "`name`"], index_name=file_index_name(field)
		)


def index_field(doctype: str, field: str) -> None:
	"""The field marked indexed in its record type's definition, and its index made now."""
	if (
		frappe.db.get_value(
			"Property Setter",
			{"doc_type": doctype, "field_name": field, "property": "search_index"},
			"value",
		)
		!= "1"
	):
		# Not validated against the record type: ERPNext's own definition is not this app's to check.
		make_property_setter(doctype, field, "search_index", "1", "Check", validate_fields_for_doctype=False)
	frappe.db.add_index(doctype, [field])  # named <field>_index, as Frappe names a field's index
