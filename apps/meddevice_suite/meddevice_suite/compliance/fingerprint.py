"""Content fingerprints shared by electronic signatures and the audit trail.

A fingerprint is the SHA-256 of a canonical JSON rendering, so the same content
always gives the same fingerprint and any change gives a different one.
"""

import hashlib
import json

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
