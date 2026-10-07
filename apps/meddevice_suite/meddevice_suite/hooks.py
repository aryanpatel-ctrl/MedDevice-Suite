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
		"before_update_after_submit": [
			"meddevice_suite.compliance.audit.before_update_after_submit",
			"meddevice_suite.compliance.approvals.before_update_after_submit",
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
	"Batch": {"validate": "meddevice_suite.compliance.alerts.reset_expiry_alert"},
	"File": {"before_validate": "meddevice_suite.compliance.evidence_files.protect"},
	"User": {"validate": "meddevice_suite.setup.roles.separate_administration"},
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
permission_query_conditions = {
	"Document Revision": "meddevice_suite.qms.document_control.revision_query_conditions",
}
has_permission = {
	"Document Revision": "meddevice_suite.qms.document_control.revision_has_permission",
	"File": "meddevice_suite.compliance.evidence_files.has_permission",
}

scheduler_events = {
	"daily": [
		"meddevice_suite.compliance.alerts.send_expiry_alerts",
		"meddevice_suite.qms.document_control.make_due_revisions_effective",
	],
}
