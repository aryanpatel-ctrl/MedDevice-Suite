"""Frappe hooks of MedDevice Suite.

Every handler listed here lives in the folder that owns its feature (rules.md section 3);
this file only wires them to Frappe. Handlers on "*" return at once for record types
that are not regulated, so they cost nothing on the rest of ERPNext.
"""

app_name = "meddevice_suite"
app_title = "MedDevice Suite"
app_publisher = "Demo Medical Devices Pvt Ltd"
app_description = "Medical-device manufacturing QMS and compliance layer for ERPNext (ISO 13485, 21 CFR Part 11, CDSCO MDR 2017)"
app_email = "examdycoe@gmail.com"
app_license = "gpl-3.0"

required_apps = ["erpnext"]

app_include_js = "meddevice_suite.bundle.js"

before_migrate = "meddevice_suite.setup.modules.sync_module_defs"
after_install = "meddevice_suite.setup.install.apply_configuration"
after_migrate = "meddevice_suite.setup.install.apply_configuration"
# Test sites only: the stores the tests move stock through, approved as QA's signed act records it.
before_tests = "meddevice_suite.qa.site_setup.before_tests"

# The built-in Administrator account is not used on a working site (URS-CMP-039): refused at
# sign-in, before a session is made, and on every request after the session or API key is read.
on_login = "meddevice_suite.compliance.administrator.refuse_administrator_login"
auth_hooks = ["meddevice_suite.compliance.administrator.refuse_administrator_requests"]

doc_events = {
	"*": {
		# Audit trail: each handler returns at once for record types that are not regulated.
		"before_insert": [
			"meddevice_suite.compliance.audit.before_insert",
			# Version-controlled masters (URS-MST-001): each returns at once for other record types.
			"meddevice_suite.master_data.versioning.first_version",
		],
		"before_change": "meddevice_suite.compliance.audit.before_change",
		# Item master (module 1): also before validate, which runs when ERPNext saves with
		# ignore_validate (Production Plan, bulk transactions).
		"before_validate": "meddevice_suite.master_data.item_master.refuse_unapproved",
		"before_cancel": [
			"meddevice_suite.compliance.audit.before_cancel",
			# The ledger checks read a voucher's header once per document (lot_control/lot_status.py):
			# a document submitted or cancelled is read afresh.
			"meddevice_suite.lot_control.lot_status.forget_voucher",
		],
		"before_submit": "meddevice_suite.lot_control.lot_status.forget_voucher",
		# What an approval forbids is refused first; asking a reason for it would mislead.
		"before_update_after_submit": [
			"meddevice_suite.compliance.approvals.before_update_after_submit",
			"meddevice_suite.master_data.versioning.version_master",
			# Item master (module 1): an item a submitted record takes on is an approved one.
			"meddevice_suite.master_data.item_master.refuse_unapproved",
			"meddevice_suite.compliance.audit.before_update_after_submit",
		],
		# Approval workflows: returns at once for record types without one.
		"validate": [
			"meddevice_suite.compliance.approvals.validate",
			"meddevice_suite.master_data.versioning.version_master",
			# Item master (module 1): a draft item is used in no record; returns at once otherwise.
			"meddevice_suite.master_data.item_master.refuse_unapproved",
		],
		"on_submit": "meddevice_suite.compliance.approvals.on_submit",
		"onload": [
			"meddevice_suite.compliance.audit_timeline.onload",
			"meddevice_suite.compliance.signing.onload",
		],
		"on_change": [
			"meddevice_suite.compliance.audit.on_change",
			# Change control (module 36): a change made under a request is recorded on it once the
			# save that makes it has gone through; returns at once for any other save.
			"meddevice_suite.qms.change_control.record_changes_made",
		],
		"on_trash": "meddevice_suite.compliance.audit.on_trash",
		"before_rename": "meddevice_suite.master_data.versioning.refuse_rename_in_use",
		"after_rename": "meddevice_suite.compliance.audit.after_rename",
	},
	"Desktop Layout": {"validate": "meddevice_suite.setup.desk.enforce_on_desktop_layout"},
	# Configuration as code: the declared alerts and workflows change only by migrate.
	"Notification": {
		"before_validate": "meddevice_suite.compliance.alerts.protect",
		"on_trash": "meddevice_suite.compliance.alerts.protect",
	},
	"Workflow": {
		"before_validate": "meddevice_suite.setup.workflows.protect",
		"on_trash": "meddevice_suite.setup.workflows.protect",
	},
	"Batch": {
		"validate": [
			"meddevice_suite.compliance.alerts.reset_expiry_alert",
			"meddevice_suite.setup.stock.refuse_negative_stock",
			"meddevice_suite.lot_control.lot_status.guard_batch",
			# Module 18 (plan H6): a lot's dates are fixed once it has stock; QA corrects them signed.
			"meddevice_suite.lot_control.lot_dates.guard_batch",
		],
		"onload": "meddevice_suite.lot_control.lot_dates.batch_onload",
	},
	# Lot status (module 17): only an Approved lot leaves a store; each status has its store.
	"Warehouse": {
		"validate": [
			"meddevice_suite.lot_control.stores.protect_store_role",
			# Module 7: approved by QA with a signature before stock moves in or out.
			"meddevice_suite.master_data.warehouse.guard",
		],
		"onload": "meddevice_suite.master_data.warehouse.onload",
	},
	"Company": {"on_update": "meddevice_suite.lot_control.stores.stores_for_new_company"},
	"Stock Ledger Entry": {
		"validate": [
			"meddevice_suite.master_data.warehouse.guard_stock_ledger_entry",  # module 7: approved stores
			# Plan D-28: finished goods come in from production; opening stock is the one receipt.
			"meddevice_suite.lot_control.finished_goods.guard_stock_ledger_entry",
			"meddevice_suite.lot_control.lot_status.guard_stock_ledger_entry",
			"meddevice_suite.lot_control.expiry.guard_stock_ledger_entry",  # module 18: expiry, FEFO
		],
		"on_submit": "meddevice_suite.lot_control.lot_status.guard_lot_made_on_submit",
	},
	"Stock Entry": {
		# Module 31: a finished good is made only on a work order of it, while its device master
		# record is Current (before_validate runs even when validation is skipped).
		"before_validate": "meddevice_suite.qms.device_master_record.guard_stock_entry",
		"validate": [
			# Plan D-28, first: a finished good that may not come in on it is refused for what it is,
			# before the lot status rules say where its lot may go.
			"meddevice_suite.lot_control.finished_goods.guard_document",
			"meddevice_suite.lot_control.lot_status.guard_stock_entry",
		],
		"before_submit": "meddevice_suite.qms.device_master_record.guard_stock_entry",
		"before_cancel": "meddevice_suite.lot_control.lot_status.refuse_cancel_of_release_transfer",
	},
	"Delivery Note": {
		# Module 5: delivered only to a customer with a verified licence valid on its date.
		"before_validate": "meddevice_suite.master_data.customer_licence.guard_delivery_note",
		# Module 17: approved stock delivered; a customer's return comes into the Returns store.
		"validate": "meddevice_suite.lot_control.lot_status.guard_sales_document",
	},
	# Customer licences (module 5): sold only to customers QA has verified a licence of.
	"Sales Order": {
		"before_validate": "meddevice_suite.master_data.customer_licence.guard_sales_order",
		"before_update_after_submit": "meddevice_suite.master_data.customer_licence.guard_order_update",
	},
	# Module 17, on an invoice that moves stock: as on a delivery note.
	"Sales Invoice": {
		"before_validate": "meddevice_suite.master_data.customer_licence.guard_sales_invoice",
		"validate": "meddevice_suite.lot_control.lot_status.guard_sales_document",
	},
	"POS Invoice": {
		"before_validate": "meddevice_suite.master_data.customer_licence.guard_sales_invoice",
		"validate": "meddevice_suite.lot_control.lot_status.guard_sales_document",
	},
	"Customer": {
		"onload": "meddevice_suite.master_data.customer_licence.customer_onload",
		"validate": "meddevice_suite.master_data.customer_licence.guard_customer",
		"before_rename": "meddevice_suite.master_data.customer_licence.guard_customer_rename",
	},
	"Pick List": {"validate": "meddevice_suite.lot_control.lot_status.guard_pick_list"},
	# Plan D-28: a count brings a new lot of a finished good in only as opening stock at migration.
	"Stock Reconciliation": {"validate": "meddevice_suite.lot_control.finished_goods.guard_document"},
	"Packing Slip": {"validate": "meddevice_suite.lot_control.lot_status.guard_packing_slip"},
	"Work Order": {
		# Module 2: only from an approved, active bill of its own product; module 3: only on approved
		# operations and workstations. Before validate, which Production Plan's work orders (saved
		# without validation) pass through too, and module 3 again after ERPNext refetches operations.
		"before_validate": [
			# Module 31: a finished good is submitted only on its Current device master record, which
			# pins the order's bill of materials (before_validate runs even when validation is skipped).
			"meddevice_suite.qms.device_master_record.guard_work_order",
			"meddevice_suite.lot_control.lot_status.guard_unvalidated_work_order",
			"meddevice_suite.master_data.bom.guard_work_order",
			"meddevice_suite.master_data.routing.guard_work_order",
		],
		"validate": [
			"meddevice_suite.lot_control.lot_status.guard_work_order",
			"meddevice_suite.master_data.routing.guard_work_order",  # after ERPNext refetches operations
		],
		"before_submit": [
			"meddevice_suite.qms.equipment_care.guard_work_order",  # modules 38 and 40
			"meddevice_suite.qms.device_master_record.guard_work_order",  # module 31
		],
	},
	# Calibration and maintenance are this app's signed records (module 38), not ERPNext's.
	"Asset Maintenance": {"before_validate": "meddevice_suite.qms.calibration.refuse_erpnext_maintenance"},
	"Asset Maintenance Log": {
		"before_validate": "meddevice_suite.qms.calibration.refuse_erpnext_maintenance"
	},
	# Routings, operations and workstations (module 3): approved by QA with a signature before use.
	"Operation": {
		"validate": "meddevice_suite.master_data.routing.guard",
		"onload": "meddevice_suite.master_data.routing.onload",
		"on_update": "meddevice_suite.qms.pinned_records.on_pinned_change",  # module 39: a risk control's new version
	},
	"Workstation": {
		"validate": "meddevice_suite.master_data.routing.guard",
		"onload": "meddevice_suite.master_data.routing.onload",
	},
	"Routing": {
		"validate": "meddevice_suite.master_data.routing.guard",
		"onload": "meddevice_suite.master_data.routing.onload",
		"on_update": "meddevice_suite.qms.pinned_records.on_pinned_change",  # pinned records (qms/pinned_records.py)
	},
	"Job Card": {
		# Module 3: approved operations and workstations; modules 38 and 40: the workstation's
		# equipment fit (calibrated, maintained, qualified, not under repair), at every save and
		# submit, and when time is logged after submit. Module 37: every operator logging time is
		# competent in the operation's work instruction (time logs change after submit too).
		"before_validate": [
			"meddevice_suite.master_data.routing.guard_job_card",
			"meddevice_suite.qms.equipment_care.guard_job_card",
			"meddevice_suite.qms.training.guard_job_card",
		],
		"validate": [
			"meddevice_suite.master_data.routing.guard_job_card",  # after its sub-operations are set
			"meddevice_suite.qms.equipment_care.guard_job_card",
		],
		"before_submit": "meddevice_suite.qms.equipment_care.guard_job_card",
		"before_update_after_submit": [
			"meddevice_suite.qms.equipment_care.guard_job_card",
			"meddevice_suite.qms.training.guard_job_card",
		],
	},
	# Module 37: joining, a new job, leaving or returning changes what training is needed.
	"Employee": {"on_update": "meddevice_suite.qms.training.on_employee_update"},
	"Purchase Receipt": {
		"validate": [
			# Plan D-28: a finished good is made by the plant, never bought in.
			"meddevice_suite.lot_control.finished_goods.guard_document",
			"meddevice_suite.lot_control.lot_status.guard_purchase_receipt",
			"meddevice_suite.lot_control.receipt.protect_certificate",
			"meddevice_suite.lot_control.receipt.refuse_existing_lots",
			"meddevice_suite.master_data.supplier_approval.mark_full_inspection",
		],
		"before_update_after_submit": "meddevice_suite.lot_control.receipt.protect_certificate",
	},
	# Controlled specifications (module 6): each inspection against the item's specification for
	# its stage, approved by QA (plan D-33); a specification in use changes only under an approved
	# change request.
	"Quality Inspection": {
		"before_validate": [
			"meddevice_suite.master_data.specification.choose_specification",
			# Module 6: the sampling plan follows from the lot (URS-MST-009).
			"meddevice_suite.master_data.sampling.apply_plan",
		],
		"validate": [
			"meddevice_suite.master_data.specification.check_against_specification",
			# After the readings are judged: under a plan, the lot by its nonconforming units.
			"meddevice_suite.master_data.sampling.judge",
			"meddevice_suite.lot_control.receipt.guard_incoming_inspection",
			# Module 8: a reading's instrument is approved equipment in service.
			"meddevice_suite.master_data.equipment.guard_inspection_instruments",
		],
		"before_submit": "meddevice_suite.master_data.specification.check_sample_size",
	},
	# Version-controlled masters (URS-MST-001): the default bill of materials in force is replaced,
	# and one is cancelled, only under an approved change request.
	"BOM": {
		# Module 2: only QA's signed approval submits a bill of materials (before_validate, which
		# runs even when a caller skips validation), and the signature is taken in that submit.
		"before_validate": "meddevice_suite.master_data.bom.only_by_approval",
		"before_submit": [
			"meddevice_suite.master_data.versioning.guard_new_default_bom",
			# Module 3: its routing, operations and workstations are approved.
			"meddevice_suite.master_data.routing.guard_bom",
		],
		"onload": "meddevice_suite.master_data.bom.bom_onload",
		"on_submit": [
			"meddevice_suite.master_data.versioning.record_displaced_default",
			"meddevice_suite.master_data.bom.sign_approval",
			# Module 39: the bill in force changed, for the risk controls pinning it.
			"meddevice_suite.qms.pinned_records.on_pinned_change",
		],
		"on_update_after_submit": [
			"meddevice_suite.master_data.versioning.record_displaced_default",
			"meddevice_suite.qms.pinned_records.on_pinned_change",
		],
		"before_cancel": "meddevice_suite.master_data.versioning.guard_cancel",
		"on_cancel": [
			"meddevice_suite.master_data.versioning.let_change_requests_not_block_cancel",
			"meddevice_suite.qms.pinned_records.on_pinned_change",
		],
	},
	"Asset": {
		# Equipment master (module 8): approved by QA with a signature before it is used.
		"validate": "meddevice_suite.master_data.equipment.guard_asset",
		# Frappe runs no validate on a change after submit; the approval stays the signature's there too.
		"before_update_after_submit": "meddevice_suite.master_data.equipment.guard_asset",
		"onload": "meddevice_suite.master_data.equipment.asset_onload",
		"before_cancel": "meddevice_suite.master_data.versioning.guard_cancel",
		"on_cancel": "meddevice_suite.master_data.versioning.let_change_requests_not_block_cancel",
	},
	# Plan D-33: QA approves a specification with a signature; a change returns it to draft.
	"Quality Inspection Template": {
		"validate": "meddevice_suite.master_data.specification.guard_template",
		"onload": "meddevice_suite.master_data.specification.onload",
		"on_trash": "meddevice_suite.master_data.specification.protect_template",
		"on_update": "meddevice_suite.qms.pinned_records.on_pinned_change",  # module 39: pinned records
	},
	# Modules 39 and 31: a controlled document revised or withdrawn, for the risk controls and the
	# device master records pinning it.
	"Controlled Document": {"on_update": "meddevice_suite.qms.pinned_records.on_pinned_change"},
	# Approved suppliers (module 4): orders only from approved suppliers, for approved items.
	"Purchase Order": {
		"validate": "meddevice_suite.master_data.supplier_approval.guard_purchase_order",
		"before_update_after_submit": "meddevice_suite.master_data.supplier_approval.guard_order_update",
	},
	"Supplier": {"onload": "meddevice_suite.master_data.supplier_approval.supplier_onload"},
	# Suppliers are rated by the Supplier Rating (URS-PUR-002), never by ERPNext's scorecard.
	"Supplier Scorecard": {"before_validate": "meddevice_suite.master_data.supplier_rating.refuse_scorecard"},
	"Purchase Invoice": {"validate": "meddevice_suite.master_data.supplier_approval.refuse_stock_on_invoice"},
	"BOM Update Log": {"validate": "meddevice_suite.master_data.versioning.refuse_bom_replace"},
	"Item": {
		"validate": [
			"meddevice_suite.setup.stock.refuse_negative_stock",
			"meddevice_suite.master_data.item_master.guard_item",
		],
		"onload": "meddevice_suite.master_data.item_master.item_onload",
		"on_update": [
			"meddevice_suite.master_data.versioning.update_variants_under_control",
			"meddevice_suite.qms.pinned_records.on_pinned_change",  # module 39: pinned records
		],
	},
	# Quality Settings (URS-PLT-013): the lockout is changed there, under change control.
	"System Settings": {"validate": "meddevice_suite.compliance.access_policy.guard_system_settings"},
	"Stock Settings": {
		"validate": [
			"meddevice_suite.setup.stock.protect_stock_settings",
			"meddevice_suite.master_data.item_master.guard_auto_reorder",
		]
	},
	"Log Settings": {"validate": "meddevice_suite.compliance.retention.protect_login_history"},
	"File": {"before_validate": "meddevice_suite.compliance.evidence_files.protect"},
	# Job profiles change only with a release of this app (URS-CMP-014).
	"Role Profile": {
		"validate": [
			"meddevice_suite.setup.roles.refuse_hand_changed_job_profile",
			"meddevice_suite.setup.roles.separate_administration_in_profile",
		],
		"on_trash": "meddevice_suite.setup.roles.refuse_hand_changed_job_profile",
	},
	"User": {
		"validate": "meddevice_suite.setup.roles.separate_administration",
		"before_rename": "meddevice_suite.compliance.audit.keep_user_id",
		# A user ID in the audit trail is disabled, never deleted (21 CFR 11.300(b)). Runs before
		# the "*" handlers and before Frappe's link check.
		"on_trash": "meddevice_suite.compliance.audit.keep_user_with_history",
	},
	# The permissions the role matrix governs change only with a release of this app (URS-CMP-015).
	"Custom DocPerm": {
		"before_validate": "meddevice_suite.setup.roles.refuse_hand_added_permission",
		"on_trash": "meddevice_suite.setup.roles.refuse_hand_removed_permission",
	},
}

# A regulated record's audit events in its form timeline, for users who may review them.
additional_timeline_content = {"*": ["meddevice_suite.compliance.audit_timeline.timeline_content"]}

# A record's audit history must not stop the record itself from being deleted;
# the deletion is recorded like any other action. A user is the exception: the User
# on_trash handler above refuses deleting anyone the audit trail names.
ignore_links_on_delete = ["Audit Event"]

# Print formats show the signatures on a record: signatures_for_print(doc).
jinja = {"methods": ["meddevice_suite.compliance.signing.signatures_for_print"]}

# Document control (module 30): outside QA, only effective revisions are visible.
# Evidence files (an approved document's, an archive's) cannot be changed or deleted.
# Change control (module 36): outside QA and Management, people see the requests they raised
# or have an action on.
permission_query_conditions = {
	# Reminders (module 60): each job sees its own; QA and management see all.
	"Reminder": "meddevice_suite.work_management.reminders.query_conditions",
	"Work Task": "meddevice_suite.work_management.work_task.query_conditions",
	"Important File": "meddevice_suite.files_repository.files.query_conditions",
	"Document Revision": "meddevice_suite.qms.document_control.revision_query_conditions",
	"Change Request": "meddevice_suite.qms.change_control.query_conditions",
	# Training (module 37): outside QA, training, production and management, one's own records.
	"Training Record": "meddevice_suite.qms.training.query_conditions",
}
has_permission = {
	"Reminder": "meddevice_suite.work_management.reminders.has_permission",
	"Work Task": "meddevice_suite.work_management.work_task.has_permission",
	"Important File": "meddevice_suite.files_repository.files.has_permission",
	"Document Revision": "meddevice_suite.qms.document_control.revision_has_permission",
	"Change Request": "meddevice_suite.qms.change_control.has_permission",
	"File": "meddevice_suite.compliance.evidence_files.has_permission",
	"Training Record": "meddevice_suite.qms.training.has_permission",
}

# The audit trail's anchor file is never named by a File record (URS-CMP-040): refused before
# Frappe's File.before_insert reads the named file and stores a copy of it.
extend_doctype_class = {"File": ["meddevice_suite.compliance.evidence_files.ProtectedFile"]}

scheduler_events = {
	# The head of the audit trail and of the signatures, so that removed entries are detected
	# (URS-CMP-040): hourly, so at most an hour of entries is ever unanchored.
	"hourly": ["meddevice_suite.compliance.audit_anchor.anchor_trail"],
	"daily": [
		"meddevice_suite.compliance.alerts.send_expiry_alerts",
		"meddevice_suite.compliance.archive.fail_stalled_archives",
		"meddevice_suite.qms.document_control.make_due_revisions_effective",
		# After revisions take effect: training needed and expiring (module 37).
		"meddevice_suite.qms.training.daily",
		"meddevice_suite.qms.document_control.send_review_alerts",
		"meddevice_suite.qms.change_control.send_change_alerts",
		"meddevice_suite.master_data.supplier_approval.send_certificate_alerts",
		"meddevice_suite.master_data.customer_licence.send_expiry_alerts",
		"meddevice_suite.qms.regulatory_licence.send_expiry_alerts",  # module 41
		"meddevice_suite.master_data.supplier_rating.rate_finished_months",
		# Module 39: risk reviews due, and the pinned controls changed past the document hooks.
		"meddevice_suite.qms.risk_management.raise_periodic_reviews",
		"meddevice_suite.qms.pinned_records.sweep",
		# After the alerts above: reminders track what they told people (module 60).
		"meddevice_suite.work_management.reminders.run",
	],
}

# The desk's page loader, so that opening a record type one may not read says so plainly
# instead of reporting a missing permission on "document Page" (setup/desk.py).
override_whitelisted_methods = {
	"frappe.desk.desk_page.getpage": "meddevice_suite.setup.desk.getpage",
	# Item master (module 1): lots and serial numbers made in bulk only for approved items.
	"erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle.upload_csv_file": (
		"meddevice_suite.master_data.item_master.upload_csv_file"
	),
	"erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle.create_serial_nos": (
		"meddevice_suite.master_data.item_master.create_serial_nos"
	),
	# ERPNext adds a job card's time log by writing the row straight to the database, past every
	# document hook and without a permission check; the job card's rules (routing, equipment and
	# the operators' training) run before it is called.
	"erpnext.manufacturing.doctype.job_card.job_card.make_time_log": (
		"meddevice_suite.qms.equipment_care.make_time_log"
	),
}
