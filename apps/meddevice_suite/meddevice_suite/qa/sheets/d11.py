"""What the browser test sheet of D11 Task, Approval & Reminders starts from (modules 48, 59, 60;
URS-PLT-014 to URS-PLT-017, URS-PLT-020, URS-CMP-019, URS-CMP-020), on a test site only
(cypress/integration/sheet_d11.js).

The sheet's tests act on the screens as the Production Supervisor, the Operator, QA, Management,
Sales, Purchase and Stores: work tasks raised, done, returned, approved with signatures and
withdrawn; reminders seen and refused by hand; recurring reminders planned and completed; a purchase
order and a quotation through their approval workflows; reminders told once and escalated once; a
shorter lead time that leaves a reminder open; and each job's My Work.

Each test starts from records of its own, made here afresh through the code's own services, each
step as the job that does it (`acting_as`):

- a task raised by a job for another, with its approver, as far along as the test needs: open,
  submitted, or returned by its approver with a signature and submitted again; its due date in the
  past where the test needs it overdue;
- a lot of the journey's raw material received into quarantine, its expiry date corrected by QA with
  a signature to fall within the lot expiry alert's lead time (which alerts Stores and QA at once);
- an instrument approved by QA, never calibrated, so its calibration is due;
- a recurring reminder QA plans;
- an approved change request listing Quality Settings, raised by the Production Supervisor and
  assessed and approved by QA with signatures, for QA to shorten a lead time on the screen; and the
  lead time put back afterwards the same way;
- what QA's, the Production Supervisor's and Management's My Work list: a draft specification of a
  draft item, the browser product's device master record signed by its author, goods a customer sent
  back, a quotation awaiting price approval, and a change request QA raised, assessed by a second
  QA person.

The reminders are raised, followed up and told by the daily run (work_management.reminders.run);
`daily_run` runs it as the scheduler does for the categories the test is about (the other categories
are other sheets' and are left to them), with the recurring reminders, the follow-up of what is
overdue and the notices, and says which reminders it told something.

Only the Administrator who runs the tests prepares the sheet.
"""

from contextlib import AbstractContextManager

import frappe
from frappe import _
from frappe.model.workflow import apply_workflow
from frappe.utils import add_days, cint, today

from meddevice_suite.compliance import quality_settings
from meddevice_suite.compliance.signing import sign_action
from meddevice_suite.lot_control import lot_dates, stores
from meddevice_suite.master_data.specification import INCOMING
from meddevice_suite.qa.sheets import d3, d4, d10
from meddevice_suite.qa.ui_journey import (
	COMPANY,
	CUSTOMER,
	PRODUCT,
	PRODUCT_DETAILS,
	SUPPLIER,
	acting_as,
	ensure_second_qa,
	prepare_device_master_record,
)
from meddevice_suite.qa.ui_users import email_for, ensure_test_site
from meddevice_suite.qms import change_control
from meddevice_suite.setup.roles import JOB_PROFILES
from meddevice_suite.work_management import reminders, work_task

QA, PRODUCTION, OPERATOR, SALES = "QA Manager", "Production Supervisor", "Operator", "Sales"
REASON = "Browser test sheet D11"
MATERIAL = d4.MATERIAL
# The sheet's task, as D11-T01 words it.
TASK_SUBJECT = "Clean extruder die 2 after silicone run"
TASK_DESCRIPTION = "Clean and inspect die 2 per WI-PRD-007"
# How far along a task is prepared (`prepare_task`).
TASK_STAGES = ("open", "submitted", "resubmitted")
LOT_EXPIRY_SETTING = "lot_expiry_warning_days"


def require_administrator() -> None:
	ensure_test_site()
	if frappe.session.user != "Administrator":
		frappe.throw(
			_("The browser test sheets are prepared only by the Administrator."), frappe.PermissionError
		)


def require_password(password: str | None) -> str:
	if not password:
		frappe.throw(_("Give the run's password of the job users."))
	return password


def act_as(profile: str) -> AbstractContextManager:
	return acting_as(email_for(profile))


def require_job(profile: str) -> str:
	if profile not in JOB_PROFILES:
		frappe.throw(_("{0} is not one of the jobs.").format(profile))
	return profile


def tag() -> str:
	return frappe.generate_hash(length=10).upper()


# --- The sheet's masters ------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def prepare_sheet(password: str) -> dict:
	"""The journey supplier's approval for the sheet's material and the second QA person, after the
	journey's masters (ui_journey.prepare_journey); and what the tests work with."""
	require_administrator()
	require_password(password)
	d4.ensure_supplier_approved(password, [MATERIAL])
	return {
		"company": COMPANY,
		"supplier": SUPPLIER,
		"material": MATERIAL,
		"customer": CUSTOMER,
		"product": PRODUCT,
		"rate": PRODUCT_DETAILS[2],
		"task_subject": TASK_SUBJECT,
		"task_description": TASK_DESCRIPTION,
		"second_qa": ensure_second_qa(password),
		"stores": {
			"quarantine": stores.store_for(stores.QUARANTINE, COMPANY),
			"returns": stores.store_for(stores.RETURNS, COMPANY),
		},
		"lot_expiry_default": quality_settings.SETTINGS[LOT_EXPIRY_SETTING].default,
	}


@frappe.whitelist(methods=["POST"])
def unique_tag() -> str:
	"""A tag that names the records a test makes on the screen apart from any earlier run's."""
	require_administrator()
	return tag()


# --- Work tasks (module 59) -----------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def prepare_task(
	password: str,
	stage: str = "open",
	raised_by: str = PRODUCTION,
	assigned_to: str = OPERATOR,
	approver: str = QA,
	due_in_days: int = 3,
) -> dict:
	"""A task raised by `raised_by` for `assigned_to`, approved by `approver`, due in `due_in_days`
	(in the past when negative), taken as far as `stage`: open; submitted by the person assigned; or
	resubmitted (submitted, returned by the approver with a signature, submitted again). Returns
	{task, subject, due_date}."""
	require_administrator()
	require_password(password)
	if stage not in TASK_STAGES:
		frappe.throw(_("The sheet prepares tasks only {0}.").format(", ".join(TASK_STAGES)))
	for profile in (raised_by, assigned_to, approver):
		require_job(profile)
	due = add_days(today(), cint(due_in_days))
	with act_as(raised_by):
		task = (
			frappe.get_doc(
				{
					"doctype": work_task.DOCTYPE,
					"subject": TASK_SUBJECT,
					"description": TASK_DESCRIPTION,
					"due_date": due,
					"assigned_to": email_for(assigned_to),
					"approver": email_for(approver),
				}
			)
			.insert()
			.name
		)
	if stage in ("submitted", "resubmitted"):
		with act_as(assigned_to):
			work_task.submit_for_approval(task, "Die cleaned, no damage found")
	if stage == "resubmitted":
		with act_as(approver):
			sign_action(
				work_task.DOCTYPE, task, "Return", password, "Record the inspection of the die lip as well"
			)
		with act_as(assigned_to):
			work_task.submit_for_approval(task, "Die cleaned; die lip inspected, no nicks")
	return {"task": task, "subject": TASK_SUBJECT, "due_date": str(due)}


# --- Due items the reminders track (module 60) -----------------------------------------------------


@frappe.whitelist(methods=["POST"])
def prepare_lot_expiring(password: str, days: int = 5) -> dict:
	"""A lot of the journey's raw material received by Stores into quarantine, its expiry date
	corrected by QA with a signature to `days` from today, which alerts Stores and QA at once when it
	falls within the lot expiry alert's lead time (lot_control.lot_dates). Returns {lot, item,
	expiry_date}."""
	require_administrator()
	require_password(password)
	lot = d4.received(MATERIAL, 10)["lot"]
	expiry = add_days(today(), cint(days))
	with act_as(QA):
		lot_dates.correct_dates(
			frappe.get_doc("Batch", lot),
			password=password,
			reason=f"{REASON}: expiry date checked against the certificate",
			expiry_date=expiry,
		)
	return {"lot": lot, "item": MATERIAL, "expiry_date": str(expiry)}


@frappe.whitelist(methods=["POST"])
def prepare_calibration_due(password: str) -> dict:
	"""An instrument recorded and approved by QA with a signature and never calibrated: its
	calibration is due from the day QA approved it. Returns {asset, asset_name}."""
	require_administrator()
	require_password(password)
	asset = d10.approved(d10.new_equipment({}), password)
	return {"asset": asset, "asset_name": frappe.db.get_value("Asset", asset, "asset_name")}


@frappe.whitelist(methods=["POST"])
def prepare_schedule(title: str, first_due_in_days: int = 3, lead_days: int = 7) -> dict:
	"""A recurring reminder QA plans, active, of the Quality Manager job, half-yearly. Returns
	{schedule, first_due_date}."""
	require_administrator()
	first_due = add_days(today(), cint(first_due_in_days))
	with act_as(QA):
		schedule = frappe.get_doc(
			{
				"doctype": reminders.SCHEDULE,
				"title": f"{title} {tag()}",
				"description": "Planned by QA (browser test D11)",
				"responsible_role": "Quality Manager",
				"frequency": "Half-yearly",
				"first_due_date": first_due,
				"lead_days": cint(lead_days),
				"active": 1,
			}
		).insert()
	return {"schedule": schedule.name, "title": schedule.title, "first_due_date": str(first_due)}


@frappe.whitelist(methods=["POST"])
def daily_run(categories: str | list) -> dict:
	"""The daily reminder run (reminders.run) for the categories named (none: only the recurring
	reminders), as the scheduler runs it:
	each category's reminders raised and settled, then the recurring reminders raised, what is
	overdue followed up and the notices sent. Returns {told: the open reminders whose people the run
	told something (People Told Of changed)}."""
	require_administrator()
	categories = (frappe.parse_json(categories) if isinstance(categories, str) else categories) or []
	unknown = [category for category in categories if category not in reminders.CATEGORIES]
	if unknown:
		frappe.throw(_("The daily run has no reminder category {0}.").format(", ".join(unknown)))
	before = told_so_far()
	for category in categories:
		# A failure here is the test's, not another category's: it is not logged and passed over.
		reminders.track(category, reminders.CATEGORIES[category].due)
	for name, item in reminders.occurrences_due():
		reminders.in_step(
			f"recurring reminder {name}",
			lambda name=name, item=item: reminders.make(reminders.SCHEDULED, item, schedule=name),
		)
	for name in reminders.to_follow_up():
		reminders.in_step(f"reminder {name}", lambda name=name: reminders.follow_up(name))
	reminders.send_notices()
	after = told_so_far()
	return {"told": sorted(name for name, told in after.items() if before.get(name, "") != told)}


def told_so_far() -> dict[str, str]:
	"""Open reminder -> what its people have been told (People Told Of)."""
	return {
		row.name: row.notified or ""
		for row in frappe.get_all(
			reminders.DOCTYPE,
			filters={"status": ["in", reminders.STILL_OPEN]},
			fields=["name", "notified"],
		)
	}


# --- The lead time QA shortens (Quality Settings, under change control) ----------------------------


def approved_settings_change(password: str) -> str:
	"""A minor change request listing Quality Settings, raised by the Production Supervisor and
	assessed and approved by QA with signatures (module 36)."""
	with act_as(PRODUCTION):
		request = frappe.get_doc(
			{
				"doctype": "Change Request",
				"title": f"Lot expiry alert lead time {tag()} (browser test D11)",
				"change_type": change_control.CHANGE_TYPE_OF[quality_settings.DOCTYPE],
				"description": "The lot expiry alert's lead time changed (browser test D11).",
				"reason": "Lead time reviewed against usage (browser test D11).",
				"affected_records": [
					{
						"reference_doctype": quality_settings.DOCTYPE,
						"reference_name": quality_settings.DOCTYPE,
					}
				],
				"actions": [
					{
						"description": "Change the lead time in Quality Settings",
						"action_owner": email_for(PRODUCTION),
						"due_date": add_days(today(), 7),
					}
				],
			}
		).insert()
		change_control.submit_for_assessment(request.name)
	with act_as(QA):
		request = frappe.get_doc("Change Request", request.name)
		request.update(
			{
				**dict.fromkeys(change_control.IMPACT_AREAS, "No"),
				"impact_summary": "An alert's lead time only (browser test D11).",
				"classification": "Minor",
				"effectiveness_check_days": 0,
			}
		)
		request.save()
		change_control.assess(request, password=password, reason=REASON)
		change_control.approve(
			frappe.get_doc("Change Request", request.name), password=password, reason=REASON
		)
	return request.name


def set_lot_expiry_lead(days: int, password: str) -> None:
	"""QA sets the lot expiry alert's lead time under an approved change request, unless it is so."""
	if quality_settings.value(LOT_EXPIRY_SETTING) == days:
		return
	change = approved_settings_change(password)
	with act_as(QA):
		settings = frappe.get_doc(quality_settings.DOCTYPE)
		settings.set(LOT_EXPIRY_SETTING, days)
		settings.change_request = change
		settings.save()


@frappe.whitelist(methods=["POST"])
def prepare_shorter_lead(password: str) -> dict:
	"""D11-T17: the lot expiry alert's lead time as decided (put back first, if an earlier run left it
	shorter); a lot expiring in 20 days with its open Lot Expiry reminder, raised by the daily run;
	and an approved change request listing Quality Settings. Returns {lot, expiry_date, reminder,
	change_request, lead_days}."""
	require_administrator()
	require_password(password)
	default = quality_settings.SETTINGS[LOT_EXPIRY_SETTING].default
	set_lot_expiry_lead(default, password)
	lot = prepare_lot_expiring(password, 20)
	reminders.track("Lot Expiry", reminders.CATEGORIES["Lot Expiry"].due)  # the daily run
	reminder = frappe.db.get_value(
		reminders.DOCTYPE,
		{
			"category": "Lot Expiry",
			"reference_doctype": "Batch",
			"reference_name": lot["lot"],
			"status": ["in", reminders.STILL_OPEN],
		},
	)
	return {
		**lot,
		"reminder": reminder,
		"change_request": approved_settings_change(password),
		"lead_days": default,
	}


@frappe.whitelist(methods=["POST"])
def restore_lead_time(password: str) -> int:
	"""The lot expiry alert's lead time put back as decided, by QA under an approved change request,
	after D11-T17 shortened it. Returns the lead time in force."""
	require_administrator()
	require_password(password)
	set_lot_expiry_lead(quality_settings.SETTINGS[LOT_EXPIRY_SETTING].default, password)
	return quality_settings.value(LOT_EXPIRY_SETTING)


# --- Approvals waiting in My Work (URS-PLT-017) ----------------------------------------------------


def quotation_awaiting_approval() -> str:
	"""A quotation Sales drafts for the journey's customer and sends for price approval."""
	with act_as(SALES):
		quotation = frappe.get_doc(
			{
				"doctype": "Quotation",
				"quotation_to": "Customer",
				"party_name": CUSTOMER,
				"company": COMPANY,
				"items": [{"item_code": PRODUCT, "qty": 100, "rate": PRODUCT_DETAILS[2]}],
			}
		).insert()
		apply_workflow(quotation, "Send for Price Approval")
	return quotation.name


def change_raised_by_qa(password: str) -> dict:
	"""A change request QA raises, assessed by the second QA person with a signature: awaiting
	approval, which QA, who raised it, may not give."""
	second_qa = ensure_second_qa(password)
	with act_as(QA):
		request = frappe.get_doc(
			{
				"doctype": "Change Request",
				"title": f"Review the reminder lead times {tag()} (browser test D11)",
				"change_type": change_control.CHANGE_TYPE_OF[quality_settings.DOCTYPE],
				"description": "Alert lead times reviewed (browser test D11).",
				"reason": "Annual review of the alerts (browser test D11).",
				"affected_records": [
					{
						"reference_doctype": quality_settings.DOCTYPE,
						"reference_name": quality_settings.DOCTYPE,
					}
				],
				"actions": [
					{
						"description": "Change the lead times in Quality Settings",
						"action_owner": email_for(QA),
						"due_date": add_days(today(), 7),
					}
				],
			}
		).insert()
		change_control.submit_for_assessment(request.name)
	with acting_as(second_qa):
		request = frappe.get_doc("Change Request", request.name)
		request.update(
			{
				**dict.fromkeys(change_control.IMPACT_AREAS, "No"),
				"impact_summary": "Alert lead times only (browser test D11).",
				"classification": "Minor",
				"effectiveness_check_days": 0,
			}
		)
		request.save()
		change_control.assess(request, password=password, reason=REASON)
	return {"name": request.name, "title": request.title, "assessed_by": second_qa}


def draft_specification() -> dict:
	"""A draft raw material with a complete incoming specification naming it, in draft (D3-T04)."""
	item = d3.new_material(f"UIJ-D11-RM-{tag()}")
	name = d3.new_specification(f"{item} {INCOMING} (browser test D11)", item)
	return {"name": name, "item": item}


@frappe.whitelist(methods=["POST"])
def prepare_my_work(password: str) -> dict:
	"""D11-T18: a draft specification naming its item; the browser product's device master record
	drafted by the Production Supervisor and signed by them as its author, awaiting QA's approval;
	goods a customer sent back into the Returns store; a quotation awaiting price approval; and a
	change request QA raised, assessed by the second QA person. Returns what each is."""
	require_administrator()
	require_password(password)
	record = prepare_device_master_record(password)
	with act_as(PRODUCTION):
		sign_action("Device Master Record", record["record"], "Author", password, REASON)
	returned = d4.prepare_delivery(qty=10, delivered=3, returned=2)
	return {
		"specification": draft_specification(),
		"device_master_record": {
			"name": record["record"],
			"item": record["item"],
			"item_name": frappe.db.get_value("Item", record["item"], "item_name"),
		},
		"returned": {"lot": returned["lot"], "qty": 2, "store": stores.store_for(stores.RETURNS, COMPANY)},
		"quotation": quotation_awaiting_approval(),
		"change_request": change_raised_by_qa(password),
	}
