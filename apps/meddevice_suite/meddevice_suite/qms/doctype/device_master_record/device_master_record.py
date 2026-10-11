# Copyright (c) 2026, Demo Medical Devices Pvt Ltd and contributors
# For license information, please see license.txt

from frappe.model.document import Document

from meddevice_suite.qms import device_master_record


class DeviceMasterRecord(Document):
	"""The medical device file of a finished good (module 31); the rules are in
	`qms.device_master_record`."""

	def autoname(self) -> None:
		device_master_record.name_record(self)

	def before_insert(self) -> None:
		device_master_record.new_record(self)

	def before_validate(self) -> None:
		device_master_record.guard(self)

	def validate(self) -> None:
		device_master_record.validate(self)

	def before_update_after_submit(self) -> None:
		device_master_record.only_by_act(self)

	def before_cancel(self) -> None:
		device_master_record.refuse_cancel(self)

	def on_trash(self) -> None:
		device_master_record.refuse_delete(self)

	def onload(self) -> None:
		# The form offers a new revision only when none is in progress (the server checks again).
		self.set_onload("revision_in_progress", device_master_record.revision_in_progress(self.item))
		if self.status == device_master_record.OUT_OF_DATE:
			# The item's open work orders, for QA to decide on while the record is out of date.
			self.set_onload("open_work_orders", device_master_record.open_work_orders(self.item))
