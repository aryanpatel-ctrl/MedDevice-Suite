app_name = "meddevice_suite"
app_title = "MedDevice Suite"
app_publisher = "Demo Medical Devices Pvt Ltd"
app_description = "Medical-device manufacturing QMS and compliance layer for ERPNext (ISO 13485, 21 CFR Part 11, CDSCO MDR 2017)"
app_email = "examdycoe@gmail.com"
app_license = "gpl-3.0"

required_apps = ["erpnext"]

before_migrate = "meddevice_suite.setup.modules.sync_module_defs"
after_install = "meddevice_suite.setup.install.apply_configuration"
after_migrate = "meddevice_suite.setup.install.apply_configuration"

doc_events = {
	"*": {
		# Audit trail: each handler returns at once for record types that are not regulated.
		"on_change": "meddevice_suite.compliance.audit.on_change",
		"on_trash": "meddevice_suite.compliance.audit.on_trash",
		"after_rename": "meddevice_suite.compliance.audit.after_rename",
	},
	"Desktop Layout": {"validate": "meddevice_suite.setup.desk.enforce_on_desktop_layout"},
}

# A record's audit history must not stop the record itself from being deleted;
# the deletion is recorded like any other action.
ignore_links_on_delete = ["Audit Event"]
