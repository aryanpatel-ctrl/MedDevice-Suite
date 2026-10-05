app_name = "meddevice_suite"
app_title = "MedDevice Suite"
app_publisher = "Demo Medical Devices Pvt Ltd"
app_description = "Medical-device manufacturing QMS and compliance layer for ERPNext (ISO 13485, 21 CFR Part 11, CDSCO MDR 2017)"
app_email = "examdycoe@gmail.com"
app_license = "gpl-3.0"

required_apps = ["erpnext"]

after_install = "meddevice_suite.setup.install.apply_configuration"
after_migrate = "meddevice_suite.setup.install.apply_configuration"
