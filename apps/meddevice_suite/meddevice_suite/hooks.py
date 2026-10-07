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
		"before_update_after_submit": "meddevice_suite.compliance.audit.before_update_after_submit",
		# Approval workflows: returns at once for record types without one.
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
}

# A regulated record's audit events in its form timeline, for users who may review them.
additional_timeline_content = {"*": ["meddevice_suite.compliance.audit_timeline.timeline_content"]}

# A record's audit history must not stop the record itself from being deleted;
# the deletion is recorded like any other action.
ignore_links_on_delete = ["Audit Event"]

# Print formats show the signatures on a record: signatures_for_print(doc).
jinja = {"methods": ["meddevice_suite.compliance.signing.signatures_for_print"]}
