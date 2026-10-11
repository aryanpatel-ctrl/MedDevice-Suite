"""Version-controlled masters (URS-MST-001; D2 "All masters are version controlled and linked
with Document Control"; ISO 13485 4.2.4, 7.3.9).

Each of the nine masters carries a version number and the date that version took effect.
Once a master is in use, a change to what it controls is made only under an approved change
request that lists it (module 36): the request is named for that change and recorded as the
one the master was last changed under, the version is raised, and the audit trail keeps the
values the change replaced. Before a master is in use it is still being set up, and changes
are free. A master in use keeps its name and is never merged into another.

Two changes ERPNext makes itself are held to the same rule: a new default bill of materials
switches the old default off, and cancelling one takes it out of force; and a template item's
change is copied to its variants, which follow it under the template's request.

What each master controls is the part a product, an inspection or a regulatory record
depends on; a note, a description or a contact detail is not controlled. "In use" means a
submitted record depends on it. Both were decided under the client's instruction of
2026-10-08 (every open choice is ours until the client's details arrive).

The inspection template's controlled content and the first issue of a template made before
specifications were controlled are module 6's (`master_data.specification`); this module
gives it its version like the other masters.
"""

import copy
import json
from collections.abc import Callable
from dataclasses import dataclass, field

import frappe
from frappe import _
from frappe.core.doctype.docfield.docfield import DocField
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.model import child_table_fields, default_fields, no_value_fields
from frappe.model.document import Document
from frappe.query_builder import DocType
from frappe.query_builder.functions import Date
from frappe.utils import cint, today

from meddevice_suite.compliance.audit import record, row_content, same
from meddevice_suite.master_data.equipment import CONTROLLED_FIELDS as EQUIPMENT_CONTROLLED
from meddevice_suite.master_data.equipment import approved as equipment_approved
from meddevice_suite.master_data.item_master import APPROVAL_STATUS, APPROVED, UNSET_WHEN_ZERO
from meddevice_suite.master_data.item_master import CONTROLLED_FIELDS as ITEM_DEVICE_FIELDS
from meddevice_suite.qms.change_control import require_approved_change

VERSION, EFFECTIVE_FROM = "mds_version", "mds_effective_from"
# The approved change request named for a change, and, once the change is made, the one it was
# made under; a request is named afresh for each change.
CHANGE_REQUEST, CHANGED_UNDER = "mds_change_request", "mds_changed_under"
SPECIFICATION = "Quality Inspection Template"


def submitted(doctype: str, **filters) -> bool:
	return bool(frappe.db.exists(doctype, {**filters, "docstatus": 1}))


def holds_stock(warehouse: str) -> bool:
	"""Stock has moved through the store (a cancelled movement aside)."""
	return bool(frappe.db.exists("Stock Ledger Entry", {"warehouse": warehouse, "is_cancelled": 0}))


def item_in_use(doc: Document) -> bool:
	"""QA has approved the item (module 1: from approval it is locked), or a submitted record
	depends on it, or on one of its variants: a template's change reaches its variants (ERPNext's
	update_variants)."""
	codes = [doc.name]
	if doc.get("has_variants"):
		codes += frappe.get_all("Item", filters={"variant_of": doc.name}, pluck="name")
	codes = ["in", codes]
	return bool(
		(
			frappe.get_meta("Item").get_field(APPROVAL_STATUS)  # once module 1 is configured
			and frappe.db.exists("Item", {"name": codes, APPROVAL_STATUS: APPROVED})
		)
		or frappe.db.exists("Stock Ledger Entry", {"item_code": codes, "is_cancelled": 0})
		or submitted("BOM", item=codes)
		or submitted("BOM Item", item_code=codes)
		or submitted("Purchase Order Item", item_code=codes)
		or submitted("Sales Order Item", item_code=codes)
		or submitted("Quality Inspection", item_code=codes)
	)


@dataclass(frozen=True)
class Master:
	fields: tuple[str, ...]  # controlled fields of the master itself
	tables: dict[str, tuple[str, ...]] = field(default_factory=dict)  # table -> controlled row fields
	in_use: Callable[[Document], bool] = lambda doc: False


MASTERS: dict[str, Master] = {
	"Item": Master(
		fields=(
			"item_name",
			"item_group",
			"stock_uom",
			"is_stock_item",
			"has_batch_no",
			"has_expiry_date",
			"shelf_life_in_days",
			"gst_hsn_code",
			"quality_inspection_template",
			"inspection_required_before_purchase",
			"inspection_required_before_delivery",
			"disabled",
			*ITEM_DEVICE_FIELDS,  # module 1: type, storage, device class, pack sizes, UDI
		),
		# Module 1: what a variant is (its attribute values), its units and its barcodes.
		tables={
			"attributes": ("attribute", "attribute_value"),
			"uoms": ("uom", "conversion_factor"),
			"barcodes": ("barcode", "barcode_type"),
		},
		in_use=item_in_use,
	),
	# A submitted bill of materials keeps its lines; which one is in force changes under control.
	"BOM": Master(fields=("is_active", "is_default"), in_use=lambda doc: doc.docstatus == 1),
	"Routing": Master(
		fields=("disabled",),
		tables={
			"operations": ("operation", "workstation", "workstation_type", "time_in_mins", "sequence_id")
		},
		in_use=lambda doc: submitted("BOM", routing=doc.name),
	),
	"Operation": Master(
		fields=("workstation", "is_corrective_operation", "quality_inspection_template", "batch_size"),
		in_use=lambda doc: submitted("BOM Operation", operation=doc.name)
		or submitted("Job Card", operation=doc.name),
	),
	"Workstation": Master(
		fields=("workstation_type", "production_capacity", "warehouse", "disabled"),
		in_use=lambda doc: submitted("BOM Operation", workstation=doc.name)
		or submitted("Job Card", workstation=doc.name),
	),
	"Supplier": Master(
		# Not the GSTIN: India Compliance sets it from the party's address straight in the database
		# (gst_india/overrides/address.py), past any check; the address carries it.
		fields=("supplier_name", "supplier_type", "supplier_group", "country", "tax_id", "disabled"),
		in_use=lambda doc: submitted("Purchase Order", supplier=doc.name)
		or submitted("Purchase Receipt", supplier=doc.name),
	),
	"Customer": Master(
		fields=("customer_name", "customer_type", "customer_group", "tax_id", "disabled"),  # GSTIN: as above
		in_use=lambda doc: submitted("Quotation", quotation_to="Customer", party_name=doc.name)
		or submitted("Sales Order", customer=doc.name)
		or submitted("Delivery Note", customer=doc.name),
	),
	"Warehouse": Master(
		fields=("parent_warehouse", "is_group", "warehouse_type", "disabled"),
		in_use=lambda doc: holds_stock(doc.name),
	),
	# Equipment (module 8): what QA approved changes under control once approved or submitted.
	"Asset": Master(
		fields=("asset_name", *EQUIPMENT_CONTROLLED),
		in_use=lambda doc: doc.docstatus == 1 or equipment_approved(doc.name),
	),
	# The specification's content and use are module 6's.
	SPECIFICATION: Master(fields=()),
}


def custom_fields() -> dict[str, list[dict]]:
	fields = {}
	for doctype in MASTERS:
		# A submitted master (bill of materials, asset) is versioned after it is submitted.
		on_submit = int(bool(frappe.get_meta(doctype).is_submittable))
		fields[doctype] = [
			{
				"fieldname": "mds_version_section",
				"label": "Version Control",
				"fieldtype": "Section Break",
				"collapsible": 1,
			},
			{
				"fieldname": VERSION,
				"label": "Version",
				"fieldtype": "Int",
				"insert_after": "mds_version_section",
				"read_only": 1,
				"no_copy": 1,
				"allow_on_submit": on_submit,
				"in_list_view": 0,
			},
			{
				"fieldname": EFFECTIVE_FROM,
				"label": "Version Effective From",
				"fieldtype": "Date",
				"insert_after": VERSION,
				"read_only": 1,
				"no_copy": 1,
				"allow_on_submit": on_submit,
			},
		]
		if doctype != SPECIFICATION:  # module 6 gives the specification its own fields
			fields[doctype] += [
				{
					"fieldname": CHANGE_REQUEST,
					"label": "Change Request for This Change",
					"fieldtype": "Link",
					"options": "Change Request",
					"insert_after": EFFECTIVE_FROM,
					"no_copy": 1,
					"allow_on_submit": on_submit,
					"description": "Once in use: the approved change request this change is made under. Named for each change.",
				},
				{
					"fieldname": CHANGED_UNDER,
					"label": "Last Changed Under",
					"fieldtype": "Link",
					"options": "Change Request",
					"insert_after": CHANGE_REQUEST,
					"read_only": 1,
					"no_copy": 1,
					"allow_on_submit": on_submit,
				},
			]
	return fields


def apply_master_versions() -> None:
	create_custom_fields(custom_fields(), ignore_validate=True)
	# A master made before versioning is version 1, in effect since it was made.
	for doctype in MASTERS:
		table = DocType(doctype)
		(
			frappe.qb.update(table)
			.set(table[VERSION], 1)
			.set(table[EFFECTIVE_FROM], Date(table.creation))
			.where(table[VERSION].isnull() | (table[VERSION] == 0))
		).run()


def setting_up() -> bool:
	return bool(frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_patch)


def first_version(doc: Document, method: str | None = None) -> None:
	"""before_insert of a master: version 1, in effect from today."""
	if doc.doctype in MASTERS:
		doc.set(VERSION, 1)
		doc.set(EFFECTIVE_FROM, today())


def changed_fields(doc: Document, before: Document) -> list[str]:
	"""The controlled fields the change touches; a field this site does not have is skipped."""
	master = MASTERS[doc.doctype]
	meta = doc.meta
	touched = [
		f
		for f in master.fields
		if meta.get_field(f) and not same(before.get(f), doc.get(f), meta.get_field(f).fieldtype)
	]
	for table, row_fields in master.tables.items():
		rows_before, rows_now = before.get(table) or [], doc.get(table) or []
		row_meta = frappe.get_meta(meta.get_field(table).options)
		if len(rows_before) != len(rows_now) or any(
			not same(old.get(f), new.get(f), row_meta.get_field(f).fieldtype)
			for old, new in zip(rows_before, rows_now, strict=True)
			for f in row_fields
			if row_meta.get_field(f)
		):
			touched.append(table)
	return touched


def version_master(doc: Document, method: str | None = None) -> None:
	"""validate and before_update_after_submit of a master. The version, its date and the request
	it was changed under are set only here: a change to what a master in use controls names an
	approved change request that lists it, which is then recorded and the version raised."""
	if doc.doctype not in MASTERS or doc.is_new() or setting_up():
		return
	before = doc.get_doc_before_save()
	if before is None:
		return
	raised = doc.flags.version_raised_to == cint(doc.get(VERSION)) == cint(before.get(VERSION)) + 1
	if not raised and (
		cint(doc.get(VERSION)) != cint(before.get(VERSION))
		or not same(doc.get(EFFECTIVE_FROM), before.get(EFFECTIVE_FROM), "Date")
		or not same(doc.get(CHANGED_UNDER), before.get(CHANGED_UNDER), "Link")
	):
		frappe.throw(
			_("The version of {0} is raised only by a change under change control.").format(doc.name),
			frappe.PermissionError,
		)
	if doc.doctype == SPECIFICATION:
		return  # the specification's change control is module 6's, which raises its version
	if doc.doctype == "Item" and doc.get("has_variants") and item_in_use(before):
		# Its variants take on this save's changes from update_variants_under_control.
		doc.flags.dont_update_variants = True
		doc.flags.variant_update_from = before
	touched = changed_fields(doc, before)
	if doc.doctype == "Item" and before.get(APPROVAL_STATUS) != APPROVED and not before.get("has_variants"):
		# An item held before the item master (module 1) is completed once, before QA approves it,
		# as a specification made before control gets its first issue: its type, storage and
		# device data and its specification where never filled (0 is a value, 0 °C), and its
		# inspection switched on, never off. Not a template: it passes its data to variants that
		# may be approved.
		touched = [f for f in touched if not completed_before_approval(doc, before, f)]
	# A request counts only when named with the change, not one left on the record before.
	named = (
		doc.get(CHANGE_REQUEST)
		if not same(doc.get(CHANGE_REQUEST), before.get(CHANGE_REQUEST), "Link")
		else None
	)
	if not touched or not MASTERS[doc.doctype].in_use(before):
		drop_unused_request(doc)
		return
	request = named or following_template(doc, touched)
	if not request:
		frappe.throw(
			_("{0} {1} is in use: name the approved change request that lists it to change it.").format(
				_(doc.doctype), doc.name
			),
			title=_("Change control"),
		)
	if named:
		require_approved_change(request, doc.doctype, doc.name)
	else:  # a variant following its template: the template's request lists the template
		require_approved_change(request, "Item", doc.variant_of)
	if doc.doctype == "BOM" and doc.get("is_default") and not before.get("is_default"):
		# Made the default, it switches the default in force off (ERPNext's manage_default_bom).
		if displaced := displaced_default(doc):
			require_approved_change(request, "BOM", displaced)
			doc.flags.displaces = (displaced, request)
	record_change(doc, request)


# What an item held before the item master may be given once, before QA approves it.
FILLED_BEFORE_APPROVAL = (*ITEM_DEVICE_FIELDS, "quality_inspection_template")
SWITCHED_ON_BEFORE_APPROVAL = ("inspection_required_before_purchase", "inspection_required_before_delivery")


def completed_before_approval(doc: Document, before: Document, field: str) -> bool:
	if field in FILLED_BEFORE_APPROVAL:
		return never_filled(before, field)
	if field in SWITCHED_ON_BEFORE_APPROVAL:
		return not cint(before.get(field)) and bool(cint(doc.get(field)))
	return False


def never_filled(doc: Document, field: str) -> bool:
	"""Blank, or a count still 0 (a number column is never blank; 0 is never a count)."""
	return doc.get(field) in (None, "") or (field in UNSET_WHEN_ZERO and not cint(doc.get(field)))


def drop_unused_request(doc: Document) -> None:
	"""A request named without a controlled change is not kept for a later one, which names its
	own. On a bill of materials or an asset it stays: it is named there for the submit or cancel
	that follows."""
	if doc.get(CHANGE_REQUEST) and not doc.meta.is_submittable:
		doc.set(CHANGE_REQUEST, None)


# What identifies a row rather than what it says: a template's row and its variant's copy differ
# in these, never in content.
ROW_IDENTITY = frozenset((*default_fields, *child_table_fields))


def value_of(doc: Document, df: DocField) -> object:
	if df.fieldtype in ("Table", "Table MultiSelect"):
		return [
			{key: value for key, value in row_content(row).items() if key not in ROW_IDENTITY}
			for row in doc.get(df.fieldname) or []
		]
	return doc.get(df.fieldname)


def unchanged(old: object, new: object, df: DocField) -> bool:
	if df.fieldtype in ("Table", "Table MultiSelect"):
		return old == new
	return same(old, new, df.fieldtype)


def update_variants_under_control(doc: Document, method: str | None = None) -> None:
	"""Item on_update of a template whose variants are in use, after ERPNext's own (told not to
	run). Each variant takes on what changed on the template in this save, and nothing else: a
	field a variant was changed on by itself keeps its value unless the template's request also
	lists that variant. A controlled change reaches the variants under the template's request."""
	before = doc.flags.variant_update_from
	if not before:
		return
	doc.flags.variant_update_from = None
	if frappe.db.get_single_value("Item Variant Settings", "do_not_update_variants"):
		return
	from erpnext.controllers.item_variant import copy_attributes_to_variant

	fields = [
		df
		for df in doc.meta.fields
		if df.fieldtype not in no_value_fields or df.fieldtype in ("Table", "Table MultiSelect")
	]
	changed = {df.fieldname for df in fields if not unchanged(value_of(before, df), value_of(doc, df), df)}
	if not changed:
		return
	request = doc.get(CHANGED_UNDER) if doc.flags.version_raised_to else None
	listed = (
		set(
			frappe.get_all(
				"Change Request Affected Record",
				filters={"parent": request, "parenttype": "Change Request", "reference_doctype": "Item"},
				pluck="reference_name",
			)
		)
		if request
		else set()
	)
	frappe.flags.variants_follow = doc.name
	try:
		for name in frappe.get_all("Item", filters={"variant_of": doc.name}, pluck="name"):
			variant = frappe.get_doc("Item", name)
			own = {df.fieldname: copy.deepcopy(variant.get(df.fieldname)) for df in fields}
			own_values = {df.fieldname: value_of(variant, df) for df in fields}
			copy_attributes_to_variant(doc, variant)
			differs = False
			for df in fields:
				f = df.fieldname
				if unchanged(own_values[f], value_of(variant, df), df):
					continue
				# ERPNext writes each variant's description from the template's and its attributes,
				# so it never equals the template's: it is the template's to change.
				diverged = f != "description" and not unchanged(own_values[f], value_of(before, df), df)
				if f not in changed or (diverged and name not in listed):
					variant.set(f, own[f])  # not this save's change, or the variant's own value
				else:
					differs = True
			if differs:
				variant.save()
	finally:
		frappe.flags.variants_follow = None


def following_template(doc: Document, touched: list[str]) -> str | None:
	"""A variant that only takes on its template's values while the template's controlled change
	is passed to it follows that change, under the template's request."""
	master = MASTERS["Item"]
	# A variant's attribute values are what it is, never its template's (module 1).
	followed_tables = {table: rows for table, rows in master.tables.items() if table != "attributes"}
	if (
		doc.doctype != "Item"
		or not doc.get("variant_of")
		or frappe.flags.variants_follow != doc.variant_of
		or not set(touched) <= set(master.fields) | set(followed_tables)
	):
		return None
	template = frappe.get_doc("Item", doc.variant_of)
	if not template.get(CHANGED_UNDER):
		return None
	for f in touched:
		if f in followed_tables:
			row_fields = followed_tables[f]
			ours = [[row.get(field) for field in row_fields] for row in doc.get(f) or []]
			theirs = [[row.get(field) for field in row_fields] for row in template.get(f) or []]
			if ours != theirs:
				return None
		elif not same(doc.get(f), template.get(f), doc.meta.get_field(f).fieldtype):
			return None
	return template.get(CHANGED_UNDER)


def record_change(doc: Document, request: str) -> None:
	"""The change was made under `request`: recorded, the request named afresh for the next one,
	and the version raised."""
	doc.set(CHANGED_UNDER, request)
	doc.set(CHANGE_REQUEST, None)
	raise_version(doc)


def raise_version(doc: Document) -> None:
	"""The changed master is a new version, in effect from today."""
	doc.set(VERSION, cint(doc.get_doc_before_save().get(VERSION)) + 1)
	doc.set(EFFECTIVE_FROM, today())
	# What the version was raised to in this save; a later save compares it with its own before.
	doc.flags.version_raised_to = cint(doc.get(VERSION))


# --- Bills of materials ----------------------------------------------------------------------


def displaced_default(doc: Document) -> str | None:
	# ERPNext switches the old default off only for an active new default (bom.py).
	if not (doc.get("is_default") and doc.get("is_active")):
		return None
	return frappe.db.get_value(
		"BOM", {"item": doc.item, "is_default": 1, "docstatus": 1, "name": ["!=", doc.name]}
	)


def guard_new_default_bom(doc: Document, method: str | None = None) -> None:
	"""BOM before_submit: a new default replaces the item's default in force, which ERPNext
	switches off straight in the database (manage_default_bom); that is a change of the BOM in
	use, made under an approved change request that lists it."""
	displaced = displaced_default(doc)
	if not displaced or setting_up():
		return
	request = doc.get(CHANGE_REQUEST)
	if not request:
		frappe.throw(
			_(
				"{0} is the default bill of materials of {1}: name the approved change request that lists it to replace it."
			).format(displaced, doc.item),
			title=_("Change control"),
		)
	require_approved_change(request, "BOM", displaced)
	doc.flags.displaces = (displaced, request)
	doc.set(CHANGED_UNDER, request)
	doc.set(CHANGE_REQUEST, None)


def record_displaced_default(doc: Document, method: str | None = None) -> None:
	"""BOM on_submit, after ERPNext switched the displaced default off: its new version and the
	change, in the audit trail."""
	if not doc.flags.displaces:
		return
	displaced, request = doc.flags.displaces
	old = frappe.db.get_value("BOM", displaced, [VERSION, EFFECTIVE_FROM, CHANGED_UNDER], as_dict=True)
	new = {VERSION: cint(old.get(VERSION)) + 1, EFFECTIVE_FROM: today(), CHANGED_UNDER: request}
	# A documented system change: ERPNext's own update bypassed the record; this records it.
	frappe.db.set_value("BOM", displaced, new, update_modified=False)
	changes = {"changed": [["is_default", 1, 0], *([f, old.get(f), new[f]] for f in new)]}
	record(
		"BOM",
		displaced,
		"Updated",
		json.loads(frappe.as_json(changes)),  # stored values as plain JSON, as the audit trail keeps them
		reason=_("Replaced as the default bill of materials by {0}").format(doc.name),
	)


def guard_cancel(doc: Document, method: str | None = None) -> None:
	"""before_cancel of a submitted master (bill of materials, equipment): cancelling takes it out
	of force (ERPNext switches a BOM off), a change of a master in use made under an approved
	change request that lists it."""
	if setting_up():
		return
	request = doc.get(CHANGE_REQUEST)
	if not request:
		frappe.throw(
			_("{0} is in use: name the approved change request that lists it to cancel it.").format(doc.name),
			title=_("Change control"),
		)
	require_approved_change(request, doc.doctype, doc.name)
	doc.set(CHANGED_UNDER, request)
	doc.set(CHANGE_REQUEST, None)


def let_change_requests_not_block_cancel(doc: Document, method: str | None = None) -> None:
	"""on_cancel of a submitted master, after the record's own (ERPNext's Asset sets its own list
	here): a change request names the record it changes and does not depend on it, so it does not
	hold back the cancel it authorises in Frappe's link check, which runs next."""
	doc.ignore_linked_doctypes = (*(doc.get("ignore_linked_doctypes") or ()), "Change Request")


def refuse_bom_replace(doc: Document, method: str | None = None) -> None:
	"""BOM Update Log validate: ERPNext's BOM Update Tool would replace a bill of materials inside
	approved ones straight in the database, past change control and the audit trail. A changed bill
	of materials is a new one under an approved change request (decided 2026-10-09); updating
	costs stays allowed."""
	if doc.get("update_type") == "Replace BOM":
		frappe.throw(
			_(
				"A bill of materials is not replaced inside approved ones; make the changed one as a new bill of materials under an approved change request."
			),
			frappe.PermissionError,
		)


# --- Renaming --------------------------------------------------------------------------------


def refuse_rename_in_use(doc: Document, method: str, old: str, new: str, merge: bool = False) -> None:
	"""before_rename: a master in use keeps its name, and is never merged into another, which
	would move its history; a parameter read by a specification in use keeps its name."""
	if setting_up():
		return
	if doc.doctype == "Quality Inspection Parameter":
		read_by = frappe.get_all(
			"Item Quality Inspection Parameter",
			filters={"parenttype": SPECIFICATION, "specification": old},
			pluck="parent",
		)
		in_use = bool(read_by) and bool(
			frappe.db.exists(
				"Quality Inspection", {"quality_inspection_template": ["in", read_by], "docstatus": 1}
			)
		)
	elif doc.doctype == SPECIFICATION:
		from meddevice_suite.master_data.specification import in_use as specification_in_use

		in_use = specification_in_use(old)
	elif doc.doctype in MASTERS:
		in_use = MASTERS[doc.doctype].in_use(doc)
	else:
		return
	if in_use:
		frappe.throw(
			_("{0} {1} is in use: it keeps its name and is never merged into another.").format(
				_(doc.doctype), old
			),
			frappe.PermissionError,
		)
