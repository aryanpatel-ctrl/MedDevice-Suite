"""Content fingerprints shared by electronic signatures and the audit trail.

A fingerprint is taken over a canonical JSON rendering, so the same content always gives the same
fingerprint and any change gives a different one.

Each audit event and each signature is fingerprinted together with the fingerprint of the entry
before it on the same record, so the entries form a chain. Two versions of that fingerprint exist
(21 CFR 11.10(e), 11.70; independent review 2026-10-15), and each entry states its own in
`fingerprint_version`, set by the service that records it and as unchangeable as the rest:

1. SHA-256, unkeyed. Anyone able to write the database could rewrite an older entry and compute
   every fingerprint after it again. Entries recorded before version 2 keep it: nothing recorded is
   ever rewritten, and they are verified as they were made. An entry with no version is version 1.
2. HMAC-SHA256 under a key derived from the site's `encryption_key`, which is kept in the site's
   site_config.json, outside the database: database access alone no longer lets anyone compute a
   fingerprint, so an entry rewritten in the database is detected however the chain after it is
   recomputed. Every entry recorded now is version 2.

Rewriting entries as version 1, to escape the key, is detected too: a version-1 entry that follows a
version-2 entry in its chain, or that is dated after the moment this site began recording version 2
(`keyed_since`, kept in site_config.json beside the key), is reported. What the key cannot protect is
a chain recorded wholly before that moment; the anchors (audit_anchor) and the period archives
(archive) are the evidence for those.

The site's `encryption_key` must be kept with the site's backups, as Frappe already requires for the
passwords it stores: without it, no version-2 entry verifies.
"""

import hashlib
import hmac
import json
from collections.abc import Callable
from datetime import datetime
from typing import Any, NamedTuple

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, format_datetime, get_datetime, now_datetime

FIRST_VERSION = 1  # SHA-256, unkeyed
KEYED_VERSION = 2  # HMAC-SHA256 under a key held outside the database
CURRENT_VERSION = KEYED_VERSION
VERSIONS = (FIRST_VERSION, KEYED_VERSION)
# site_config.json: when this site began recording version-2 fingerprints.
KEYED_SINCE = "meddevice_suite_keyed_fingerprints_since"

from meddevice_suite.compliance.audit_scope import recorded_as

# Fields that change without the record's content changing.
VOLATILE_FIELDS = frozenset(
	{"modified", "modified_by", "_user_tags", "_comments", "_assign", "_liked_by", "_seen"}
)


def record_content(doc: Document) -> dict:
	"""The record's content, child rows included, without bookkeeping fields."""
	return without_volatile_fields(doc.as_dict(no_private_properties=True, convert_dates_to_str=True))


def without_volatile_fields(value: Any) -> Any:
	if isinstance(value, dict):
		return {
			key: without_volatile_fields(item) for key, item in value.items() if key not in VOLATILE_FIELDS
		}
	if isinstance(value, list):
		return [without_volatile_fields(item) for item in value]
	return value


def canonical(value: Any) -> bytes:
	return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False).encode()


def sha256(value: Any) -> str:
	return hashlib.sha256(canonical(value)).hexdigest()


def keyed_sha256(trail: str, value: Any) -> str:
	"""HMAC-SHA256 of the value under the site's key for `trail` (version 2)."""
	return hmac.new(trail_key(trail), canonical(value), hashlib.sha256).hexdigest()


def trail_key(trail: str) -> bytes:
	"""The key of one trail, derived from the site's encryption key: the encryption key itself is
	never used as it is, and one trail's key never computes another trail's fingerprints."""
	from frappe.utils.password import get_encryption_key

	label = f"meddevice_suite/{trail}/fingerprint/v{KEYED_VERSION}".encode()
	return hmac.new(get_encryption_key().encode(), label, hashlib.sha256).digest()


def version_of(entry: Document | frappe._dict) -> int:
	"""An entry's fingerprint version; an entry recorded before versions were stored is version 1."""
	return cint(entry.get("fingerprint_version")) or FIRST_VERSION


def keyed_since() -> datetime:
	"""When this site began recording version-2 fingerprints.

	Kept in site_config.json, beside the key, where database access alone cannot move it. Recorded
	once, by the migrate that brings version 2 (`setup.install.apply_configuration`) or else the
	first time it is needed, and never changed. Workers still running the code before version 2
	while that migrate runs would record version-1 entries after it, which verification reports:
	the upgrade that brings version 2 stops the site's workers first, as change control does for
	any upgrade."""
	if not (since := frappe.conf.get(KEYED_SINCE)):
		from frappe.installer import update_site_config

		since = str(now_datetime())
		update_site_config(KEYED_SINCE, since)
		frappe.local.conf[KEYED_SINCE] = since
	return get_datetime(since)


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
	second sees the first's entry instead of an old snapshot. Entries recorded under a former name
	of the record type are part of the chain (`audit_scope.recorded_as`).
	"""
	entry = frappe.qb.DocType(doctype)
	successor = frappe.qb.DocType(doctype).as_("successor")
	heads = (
		frappe.qb.from_(entry)
		.left_join(successor)
		.on(successor[link_field] == entry.name)
		.select(entry.name, entry[hash_field])
		.where(entry.reference_doctype.isin(recorded_as(reference[0])))
		.where(entry.reference_name == reference[1])
		.where(successor.name.isnull())
		.orderby(entry.creation, order=frappe.qb.desc)
		.limit(1)
		.for_update()
		.run(as_dict=True)
	)
	return heads[0] if heads else None


class Trail(NamedTuple):
	"""How one trail stores its chain."""

	doctype: str
	link: str  # the field naming the entry before it on the same record
	fingerprint: str  # the field holding the entry's fingerprint
	recorded_at: str  # the time the entry records, part of its fingerprint
	name_at: str  # the record's name when the entry was recorded, part of its fingerprint


def chain_problems(
	trail: Trail,
	entries: list[frappe._dict],
	names: set[str],
	fingerprint_of: Callable[[frappe._dict, str], str | None],
) -> list[str]:
	"""What is wrong with one record's chain of entries; an empty list means it is intact.

	One rule for both trails (independent review 2026-10-15: the audit trail accepted a second start
	of a chain, which the signatures refused). The chain is followed by its links, not by time, so a
	server clock set back does not read as tampering. Reported: an entry whose content does not match
	its fingerprint; an entry missing before another; two entries following the same one, or a chain
	starting twice, either of which means an entry was inserted; an entry recorded for a name the
	record has never had, which was moved here from another record; an unknown fingerprint version;
	and a version-1 entry where only version 2 can be (see the module docstring).
	"""
	by_name = {entry.name: entry for entry in entries}
	problems, followed, firsts = [], set(), []
	since = keyed_since()
	for entry in entries:
		previous = None
		# First, so that an entry moved here is named as such even when the entry before it stayed
		# on the record it was moved from.
		if entry.get(trail.name_at) not in names:
			problems.append(
				_(
					"{0}: it was recorded for {1}, a name this record has never had; it belongs to another record."
				).format(entry.name, entry.get(trail.name_at))
			)
		if link := entry.get(trail.link):
			previous = by_name.get(link)
			if previous is None:
				problems.append(_("{0}: the entry before it, {1}, is missing.").format(entry.name, link))
				continue
			if link in followed:
				problems.append(
					_("{0}: another entry already follows {1}, so one of them was inserted.").format(
						entry.name, link
					)
				)
			followed.add(link)
		else:
			firsts.append(entry.name)
		problems += version_problems(trail, entry, previous, since)
		if entry.get(trail.fingerprint) != fingerprint_of(
			entry, previous.get(trail.fingerprint) if previous else ""
		):
			problems.append(_("{0}: content does not match its fingerprint.").format(entry.name))
	if len(firsts) > 1:
		problems.append(
			_(
				"The chain starts {0} times ({1}); a record's chain starts once, so an entry was inserted."
			).format(len(firsts), ", ".join(sorted(firsts)))
		)
	return problems


def version_problems(
	trail: Trail, entry: frappe._dict, previous: frappe._dict | None, since: datetime
) -> list[str]:
	version = version_of(entry)
	if version not in VERSIONS:
		return [_("{0}: unknown fingerprint version {1}.").format(entry.name, version)]
	if version != FIRST_VERSION:
		return []
	if previous and version_of(previous) != FIRST_VERSION:
		rewritten = _(
			"{0}: it has the unkeyed fingerprint of version 1 but follows {1}, which has the keyed one; "
			"entries are never recorded that way, so it was rewritten."
		).format(entry.name, previous.name)
		return [rewritten]
	if get_datetime(entry.get(trail.recorded_at)) > since:
		rewritten = _(
			"{0}: it has the unkeyed fingerprint of version 1 but is dated {1}, after this site began "
			"recording keyed fingerprints on {2}; it was rewritten."
		).format(entry.name, format_datetime(entry.get(trail.recorded_at)), format_datetime(since))
		return [rewritten]
	return []


def in_chain_order(entries: list[frappe._dict], link: str) -> list[frappe._dict]:
	"""The entries from the first of the chain to the newest, by their links; any that no link
	reaches (a broken chain) follow in the order given."""
	following: dict[str, list[frappe._dict]] = {}
	for entry in entries:
		following.setdefault(entry.get(link) or "", []).append(entry)
	ordered, seen = [], set()
	pending = list(following.get("", []))
	while pending:
		entry = pending.pop(0)
		if entry.name in seen:
			continue
		seen.add(entry.name)
		ordered.append(entry)
		pending[:0] = following.get(entry.name, [])
	return ordered + [entry for entry in entries if entry.name not in seen]
