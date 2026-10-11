"""Masters for the browser test of the standard flow (URS-PLT-007), on a test site only.

The browser walks the standard flow through the real screens, signed in as each job
(cypress/integration/standard_flow.js), as the server test does through code
(pilot/test_standard_flow.py). The test site has the fixed test company of
qa/site_setup.py but none of the pilot's demo data, and the pilot's loader never runs
there; these helpers make what the journey needs instead, every record named "UIJ": a
product and its raw materials, batch-tracked with a shelf life, a supplier, a customer, the
quarantine store, a short routing with its workstations, the product's default BOM and its
device master record (module 31), drafted by the Production Supervisor and approved by QA. Each
item is approved by the run's QA Manager user with a signature (module 1), with the password
the browser was given for the run. The site's defaults (company, price lists, stores) are left
as they are.

Every run starts from the same stock. A run that was interrupted can leave lots of the
journey's items in a store, and they would be picked before the run's own, so they are
written off first. Then a lot of a raw material and a lot of the product are put in store
that expire later than anything the run receives or makes: the browser shows that the lot
that expires first is taken first, although another lot was in store before it
(URS-STK-002).

Only the Administrator who runs the tests prepares the journey. Any signed-in user may ask
for the lots of a stock transaction they may read, which the browser checks after each step.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager

import frappe
from frappe import _
from frappe.utils import add_days, today
from frappe.utils.password import update_password

from meddevice_suite.lot_control.lot_status import APPROVED, REJECTED, set_status, stock_by_lot
from meddevice_suite.master_data import item_master
from meddevice_suite.pilot.demo import (
	BOM_QUANTITY,
	ensure,
	ensure_bom,
	ensure_item,
	ensure_licence,
	ensure_price,
)
from meddevice_suite.pilot.inspection import (
	calibrate_instrument,
	ensure_inspection_masters,
	ensure_inspection_template,
)
from meddevice_suite.qa.site_setup import TEST_COMPANY
from meddevice_suite.qa.ui_users import email_for, ensure_test_site

COMPANY = TEST_COMPANY["company_name"]
CUSTOMER = "UIJ City Hospital"
SUPPLIER = "UIJ Medical Components"
QUARANTINE = "Quarantine"  # the pilot's name for it (pilot/demo.py EXTRA_STORES)

PRODUCT = "UIJ-FC-2W-SIL-16"
# name, HSN, selling rate, shelf life in days
PRODUCT_DETAILS = ("Foley catheter, 2-way, silicone, 16 Fr (browser test)", "90183910", 98, 1825)
# code -> (name, unit, HSN, shelf life in days, buying rate, quantity per BOM batch of 100 pieces)
RAW_MATERIALS: dict[str, tuple[str, str, str, int, float, float]] = {
	"UIJ-RM-SILICONE": ("Medical-grade silicone compound (browser test)", "Kg", "391000", 730, 1450, 1.4),
	"UIJ-RM-CONNECTOR": ("Funnel connector with valve (browser test)", "Nos", "39269099", 1825, 3.5, 100),
	"UIJ-RM-POUCH": ("Peel pouch, sterile barrier (browser test)", "Nos", "39232990", 1825, 1.8, 100),
}
ROUTING = "UIJ Catheter Line"
LABELLING_TITLE = "UIJ labelling and instructions for use"
# (operation, workstation, minutes per batch of 100), in process order
OPERATIONS = (
	("UIJ Moulding", "UIJ Moulding Station", 20),
	("UIJ Leak Test", "UIJ Leak Test Station", 10),
	("UIJ Packing", "UIJ Packing Line", 10),
)
# One production batch: the run buys what it needs and delivers all it makes.
QUANTITY = BOM_QUANTITY

# The lots already in store when the run starts, and how long after today they expire:
# later than every shelf life above.
LATER_EXPIRY_DAYS = 3650
LOTS_IN_STORE = {"UIJ-RM-SILICONE": ("Stores", 1, 1450), PRODUCT: ("Finished Goods", 10, 60)}

STOCK_TRANSACTIONS = ("Purchase Receipt", "Stock Entry", "Delivery Note")


def journey_items() -> list[str]:
	return [PRODUCT, *RAW_MATERIALS]


def store(name: str) -> str:
	return f"{name} - {frappe.get_cached_value('Company', COMPANY, 'abbr')}"  # as ERPNext names them


@frappe.whitelist(methods=["POST"])
def prepare_journey(password: str | None = None) -> dict:
	"""Make the journey's masters, start it from known stock, and say what it works with.
	`password` is the run's password of the job users (ui_users.prepare_job_users), with which
	the QA Manager user approves the items."""
	ensure_test_site()
	if frappe.session.user != "Administrator":
		frappe.throw(_("The browser journey is prepared only by the Administrator."), frappe.PermissionError)

	ensure_masters(
		approve_as_qa(password), approve_as_qa(password, "BOM"), approve_as_qa(password, "Asset"), password
	)
	if not password:
		frappe.throw(_("Give the run's password of the job users to verify the customer's licence."))
	ensure_licence(  # module 5: sold to only under a licence QA has verified
		CUSTOMER,
		lambda: acting_as(email_for("Sales")),
		lambda: acting_as(email_for("QA Manager")),
		password,
		"Set up for the browser journey",
	)
	write_off_leftover_stock()
	instrument = ensure_inspection_masters(COMPANY, approve_as_qa(password), approve_as_qa(password, "Asset"))
	calibrate_instrument(instrument, password, lambda: acting_as(email_for("Maintenance")))  # module 38
	return {
		"instrument": instrument,
		"company": COMPANY,
		"customer": CUSTOMER,
		"supplier": SUPPLIER,
		"product": PRODUCT,
		"product_shelf_life_days": PRODUCT_DETAILS[3],
		"quantity": QUANTITY,
		"rate": PRODUCT_DETAILS[2],
		"materials": {code: details[5] * QUANTITY / BOM_QUANTITY for code, details in RAW_MATERIALS.items()},
		"stores": {
			"quarantine": store(QUARANTINE),
			"approved": store("Stores"),
			"work_in_progress": store("Work In Progress"),
			"finished_goods": store("Finished Goods"),
		},
		"bom": frappe.db.get_value("BOM", {"item": PRODUCT, "is_default": 1, "docstatus": 1}),
		# Module 31: the record the work order is submitted on.
		"device_master_record": frappe.db.get_value(
			"Device Master Record", {"item": PRODUCT, "status": "Current", "docstatus": 1}
		),
		"operations": [operation for operation, _workstation, _minutes in OPERATIONS],
		"operator": frappe.db.get_value(
			"Employee", {"user_id": email_for("Operator")}
		),  # trained (module 37)
		"lots_in_store": put_later_expiring_lots(),
	}


def approve_as_qa(password: str | None, doctype: str = "Item") -> Callable[[str], None]:
	"""QA's signed approval of a journey item (module 1), bill of materials (module 2) or piece of
	equipment (module 8), as the run's QA Manager user."""
	from meddevice_suite.pilot.demo import signed_approval

	approved, act, _reason = signed_approval(doctype)

	def approve(code: str) -> None:
		if approved(code):
			return
		if not password:
			frappe.throw(_("Give the run's password of the job users to approve {0}.").format(code))
		with acting_as(email_for("QA Manager")):
			act(frappe.get_doc(doctype, code), password=password, reason="Set up for the browser journey")

	return approve


@contextmanager
def acting_as(user: str) -> Iterator[None]:
	"""Act as a journey job user, then return to the Administrator who prepares the journey.

	frappe.set_user rewrites the request's session in place (its sid becomes the user name, its
	data is emptied); that same object is what Frappe saves for the browser's sid when the request
	ends, so it is restored in place, not replaced."""
	saved = dict(frappe.local.session)
	frappe.set_user(user)  # nosemgrep: frappe-setuser - test site, Administrator only
	try:
		yield
	finally:
		frappe.set_user(saved["user"])  # nosemgrep: frappe-setuser - back to the Administrator
		frappe.local.session.clear()
		frappe.local.session.update(saved)


def ensure_masters(
	approve: Callable[[str], None],
	approve_bom: Callable[[str], None],
	approve_equipment: Callable[[str], None] | None = None,
	password: str | None = None,
) -> None:
	instrument = ensure_inspection_masters(
		COMPANY, approve, approve_equipment
	)  # the parameters the templates below name
	if password:  # module 38: the instrument calibrated before it measures (tests: by approve_equipment)
		calibrate_instrument(instrument, password, lambda: acting_as(email_for("Maintenance")))
	ensure(
		"Warehouse",
		store(QUARANTINE),
		warehouse_name=QUARANTINE,
		company=COMPANY,
		parent_warehouse=store("All Warehouses"),
	)
	ensure("Supplier", SUPPLIER, supplier_name=SUPPLIER, supplier_group="Raw Material")
	ensure("Customer", CUSTOMER, customer_name=CUSTOMER, customer_group="Commercial", territory="India")
	for code, (name, unit, hsn, shelf_life, rate, _per_batch) in RAW_MATERIALS.items():
		ensure_item(
			code, name, "Raw Material", unit, hsn, shelf_life, COMPANY, store("Stores"), supplier=SUPPLIER
		)
		ensure_price(code, "Standard Buying", rate)
		ensure_inspection_template(code, "Raw Material")
	name, hsn, rate, shelf_life = PRODUCT_DETAILS
	ensure_item(PRODUCT, name, "Products", "Nos", hsn, shelf_life, COMPANY, store("Finished Goods"))
	ensure_price(PRODUCT, "Standard Selling", rate)
	ensure_inspection_template(PRODUCT, "Products")
	for code in journey_items():  # each with its specification, approved before it is used
		approve(code)

	for operation, workstation, _minutes in OPERATIONS:
		ensure("Workstation", workstation, workstation_name=workstation, production_capacity=1)
		ensure("Operation", operation, name=operation, workstation=workstation)  # named by the user
	ensure(
		"Routing",
		ROUTING,
		routing_name=ROUTING,
		operations=[
			{
				"operation": operation,
				"workstation": workstation,
				"time_in_mins": minutes,
				"sequence_id": index,
			}
			for index, (operation, workstation, minutes) in enumerate(OPERATIONS, 1)
		],
	)
	if password:  # module 3: QA approves the operations, with their work instructions, and the routing
		from meddevice_suite.pilot.documents import approve_production_masters

		approve_production_masters(
			[(operation, workstation) for operation, workstation, _minutes in OPERATIONS],
			[ROUTING],
			lambda profile: acting_as(email_for(profile)),
			email_for,
			password,
		)
		from meddevice_suite.pilot.training import train_operator

		# Module 37: the operator logging time is competent on each operation's work instruction.
		train_operator(
			COMPANY,
			[operation for operation, _workstation, _minutes in OPERATIONS],
			lambda profile: acting_as(email_for(profile)),
			email_for,
			password,
		)
	ensure_bom(
		PRODUCT, ROUTING, {code: details[5] for code, details in RAW_MATERIALS.items()}, COMPANY, approve_bom
	)
	if password:  # module 31: the product's device master record, Current before production
		from meddevice_suite.pilot.device_master import ensure_device_master_record

		ensure_device_master_record(
			PRODUCT,
			lambda profile: acting_as(email_for(profile)),
			email_for,
			password,
			LABELLING_TITLE,
		)


def write_off_leftover_stock() -> None:
	"""Scrap whatever stock of the journey's items an earlier run left in any store: each
	leftover lot is rejected first, because only a rejected lot can be scrapped (module 17)."""
	leftovers = [row for item in journey_items() for row in stock_by_lot(item)]
	for lot in {row.batch_no for row in leftovers}:
		set_status(lot, REJECTED, "Browser test: leftover of an earlier run, scrapped")
	if leftovers:
		post_stock_entry(
			"Material Issue",
			[
				{
					"item_code": row.item_code,
					"s_warehouse": row.warehouse,
					"qty": row.qty,
					"batch_no": row.batch_no,
					"use_serial_batch_fields": 1,
				}
				for row in leftovers
			],
		)


def put_later_expiring_lots() -> dict[str, str]:
	"""A lot of each item in LOTS_IN_STORE, in its store, expiring after anything the run makes."""
	lots, rows = {}, []
	for item, (store_name, qty, rate) in LOTS_IN_STORE.items():
		lot = frappe.get_doc(
			{
				"doctype": "Batch",
				"item": item,
				"manufacturing_date": today(),
				"expiry_date": add_days(today(), LATER_EXPIRY_DAYS),
			}
		).insert(ignore_permissions=True)  # test-site set-up by the Administrator, checked by the caller
		lots[item] = lot.name
		rows.append(
			{
				"item_code": item,
				"t_warehouse": store(store_name),
				"qty": qty,
				"basic_rate": rate,
				"batch_no": lot.name,
				"use_serial_batch_fields": 1,
			}
		)
	post_stock_entry("Material Receipt", rows)
	# Stock that was already in an approved store before the run is approved stock.
	for lot in lots.values():
		set_status(lot, APPROVED, "Browser test: approved stock already in store before the run")
	return lots


def post_stock_entry(purpose: str, rows: list[dict]) -> None:
	entry = frappe.get_doc(
		{"doctype": "Stock Entry", "stock_entry_type": purpose, "company": COMPANY, "items": rows}
	)
	# test-site set-up by the Administrator, checked by the caller
	entry.insert(ignore_permissions=True)
	entry.submit()


# Any signed-in user may ask: the answer describes only a record the user may read.
@frappe.whitelist(methods=["POST"])
def prepare_training(password: str) -> dict:
	"""A work instruction new to the Operator's job, so the browser trains the operator on it: QA
	adds it to the job's matrix with a signature, the system opens the operator's record, and the
	Training Coordinator plans it, with the Production Supervisor to assess (module 37)."""
	from meddevice_suite.pilot.documents import ensure_work_instruction
	from meddevice_suite.pilot.training import ensure_matrix, ensure_operator
	from meddevice_suite.qms import training

	ensure_test_site()
	if frappe.session.user != "Administrator":
		frappe.throw(_("The browser journey is prepared only by the Administrator."), frappe.PermissionError)
	act_as = lambda profile: acting_as(email_for(profile))  # noqa: E731
	instruction = ensure_work_instruction(
		f"Work instruction: browser training {frappe.generate_hash(length=6)}", act_as, email_for, password
	)
	employee = ensure_operator(COMPANY, email_for("Operator"))
	ensure_matrix({instruction}, act_as, password)
	training.reconcile([employee])
	name = frappe.db.get_value(
		training.RECORD, {"employee": employee, "controlled_document": instruction, "status": training.OPEN}
	)
	with acting_as(email_for("Training Coordinator")):
		record = frappe.get_doc(training.RECORD, name)
		record.update(
			{
				"training_method": "Instructor-led",
				"training_date": today(),
				"trainer": "QA trainer (browser test)",
				"assessor": email_for("Production Supervisor"),
				"assessment_method": "Practical Demonstration",
			}
		)
		record.save()
	return {"record": name}


# The browser's policy (module 39): severity counts double, so the matrix is not symmetric and a
# swapped axis shows; D-13's scales, with the regions a test site needs.
def browser_acceptability(severity: int, probability: int) -> str:
	from meddevice_suite.qms import risk_management

	weight = 2 * severity + probability
	if weight >= 12:
		return risk_management.UNACCEPTABLE
	return risk_management.ALARP if weight >= 8 else risk_management.ACCEPTABLE


@frappe.whitelist(methods=["POST"])
def prepare_risk_management(password: str) -> dict:
	"""A risk management file with a draft risk assessment, for the browser to sign on the forms
	(module 39): a policy in force (QA reviews it, Management approves it), an effective plan, a
	specification that controls the hazard and an SOP that collects post-production information.
	The draft is authored by a second QA user, so that the QA Manager approves as a third person.
	Returns {"file", "assessment", "author", "sop"}."""
	from meddevice_suite.master_data.item_master import FINISHED_GOOD, ITEM_TYPE
	from meddevice_suite.pilot.documents import ensure_work_instruction
	from meddevice_suite.qms import risk_management

	ensure_test_site()
	if frappe.session.user != "Administrator":
		frappe.throw(_("The browser journey is prepared only by the Administrator."), frappe.PermissionError)
	act_as = lambda profile: acting_as(email_for(profile))  # noqa: E731
	author = ensure_second_qa(password)
	if not risk_management.current_policy():
		with acting_as(email_for("QA Manager")):
			policy = frappe.get_doc(
				{
					"doctype": risk_management.POLICY,
					"title": "Risk acceptability policy (browser test)",
					"policy_statement": "ISO 14971 Annex C and the state of the art (browser test).",
				}
			).insert()
			for cell in policy.matrix:
				cell.acceptability = browser_acceptability(cell.severity, cell.probability)
			policy.save()
			risk_management.review_policy(policy, password=password, reason="Scales and matrix checked")
		with acting_as(email_for("Management")):
			risk_management.approve_policy(
				frappe.get_doc(risk_management.POLICY, policy.name), password=password, reason="Approved"
			)
	suffix = frappe.generate_hash(length=6)
	plan = ensure_work_instruction("Risk management plan (browser test)", act_as, email_for, password, "SOP")
	sop = ensure_work_instruction(
		"Post-market surveillance and complaints (browser test)", act_as, email_for, password, "SOP"
	)
	specification = ensure_work_instruction(
		"Balloon burst specification (browser test)", act_as, email_for, password, "Specification"
	)
	item = (
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": f"UIJ-RISK-{suffix}",
				"item_name": f"Foley catheter, risk test {suffix}",
				"item_group": "Products",
				"stock_uom": "Nos",
				"is_stock_item": 0,
				ITEM_TYPE: FINISHED_GOOD,
			}
		)
		.insert(ignore_permissions=True)  # test-site set-up by the Administrator, checked above
		.name
	)
	with acting_as(author):
		file = frappe.get_doc(
			{
				"doctype": risk_management.FILE,
				"family_name": f"Foley catheter (browser test {suffix})",
				"intended_use": "Bladder drainage through the urethra (browser test).",
				"foreseeable_misuse": "Inflating the balloon in the urethra (browser test).",
				"risk_management_plan": plan,
				"items": [{"item": item}],
			}
		).insert()
		assessment = frappe.get_doc(
			{
				"doctype": risk_management.ASSESSMENT,
				"risk_management_file": file.name,
				"reviewer": email_for("Production Supervisor"),
				"hazards": [
					{
						"hazard_id": "H-01",
						"hazard_category": "Energy",
						"hazard": "Balloon bursts",
						"sequence_of_events": "Balloon over-inflated; wall thinner than specified",
						"hazardous_situation": "Fragments left in the bladder",
						"harm": "Infection, surgery to retrieve them",
						"severity": 4,
						"probability": 4,
						"residual_severity": 4,
						"residual_probability": 1,
						"justification": "Burst test of every lot; as low as reasonably practicable.",
					}
				],
				"controls": [
					{
						"hazard_id": "H-01",
						"option": risk_management.INHERENT,
						"description": "Balloon wall thickness and burst volume specified",
						"control_doctype": "Controlled Document",
						"control_name": specification,
						"implementation_evidence_type": "Controlled Document",
						"implementation_evidence": specification,
						"effectiveness_evidence_type": "Controlled Document",
						"effectiveness_evidence": specification,
					}
				],
			}
		).insert()
	return {"file": file.name, "assessment": assessment.name, "author": author, "sop": sop}


def ensure_second_qa(password: str) -> str:
	"""A second QA user with the run's password: a risk assessment is authored, reviewed and approved
	by three people, and only QA authors one (module 39)."""
	email = f"second.qa@{email_for('QA Manager').split('@', 1)[1]}"
	user = (
		frappe.get_doc("User", email)
		if frappe.db.exists("User", email)
		else frappe.new_doc("User", email=email, send_welcome_email=0)
	)
	if (
		user.is_new()
		or not user.enabled
		or [row.role_profile for row in user.role_profiles] != ["QA Manager"]
	):
		user.update(
			{
				"first_name": "Second QA",
				"last_name": "(browser test)",
				"enabled": 1,
				"role_profiles": [{"role_profile": "QA Manager"}],
			}
		)
		user.save(ignore_permissions=True)  # test-site set-up by the Administrator, checked by the caller
	update_password(email, password)
	return email


# The browser's device master record (module 31): a product of its own on the journey's routing and
# materials, so that revising its specification leaves the standard flow's product as it is.
DMR_PRODUCT = "UIJ-DMR-FC-2W-SIL-14"
DMR_PRODUCT_DETAILS = ("Foley catheter, 2-way, silicone, 14 Fr (browser test)", "90183910", 95, 1825)


@frappe.whitelist(methods=["POST"])
def prepare_device_master_record(password: str) -> dict:
	"""A draft revision of the browser product's device master record, drafted and completed by the
	Production Supervisor, for the browser to sign as its author and approve as QA (module 31): the
	product approved with its specifications, its approved default bill of materials on the
	journey's routing, and an effective Packaging and Labelling document. A revision left in progress
	by an earlier run is withdrawn by QA first. Returns {"record", "item", "specification"}."""
	from meddevice_suite.pilot.device_master import fill
	from meddevice_suite.pilot.documents import ensure_work_instruction
	from meddevice_suite.pilot.inspection import ensure_in_process_specification
	from meddevice_suite.qms import device_master_record as dmr

	ensure_test_site()
	if frappe.session.user != "Administrator":
		frappe.throw(_("The browser journey is prepared only by the Administrator."), frappe.PermissionError)
	approve, approve_bom = approve_as_qa(password), approve_as_qa(password, "BOM")
	ensure_masters(approve, approve_bom, approve_as_qa(password, "Asset"), password)
	name, hsn, _rate, shelf_life = DMR_PRODUCT_DETAILS
	ensure_item(DMR_PRODUCT, name, "Products", "Nos", hsn, shelf_life, COMPANY, store("Finished Goods"))
	specification = ensure_inspection_template(DMR_PRODUCT, "Products")
	approve(DMR_PRODUCT)
	ensure_bom(
		DMR_PRODUCT,
		ROUTING,
		{code: details[5] for code, details in RAW_MATERIALS.items()},
		COMPANY,
		approve_bom,
	)
	ensure_in_process_specification(DMR_PRODUCT)
	act_as = lambda profile: acting_as(email_for(profile))  # noqa: E731
	labelling = ensure_work_instruction(
		LABELLING_TITLE, act_as, email_for, password, "Packaging and Labelling"
	)
	if in_progress := dmr.revision_in_progress(DMR_PRODUCT):
		with acting_as(email_for("QA Manager")):
			dmr.withdraw(
				frappe.get_doc(dmr.DOCTYPE, in_progress),
				password=password,
				reason="Left in progress by an earlier browser run",
			)
	with acting_as(email_for("Production Supervisor")):
		record = dmr.revise(DMR_PRODUCT)
		fill(frappe.get_doc(dmr.DOCTYPE, record), labelling)
	return {"record": record, "item": DMR_PRODUCT, "specification": specification}


@frappe.whitelist(methods=["POST"])
def revise_dmr_specification(password: str) -> dict:
	"""The browser product's final specification changed under an approved change request, as the
	jobs do it: the Production Supervisor raises the request, QA assesses and approves it with
	signatures and changes the specification under it (module 6), which puts the product's device
	master record out of date (module 31). Returns {"change_request", "specification"}."""
	from meddevice_suite.master_data.specification import FINAL, specification_for

	ensure_test_site()
	if frappe.session.user != "Administrator":
		frappe.throw(_("The browser journey is prepared only by the Administrator."), frappe.PermissionError)
	specification = specification_for(DMR_PRODUCT, FINAL)
	change = approved_change_request("Quality Inspection Template", specification, password)
	with acting_as(email_for("QA Manager")):
		doc = frappe.get_doc("Quality Inspection Template", specification)
		row = doc.item_quality_inspection_parameter[-1]
		row.max_value = 5.5 if row.max_value != 5.5 else 5.4  # wider, then back, run after run
		doc.mds_change_request = change
		doc.save()
	return {"change_request": change, "specification": specification}


def approved_change_request(record_type: str, record: str, password: str) -> str:
	"""A minor change request listing the record, raised by the Production Supervisor and assessed
	and approved by QA with signatures (module 36)."""
	from meddevice_suite.qms import change_control

	with acting_as(email_for("Production Supervisor")):
		request = frappe.get_doc(
			{
				"doctype": "Change Request",
				"title": f"Revise {record} (browser test)",
				"change_type": "Product or BOM",
				"description": "Acceptance limit widened (browser test).",
				"reason": "Process capability study (browser test).",
				"affected_records": [{"reference_doctype": record_type, "reference_name": record}],
				"actions": [
					{
						"description": f"Revise {record}",
						"action_owner": email_for("Production Supervisor"),
						"due_date": today(),
					}
				],
			}
		).insert()
		change_control.submit_for_assessment(request.name)
	with acting_as(email_for("QA Manager")):
		request = frappe.get_doc("Change Request", request.name)
		request.update(
			{
				**dict.fromkeys(change_control.IMPACT_AREAS, "No"),
				"impact_summary": "Limit widened within the validated range (browser test).",
				"classification": "Minor",
				"effectiveness_check_days": 90,
			}
		)
		request.save()
		change_control.assess(request, password=password, reason="Assessed (browser test)")
		change_control.approve(
			frappe.get_doc("Change Request", request.name),
			password=password,
			reason="Approved (browser test)",
		)
	return request.name


@frappe.whitelist(methods=["POST"])
def draft_licence() -> str:
	"""A licence of the plant drafted by the signed-in QA user, with its copy in the important files
	repository, for the browser to verify on its form with a signature (module 41)."""
	ensure_test_site()
	frappe.has_permission("Regulatory Licence", "create", throw=True)
	tag = frappe.generate_hash(length=5)
	licence_type = f"Manufacturing licence (browser test) {tag}"
	frappe.get_doc(
		{"doctype": "Regulatory Licence Type", "licence_type_name": licence_type, "has_expiry": 1}
	).insert()
	category = frappe.get_doc(
		{"doctype": "File Category", "category_name": f"Licences (browser test) {tag}"}
	).insert()
	copy = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"licence-{tag}.txt",
			"content": f"Manufacturing licence {tag} (browser test)",
			"is_private": 1,
		}
	).insert()
	important_file = frappe.get_doc(
		{
			"doctype": "Important File",
			"title": f"Licence {tag}",
			"category": category.name,
			"new_file": copy.file_url,
		}
	).insert()
	return (
		frappe.get_doc(
			{
				"doctype": "Regulatory Licence",
				"licence_type": licence_type,
				"licence_number": f"MFG/MD-5/{tag}",
				"issuing_authority": "State Licensing Authority",
				"valid_from": add_days(today(), -30),
				"valid_until": add_days(today(), 1825),
				"important_file": important_file.name,
			}
		)
		.insert()
		.name
	)


@frappe.whitelist(methods=["POST"])
def draft_calibration(asset: str) -> str:
	"""A calibration of `asset` drafted by the signed-in user, for the browser to complete on its
	form with their signature (module 38)."""
	from meddevice_suite.pilot.inspection import draft_calibration as draft

	ensure_test_site()
	frappe.has_permission("Calibration Record", "create", throw=True)
	return draft(asset).name


@frappe.whitelist()
def lots_of(doctype: str, name: str) -> list[dict]:
	"""The lots each line of a stock transaction moved, with their expiry dates, in line order:
	[{item_code, from_store, to_store, batch_no, qty, expiry_date}]."""
	ensure_test_site()
	if doctype not in STOCK_TRANSACTIONS:
		frappe.throw(_("{0} is not a stock transaction.").format(_(doctype)))
	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")

	bundles = [row.serial_and_batch_bundle for row in doc.items if row.serial_and_batch_bundle]
	entries: dict[str, list[dict]] = {}
	for entry in frappe.get_all(
		"Serial and Batch Entry",
		filters={"parent": ["in", bundles or [""]]},
		fields=["parent", "batch_no", "qty"],
		order_by="idx",
	):
		entries.setdefault(entry.parent, []).append(entry)

	lots = []
	for row in doc.items:
		moved = entries.get(row.serial_and_batch_bundle) or [{"batch_no": row.batch_no, "qty": row.qty}]
		for entry in moved:
			lots.append(
				{
					"item_code": row.item_code,
					"from_store": row.get("s_warehouse")
					or (row.warehouse if doctype == "Delivery Note" else None),
					"to_store": row.get("t_warehouse")
					or (row.warehouse if doctype == "Purchase Receipt" else None),
					"batch_no": entry["batch_no"],
					"qty": abs(entry["qty"]),
				}
			)
	expiry = dict(
		frappe.get_all(
			"Batch",
			filters={"name": ["in", [lot["batch_no"] for lot in lots if lot["batch_no"]] or [""]]},
			fields=["name", "expiry_date"],
			as_list=True,
		)
	)
	for lot in lots:
		lot["expiry_date"] = expiry.get(lot["batch_no"])
	return lots
