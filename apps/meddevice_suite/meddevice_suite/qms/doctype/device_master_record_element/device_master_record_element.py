# Copyright (c) 2026, Demo Medical Devices Pvt Ltd and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class DeviceMasterRecordElement(Document):
	"""A record the device master record names, pinned at its version (module 31); filled by the
	system from the live records (`qms.device_master_record.assemble`), never typed."""

	pass
