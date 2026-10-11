"""Item master (module 1; URS-MST-003 to URS-MST-006; DFD D3 Item / BOM / Routing Master;
ISO 13485 4.2.4, 7.5.1, 7.5.8).

An item is set up in draft and put to use only once QA has approved it with an electronic
signature: until then every record that would use it (an order, a receipt, a stock entry, a
lot, a bill of materials, a work order, an inspection, a delivery, a bundle …) is refused for
it, whichever way the record is made. The signed act is the only approval: the approval
fields are set by it alone. From approval the item's controlled fields change only under an
approved change request (module MST-001 counts an approved item as in use).

What QA checks before approving is the item's type and what the type demands. A raw
material, packaging item, sub-assembly or finished good is lot-tracked, with a shelf life
and a storage condition. A purchased material or packaging item requires incoming
inspection and has its incoming specification (module 6). A finished good has its final
specification and its regulatory and label data: sterile or not, device class, pack sizes
and the UDI device identifier, a GTIN no other item carries, and its lifetime (plan D-16). A template item (one with
variants) is never approved itself: each variant is approved on its own, with its own
identifier, and a template's controlled changes reach its variants in use (MST-001).

Items a site held before this module stay in draft: nothing is approved by migration. The
types follow the client's master-data template of 2026-10-09, with Equipment added for the
items that stand behind the equipment register's assets (module 8).
"""

import re

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.model.document import Document
from frappe.model.meta import Meta
from frappe.query_builder import DocType
from frappe.utils import cint, flt, now_datetime

from meddevice_suite.compliance.audit import same
from meddevice_suite.compliance.esign import sign

ITEM_TYPE = "mds_item_type"
APPROVAL_STATUS, APPROVED_BY, APPROVED_AT = "mds_approval_status", "mds_approved_by", "mds_approved_at"
DRAFT, APPROVED = "Draft", "Approved"
STERILE, DEVICE_CLASS, STORAGE_CONDITION = "mds_sterility", "mds_device_class", "mds_storage_condition"
STERILE_OPTIONS = ("Yes", "No")  # stated, never defaulted: "not stated" is not "not sterile"
MIN_TEMPERATURE, MAX_TEMPERATURE = "mds_min_temperature", "mds_max_temperature"
UNITS_PER_BOX, BOXES_PER_CARTON, UDI_DI = "mds_units_per_box", "mds_boxes_per_carton", "mds_udi_di"
# The GTIN as 14 digits (GS1's form for storing it): one GTIN written as GTIN-12, -13 or -14 is the
# same identifier, and the database refuses a second item with it, however close the two saves.
GTIN_KEY = "mds_gtin14"
# Plan D-16 (decided 2026-10-11): the device's lifetime, 5 years unless QA states another. Records
# are kept for the lifetime plus two years at least (ISO 13485 4.2.5), so it is bounded by retention.
DEVICE_LIFETIME, DEFAULT_LIFETIME_YEARS, RETAINED_BEYOND_LIFETIME_YEARS = "mds_device_lifetime_years", 5, 2

RAW_MATERIAL, PACKAGING, SUB_ASSEMBLY = "Raw Material", "Packaging", "Sub-assembly (WIP)"
FINISHED_GOOD, CONSUMABLE, SPARE_PART, EQUIPMENT = "Finished Good", "Consumable", "Spare Part", "Equipment"
ITEM_TYPES = (RAW_MATERIAL, PACKAGING, SUB_ASSEMBLY, FINISHED_GOOD, CONSUMABLE, SPARE_PART, EQUIPMENT)
LOT_TRACKED_TYPES = (RAW_MATERIAL, PACKAGING, SUB_ASSEMBLY, FINISHED_GOOD)
PURCHASED_TYPES = (RAW_MATERIAL, PACKAGING)  # inspected when they arrive (DFD 3.4)
DEVICE_CLASSES = ("A", "B", "C", "D", "Not a device")
GTIN_LENGTHS = (8, 12, 13, 14)
INCOMING, FINAL = "Incoming", "Final"
QA_ROLE = "Quality Manager"

APPROVAL_FIELDS = (APPROVAL_STATUS, APPROVED_BY, APPROVED_AT)
# What a product, a label or a regulatory record depends on: controlled from approval (MST-001).
CONTROLLED_FIELDS = (
	ITEM_TYPE,
	STERILE,
	DEVICE_CLASS,
	STORAGE_CONDITION,
	MIN_TEMPERATURE,
	MAX_TEMPERATURE,
	UNITS_PER_BOX,
	BOXES_PER_CARTON,
	UDI_DI,
	DEVICE_LIFETIME,
)

# Record types that name an item without putting it to use: the item's own setup, its prices
# and tax rules, stock summaries, and this app's records made before approval (a specification,
# a supplier's approval for the item, a change request about it, its risk analysis). Every other
# record type that names an item, in this ERPNext release or a later one, is refused a draft item.
SETUP_TYPES = frozenset(
	{
		"Item",
		"Item Price",
		"Item Lead Time",
		"Item Manufacturer",
		"Pricing Rule",
		"Promotional Scheme",
		"Tax Rule",
		"Subscription Plan",
		"Bin",
		"Stock Closing Balance",
		"Quick Stock Balance",
		"Import Supplier Invoice",
		"Quality Inspection Template",
		"Supplier Approval",
		"Change Request",
		# Module 39: a product's risks are analysed before QA approves it.
		"Risk Management File",
		"Risk Assessment",
	}
)

CUSTOM_FIELDS = {
	"Item": [
		{
			"fieldname": ITEM_TYPE,
			"label": "Item Type",
			"fieldtype": "Select",
			"options": "\n" + "\n".join(ITEM_TYPES),  # blank first: Frappe fills a select with its first
			"insert_after": "item_group",
			"reqd": 1,
			"in_standard_filter": 1,
			"description": "What the item is in the process; decides what QA checks before approving it.",
		},
		{
			"fieldname": APPROVAL_STATUS,
			"label": "QA Approval",
			"fieldtype": "Select",
			"options": f"{DRAFT}\n{APPROVED}",
			"default": DRAFT,
			"insert_after": "disabled",
			"read_only": 1,
			"no_copy": 1,
			"in_list_view": 1,
			"in_standard_filter": 1,
			"description": "Set only by QA's signed approval; a draft item is used in no record.",
		},
		{
			"fieldname": APPROVED_BY,
			"label": "Approved By",
			"fieldtype": "Link",
			"options": "User",
			"insert_after": APPROVAL_STATUS,
			"read_only": 1,
			"no_copy": 1,
		},
		{
			"fieldname": APPROVED_AT,
			"label": "Approved At",
			"fieldtype": "Datetime",
			"insert_after": APPROVED_BY,
			"read_only": 1,
			"no_copy": 1,
		},
		{
			"fieldname": "mds_device_section",
			"label": "Storage and Device Data",
			"fieldtype": "Section Break",
			"insert_after": "description",
			"collapsible": 0,
		},
		{
			"fieldname": STORAGE_CONDITION,
			"label": "Storage Condition",
			"fieldtype": "Data",
			"insert_after": "mds_device_section",
			"description": "As on the label or the specification, e.g. below 30 °C, dry, away from sunlight.",
		},
		{
			"fieldname": MIN_TEMPERATURE,
			"label": "Min Temperature (°C)",
			# Text, not a number: a number column is never blank (Frappe stores 0), and 0 °C is a
			# temperature; checked as a number on save.
			"fieldtype": "Data",
			"insert_after": STORAGE_CONDITION,
		},
		{
			"fieldname": MAX_TEMPERATURE,
			"label": "Max Temperature (°C)",
			# Text, not a number: a number column is never blank (Frappe stores 0), and 0 °C is a
			# temperature; checked as a number on save.
			"fieldtype": "Data",
			"insert_after": MIN_TEMPERATURE,
		},
		{
			"fieldname": "mds_device_column",
			"fieldtype": "Column Break",
			"insert_after": MAX_TEMPERATURE,
		},
		{
			"fieldname": STERILE,
			"label": "Supplied Sterile",
			"fieldtype": "Select",
			"options": "\n" + "\n".join(STERILE_OPTIONS),
			"insert_after": "mds_device_column",
			"depends_on": f'eval:doc.{ITEM_TYPE}=="{FINISHED_GOOD}"',
		},
		{
			"fieldname": DEVICE_CLASS,
			"label": "Device Class (MDR 2017)",
			"fieldtype": "Select",
			"options": "\n" + "\n".join(DEVICE_CLASSES),
			"insert_after": STERILE,
			"depends_on": f'eval:doc.{ITEM_TYPE}=="{FINISHED_GOOD}"',
		},
		{
			"fieldname": UNITS_PER_BOX,
			"label": "Units per Box",
			"fieldtype": "Int",
			"insert_after": DEVICE_CLASS,
			"depends_on": f'eval:doc.{ITEM_TYPE}=="{FINISHED_GOOD}"',
		},
		{
			"fieldname": BOXES_PER_CARTON,
			"label": "Boxes per Carton",
			"fieldtype": "Int",
			"insert_after": UNITS_PER_BOX,
			"depends_on": f'eval:doc.{ITEM_TYPE}=="{FINISHED_GOOD}"',
		},
		{
			"fieldname": UDI_DI,
			"label": "UDI Device Identifier (GTIN)",
			"fieldtype": "Data",
			"insert_after": BOXES_PER_CARTON,
			"no_copy": 1,
			"search_index": 1,
			"depends_on": f'eval:doc.{ITEM_TYPE}=="{FINISHED_GOOD}"',
			"description": "The GS1 GTIN of this item; a variant carries its own.",
		},
		{
			"fieldname": GTIN_KEY,
			"label": "GTIN (14 digits)",
			"fieldtype": "Data",
			"insert_after": UDI_DI,
			"read_only": 1,
			"hidden": 1,
			"no_copy": 1,
			"unique": 1,
		},
		{
			"fieldname": DEVICE_LIFETIME,
			"label": "Device lifetime (years)",
			"fieldtype": "Int",
			"default": str(DEFAULT_LIFETIME_YEARS),
			"non_negative": 1,
			"insert_after": GTIN_KEY,
			"depends_on": f'eval:doc.{ITEM_TYPE}=="{FINISHED_GOOD}"',
			"description": "How long the device is expected to stay safe and effective in use; its records are kept at least this long plus two years.",
		},
	],
}


# Counts that are 0 until stated (a number column is never blank): 0 is never a count.
UNSET_WHEN_ZERO = (UNITS_PER_BOX, BOXES_PER_CARTON)

# A variant takes these from its template, and follows the template's controlled changes (MST-001);
# its identifier and its approval are its own.
VARIANT_FIELDS = tuple(field for field in CONTROLLED_FIELDS if field != UDI_DI)


def apply_item_master_fields() -> None:
	create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)
	settings = frappe.get_single("Item Variant Settings")
	present = {row.field_name for row in settings.fields}
	if missing := [field for field in VARIANT_FIELDS if field not in present]:
		for field in missing:
			settings.append("fields", {"field_name": field})
		settings.save(ignore_permissions=True)  # setup code
	# An item made before this module is in draft, like a new one: nothing approves it but QA.
	table = DocType("Item")
	frappe.qb.update(table).set(table[APPROVAL_STATUS], DRAFT).where(
		table[APPROVAL_STATUS].isnull() | (table[APPROVAL_STATUS] == "")
	).run()
	# The 14-digit key of an identifier entered before the key was kept.
	for name, udi in frappe.get_all(
		"Item",
		filters={UDI_DI: ["is", "set"], GTIN_KEY: ["is", "not set"]},
		fields=["name", UDI_DI],
		as_list=True,
	):
		# An identifier that is not a GTIN, or one another item holds, keeps no key: the item's next
		# save is refused with the reason, and QA corrects it.
		if valid_gtin(udi) and not frappe.db.exists("Item", {GTIN_KEY: gtin_key(udi)}):
			frappe.db.set_value("Item", name, GTIN_KEY, gtin_key(udi), update_modified=False)  # setup code


def configured() -> bool:
	"""The item master's fields exist: before apply_configuration first runs on a site (its
	install, or the migrate that brings this module), nothing here applies."""
	return bool(frappe.get_meta("Item").get_field(APPROVAL_STATUS))


def is_qa(user: str | None = None) -> bool:
	return QA_ROLE in frappe.get_roles(user or frappe.session.user)


def approved(item: str | Document) -> bool:
	status = (
		item.get(APPROVAL_STATUS)
		if isinstance(item, Document)
		else frappe.db.get_value("Item", item, APPROVAL_STATUS)
	)
	return status == APPROVED


# --- What approval needs ------------------------------------------------------------------------


def valid_gtin(value: str) -> bool:
	"""A GTIN-8, -12, -13 or -14: digits whose last one is the GS1 check digit."""
	digits = (value or "").strip()
	# ASCII digits only (str.isdigit takes other scripts' digits too); all zeros is no identifier.
	if not re.fullmatch(r"[0-9]+", digits) or len(digits) not in GTIN_LENGTHS or not digits.strip("0"):
		return False
	body, check = digits[:-1], int(digits[-1])
	total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
	return (10 - total % 10) % 10 == check


def gtin_key(udi: str) -> str:
	return udi.zfill(14)


def other_item_with(udi: str, item: str) -> str | None:
	return frappe.db.get_value("Item", {GTIN_KEY: gtin_key(udi), "name": ["!=", item]})


def specification_of(item: str, stage: str) -> str | None:
	# Module 6 registers specifications; imported here so the two modules stay in order.
	from meddevice_suite.master_data.specification import specification_for

	return specification_for(item, stage)


def missing_for_approval(doc: Document) -> list[str]:
	"""What the item still lacks for QA to approve it, in words the form shows."""
	item_type = doc.get(ITEM_TYPE)
	missing = []
	if item_type not in ITEM_TYPES:
		return [_("its type")]
	if item_type in LOT_TRACKED_TYPES:
		if not doc.get("is_stock_item"):
			missing.append(_("stock item (it is kept in stock)"))
		if not doc.get("has_batch_no"):
			missing.append(_("lot tracking (Has Batch No)"))
		if not doc.get("has_expiry_date") or cint(doc.get("shelf_life_in_days")) <= 0:
			missing.append(_("a shelf life with an expiry date on every lot"))
		if not (doc.get(STORAGE_CONDITION) or "").strip():
			missing.append(_("its storage condition"))
	if item_type in PURCHASED_TYPES:
		if not doc.get("inspection_required_before_purchase"):
			missing.append(_("incoming inspection (Inspection Required before Purchase)"))
		if not specification_of(doc.name, INCOMING):
			missing.append(_("its incoming specification (module 6)"))
	if item_type == FINISHED_GOOD:
		if doc.get(STERILE) not in STERILE_OPTIONS:
			missing.append(_("whether it is supplied sterile"))
		if not doc.get(DEVICE_CLASS):
			missing.append(_("its device class"))
		if cint(doc.get(UNITS_PER_BOX)) <= 0 or cint(doc.get(BOXES_PER_CARTON)) <= 0:
			missing.append(_("its pack sizes (units per box, boxes per carton)"))
		if not valid_gtin(doc.get(UDI_DI)):
			missing.append(_("its UDI device identifier (a GTIN)"))
		if doc.meta.get_field(DEVICE_LIFETIME) and cint(doc.get(DEVICE_LIFETIME)) <= 0:
			missing.append(_("its device lifetime"))
		if not specification_of(doc.name, FINAL):
			missing.append(_("its final specification (module 6)"))
	return missing


# --- The item's own rules (Item validate) -------------------------------------------------------


def guard_item(doc: Document, method: str | None = None) -> None:
	"""Item validate: the approval fields are the signed act's alone; an identifier is a GTIN no
	other item carries; pack sizes are counts; an approved item keeps what its approval needed."""
	if not configured():
		return  # the fields arrive with apply_configuration
	before = None if doc.is_new() else doc.get_doc_before_save()
	if not doc.flags.item_approval:
		if before is None:  # new, copied or made as a variant: in draft
			doc.set(APPROVAL_STATUS, DRAFT)
			doc.set(APPROVED_BY, None)
			doc.set(APPROVED_AT, None)
		else:
			for field in APPROVAL_FIELDS:
				if not same(before.get(field), doc.get(field), doc.meta.get_field(field).fieldtype):
					frappe.throw(
						_("The approval of {0} is given only by QA's signed approval.").format(doc.name),
						frappe.PermissionError,
					)
	if doc.get(ITEM_TYPE) not in ITEM_TYPES:
		frappe.throw(_("State the item type of {0}.").format(doc.name), frappe.MandatoryError)
	udi = (doc.get(UDI_DI) or "").strip()
	doc.set(UDI_DI, udi or None)
	doc.set(GTIN_KEY, gtin_key(udi) if udi else None)  # NULL, not blank: the unique index allows many
	if udi:
		if not valid_gtin(udi):
			frappe.throw(
				_("{0} is not a GTIN: 8, 12, 13 or 14 digits ending in the GS1 check digit.").format(udi)
			)
		if other := other_item_with(udi, doc.name):
			frappe.throw(_("GTIN {0} identifies {1} already; each item carries its own.").format(udi, other))
	for field in (UNITS_PER_BOX, BOXES_PER_CARTON):
		if doc.get(field) is not None and cint(doc.get(field)) < 0:
			frappe.throw(_("{0} is a count.").format(_(doc.meta.get_label(field))))
	temperatures = {}
	for field in (MIN_TEMPERATURE, MAX_TEMPERATURE):
		value = (doc.get(field) or "").strip()
		doc.set(field, value or None)
		if value:
			try:
				temperatures[field] = float(value)
			except ValueError:
				frappe.throw(
					_("{0} is a temperature in °C, e.g. 2 or -20.").format(_(doc.meta.get_label(field)))
				)
	if len(temperatures) == 2 and temperatures[MIN_TEMPERATURE] > temperatures[MAX_TEMPERATURE]:
		frappe.throw(_("The minimum storage temperature is not above the maximum."))
	check_lifetime(doc)
	if not approved(doc) and doc.get("reorder_levels"):
		# ERPNext's automatic reorder makes one batch of requests for every item; a draft item in it
		# would stop the batch for the approved ones too.
		frappe.throw(_("Reorder levels are set once QA has approved {0}.").format(doc.name))
	if before is not None and approved(before):
		if before.get(ITEM_TYPE) != doc.get(ITEM_TYPE):
			# What QA approved was checked for its type; another type is another item.
			frappe.throw(
				_("The type of an approved item does not change; set up a new item of type {0}.").format(
					_(doc.get(ITEM_TYPE))
				),
				title=_("Approved item"),
			)
		lost = [what for what in missing_for_approval(doc) if what not in missing_for_approval(before)]
		if lost:
			frappe.throw(
				_("{0} is approved and keeps what its approval needed; it cannot lose {1}.").format(
					doc.name, "; ".join(lost)
				),
				title=_("Approved item"),
			)


def longest_lifetime() -> int:
	"""The longest lifetime the records' retention covers: kept the lifetime plus two years at least
	(ISO 13485 4.2.5), and the device's records are kept as long as the item's."""
	from meddevice_suite.compliance.retention import retention_years  # compliance builds no item rule

	return cint(retention_years("Item")) - RETAINED_BEYOND_LIFETIME_YEARS


def check_lifetime(doc: Document) -> None:
	"""A finished good's lifetime is a number of years its records' retention covers (plan D-16)."""
	if doc.get(ITEM_TYPE) != FINISHED_GOOD or not doc.meta.get_field(DEVICE_LIFETIME):
		return
	years, longest = cint(doc.get(DEVICE_LIFETIME)), longest_lifetime()
	if not 1 <= years <= longest:
		frappe.throw(
			_(
				"The device lifetime is 1 to {0} years: its records are kept {1} years, at least the lifetime plus two."
			).format(longest, longest + RETAINED_BEYOND_LIFETIME_YEARS)
		)


def item_onload(doc: Document, method: str | None = None) -> None:
	"""Item onload: QA's approval, or what still stands in its way, for the form to show."""
	doc.set_onload(
		"item_approval",
		{
			"status": doc.get(APPROVAL_STATUS) or DRAFT,
			"approved_by_name": frappe.db.get_value("User", doc.get(APPROVED_BY), "full_name")
			if doc.get(APPROVED_BY)
			else None,
			"approved_at": doc.get(APPROVED_AT),
			"missing": [] if approved(doc) or doc.get("has_variants") else missing_for_approval(doc),
		},
	)


# --- The signed approval ------------------------------------------------------------------------


def signed_content(doc: Document) -> dict:
	"""What QA's signature binds (compliance.esign.SIGNED_CONTENT): the item, its approval and what
	change control keeps, not what ERPNext keeps up to date with use (last purchase rate, projected
	quantity) or free fields (description, reorder levels). A controlled change after approval is
	then shown as a change since signing, made under its change request."""
	from meddevice_suite.master_data.versioning import EFFECTIVE_FROM, MASTERS, VERSION

	master = MASTERS["Item"]
	fields = (
		"item_code",
		"variant_of",
		"has_variants",
		*master.fields,
		*APPROVAL_FIELDS,
		VERSION,
		EFFECTIVE_FROM,
	)
	content = {field: doc.get(field) for field in fields if field != DEVICE_LIFETIME}
	if cint(doc.get(DEVICE_LIFETIME)) not in (0, DEFAULT_LIFETIME_YEARS):
		# Added with plan D-16: an approval signed before it, at the default lifetime, still verifies;
		# a lifetime QA states otherwise is bound (and raised the version, which is bound too).
		content[DEVICE_LIFETIME] = cint(doc.get(DEVICE_LIFETIME))
	for table, row_fields in master.tables.items():  # a variant's attributes, units, barcodes
		content[table] = [{f: row.get(f) for f in row_fields} for row in doc.get(table) or []]
	return content


def approve(doc: Document, password: str, reason: str | None = None) -> None:
	"""QA's signed approval of an item (registered in compliance.signing.SIGNING_ACTIONS)."""
	frappe.db.get_value("Item", doc.name, "name", for_update=True)  # one approval at a time
	doc = frappe.get_doc("Item", doc.name)
	doc.check_permission("read")
	require(is_qa(), _("Only QA approves an item."), frappe.PermissionError)
	require(not approved(doc), _("{0} is approved already.").format(doc.name))
	require(not doc.get("disabled"), _("{0} is disabled; a disabled item is not approved.").format(doc.name))
	require(reason, _("Say why the item is approved."))
	require(
		not doc.get("has_variants"),
		_("A template is not approved; each of its variants is approved on its own."),
	)
	missing = missing_for_approval(doc)
	require(not missing, _("{0} cannot be approved yet; it lacks {1}.").format(doc.name, "; ".join(missing)))
	doc.set(APPROVAL_STATUS, APPROVED)
	doc.set(APPROVED_BY, frappe.session.user)
	doc.set(APPROVED_AT, now_datetime())
	sign(doc, "Approved", "Approve", password, reason)
	doc.flags.item_approval = True
	doc.save()


def require(condition: object, message: str, exception: type[Exception] = frappe.ValidationError) -> None:
	if not condition:
		frappe.throw(message, exception)


# --- Lots and serial numbers made in bulk ------------------------------------------------------
# ERPNext's upload of lots and serial numbers from a file, and its making of serial numbers, insert
# them straight into the database, past every record's rules; they are refused here for an item
# QA has not approved (hooks.override_whitelisted_methods).

SERIAL_AND_BATCH = "erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle"


def refuse_unless_approved(item_code: str) -> None:
	if configured() and not approved(item_code):
		frappe.throw(
			_("Not yet approved by QA, so no lots or serial numbers are made for {0}.").format(item_code),
			title=_("Item not approved"),
		)


@frappe.whitelist()
def upload_csv_file(item_code: str, file_path: str) -> dict:
	refuse_unless_approved(item_code)
	return frappe.get_attr(f"{SERIAL_AND_BATCH}.upload_csv_file")(item_code, file_path)


@frappe.whitelist()
def create_serial_nos(item_code: str, serial_nos: str | list) -> object:
	refuse_unless_approved(item_code)
	return frappe.get_attr(f"{SERIAL_AND_BATCH}.create_serial_nos")(item_code, serial_nos)


# --- Reorder levels left from before this module -----------------------------------------------


def guard_auto_reorder(doc: Document, method: str | None = None) -> None:
	"""Stock Settings validate: ERPNext's automatic reorder makes one request for all items and
	drops it on any error; it is switched on only once no draft item has reorder levels (items held
	before this module keep theirs until QA approves them)."""
	if not doc.get("auto_indent") or not configured():
		return
	drafts = frappe.get_all(
		"Item",
		filters={
			APPROVAL_STATUS: ["!=", APPROVED],
			"name": ["in", frappe.get_all("Item Reorder", pluck="parent")],
		},
		pluck="name",
		limit=20,
	)
	if drafts:
		frappe.throw(
			_("Automatic reorder waits until QA has approved the items with reorder levels: {0}.").format(
				", ".join(drafts)
			)
		)


# --- Before approval, used nowhere (every record's validate) ------------------------------------


# Corrections of what was recorded before, never a new use: a stock posting being reversed, a
# valuation reposted. Refusing them for an item in draft (one held before this module) would leave
# the record uncorrectable.
CORRECTIONS = frozenset({"Repost Item Valuation"})


def item_fields_of(meta: Meta) -> tuple[list[str], dict[str, list[str]]]:
	"""The fields of a record type that name an item: its own, and those of each of its tables;
	worked out once per request for each record type."""
	cache = getattr(frappe.local, "mds_item_fields", None)
	if cache is None:
		cache = frappe.local.mds_item_fields = {}
	key = (meta.name, str(meta.get("modified")))  # a field added later gives a new definition
	if key not in cache:
		cache[key] = item_fields_from(meta)
	return cache[key]


def item_fields_from(meta: Meta) -> tuple[list[str], dict[str, list[str]]]:
	own = [df.fieldname for df in meta.fields if df.fieldtype == "Link" and df.options == "Item"]
	tables = {}
	for df in meta.get_table_fields():
		row_fields = [
			f.fieldname
			for f in frappe.get_meta(df.options).fields
			if f.fieldtype == "Link" and f.options == "Item"
		]
		if row_fields:
			tables[df.fieldname] = row_fields
	return own, tables


def items_named_by(doc: Document) -> set[str]:
	own, tables = item_fields_of(doc.meta)
	codes = {doc.get(field) for field in own}
	for table, row_fields in tables.items():
		for row in doc.get(table) or []:
			codes.update(row.get(field) for field in row_fields)
	codes.discard(None)
	codes.discard("")
	return codes


def refuse_unapproved(doc: Document, method: str | None = None) -> None:
	"""validate of every record type: a record that puts items to use names only approved ones.
	Returns at once for the item's own setup records, corrections and record types naming no item.
	before_update_after_submit too, for the items a submitted record takes on then (ERPNext's
	Update Items adds rows to a submitted order, past its validate)."""
	if doc.doctype in SETUP_TYPES or doc.doctype in CORRECTIONS or not configured():
		return
	if doc.meta.istable and doc.get("parenttype") in (SETUP_TYPES | CORRECTIONS):
		return
	if doc.doctype == "Stock Ledger Entry" and doc.get("is_cancelled"):
		return  # the reversal of a posting made before
	# A row runs its own hooks only when it is written by itself, as Update Items inserts a row
	# into a submitted order before saving the order (whose earlier state then holds the row).
	codes = items_named_by(doc)
	if method == "before_update_after_submit" and (before := doc.get_doc_before_save()):
		codes -= items_named_by(before)
	if not codes:
		return
	rows = frappe.get_all("Item", filters={"name": ["in", list(codes)]}, fields=["name", APPROVAL_STATUS])
	unapproved = sorted(row.name for row in rows if row.get(APPROVAL_STATUS) != APPROVED)
	if unapproved:
		frappe.throw(
			_("Not yet approved by QA, so not used in {0}: {1}. QA approves an item on its form.").format(
				_(doc.doctype), ", ".join(unapproved)
			),
			title=_("Item not approved"),
		)
