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

doc_events = {
	"*": {
		# Audit trail: each handler returns at once for record types that are not regulated.
		"before_insert": "meddevice_suite.compliance.audit.before_insert",
		"before_change": "meddevice_suite.compliance.audit.before_change",
		"before_cancel": "meddevice_suite.compliance.audit.before_cancel",
		# What an approval forbids is refused first; asking a reason for it would mislead.
		"before_update_after_submit": [
			"meddevice_suite.compliance.approvals.before_update_after_submit",
			"meddevice_suite.compliance.audit.before_update_after_submit",
		],
		# Approval workflows: returns at once for record types without one.
		"validate": "meddevice_suite.compliance.approvals.validate",
		"on_submit": "meddevice_suite.compliance.approvals.on_submit",
		"onload": [
			"meddevice_suite.compliance.audit_timeline.onload",
			"meddevice_suite.compliance.signing.onload",
		],
		"on_change": "meddevice_suite.compliance.audit.on_change",
		"on_trash": "meddevice_suite.compliance.audit.on_trash",
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
		]
	},
	# Lot status (module 17): only an Approved lot leaves a store; each status has its store.
	"Warehouse": {"validate": "meddevice_suite.lot_control.stores.protect_store_role"},
	"Company": {"on_update": "meddevice_suite.lot_control.stores.stores_for_new_company"},
	"Stock Ledger Entry": {"validate": "meddevice_suite.lot_control.lot_status.guard_stock_ledger_entry"},
	"Stock Entry": {
		"validate": "meddevice_suite.lot_control.lot_status.guard_stock_entry",
		"before_cancel": "meddevice_suite.lot_control.lot_status.refuse_cancel_of_release_transfer",
	},
	"Delivery Note": {"validate": "meddevice_suite.lot_control.lot_status.guard_delivery_note"},
	"Pick List": {"validate": "meddevice_suite.lot_control.lot_status.guard_pick_list"},
	"Work Order": {"validate": "meddevice_suite.lot_control.lot_status.guard_work_order"},
	"Purchase Receipt": {
		"validate": [
			"meddevice_suite.lot_control.lot_status.guard_purchase_receipt",
			"meddevice_suite.lot_control.receipt.protect_certificate",
			"meddevice_suite.master_data.supplier_approval.mark_full_inspection",
		],
		"before_update_after_submit": "meddevice_suite.lot_control.receipt.protect_certificate",
	},
	# Controlled specifications (module 6): each inspection against the item's specification for
	# its stage; a specification in use changes only under an approved change request.
	"Quality Inspection": {
		"before_validate": "meddevice_suite.master_data.specification.choose_specification",
		"validate": [
			"meddevice_suite.master_data.specification.check_against_specification",
			"meddevice_suite.lot_control.receipt.guard_incoming_inspection",
		],
		"before_submit": "meddevice_suite.master_data.specification.check_sample_size",
	},
	"Quality Inspection Template": {
		"validate": "meddevice_suite.master_data.specification.guard_template",
		"on_trash": "meddevice_suite.master_data.specification.protect_template",
	},
	# Approved suppliers (module 4): orders only from approved suppliers, for approved items.
	"Purchase Order": {"validate": "meddevice_suite.master_data.supplier_approval.guard_purchase_order"},
	"Supplier": {"onload": "meddevice_suite.master_data.supplier_approval.supplier_onload"},
	"Item": {"validate": "meddevice_suite.setup.stock.refuse_negative_stock"},
	"Stock Settings": {"validate": "meddevice_suite.setup.stock.protect_stock_settings"},
	"Log Settings": {"validate": "meddevice_suite.compliance.retention.protect_login_history"},
	"File": {"before_validate": "meddevice_suite.compliance.evidence_files.protect"},
	"User": {
		"validate": "meddevice_suite.setup.roles.separate_administration",
		"before_rename": "meddevice_suite.compliance.audit.keep_user_id",
	},
}

# A regulated record's audit events in its form timeline, for users who may review them.
additional_timeline_content = {"*": ["meddevice_suite.compliance.audit_timeline.timeline_content"]}

# A record's audit history must not stop the record itself from being deleted;
# the deletion is recorded like any other action.
ignore_links_on_delete = ["Audit Event"]

# Print formats show the signatures on a record: signatures_for_print(doc).
jinja = {"methods": ["meddevice_suite.compliance.signing.signatures_for_print"]}

# Document control (module 30): outside QA, only effective revisions are visible.
# Evidence files (an approved document's, an archive's) cannot be changed or deleted.
# Change control (module 36): outside QA and Management, people see the requests they raised
# or have an action on.
permission_query_conditions = {
	"Document Revision": "meddevice_suite.qms.document_control.revision_query_conditions",
	"Change Request": "meddevice_suite.qms.change_control.query_conditions",
}
has_permission = {
	"Document Revision": "meddevice_suite.qms.document_control.revision_has_permission",
	"Change Request": "meddevice_suite.qms.change_control.has_permission",
	"File": "meddevice_suite.compliance.evidence_files.has_permission",
}

scheduler_events = {
	"daily": [
		"meddevice_suite.compliance.alerts.send_expiry_alerts",
		"meddevice_suite.compliance.archive.fail_stalled_archives",
		"meddevice_suite.qms.document_control.make_due_revisions_effective",
		"meddevice_suite.qms.document_control.send_review_alerts",
		"meddevice_suite.qms.change_control.send_change_alerts",
		"meddevice_suite.master_data.supplier_approval.send_certificate_alerts",
	],
}

# The desk's page loader, so that opening a record type one may not read says so plainly
# instead of reporting a missing permission on "document Page" (setup/desk.py).
override_whitelisted_methods = {"frappe.desk.desk_page.getpage": "meddevice_suite.setup.desk.getpage"}
