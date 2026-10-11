# Copyright (c) 2026, Demo Medical Devices Pvt Ltd and contributors
# For license information, please see license.txt

from frappe.model.document import Document

from meddevice_suite.qms import regulatory_licence


class RegulatoryLicence(Document):
	"""A licence, registration or certificate of the plant (module 41); the rules are in
	`qms.regulatory_licence`."""

	def before_validate(self) -> None:
		regulatory_licence.validate(self)

	def before_update_after_submit(self) -> None:
		regulatory_licence.guard_after_verification(self)

	def before_cancel(self) -> None:
		regulatory_licence.refuse_cancel(self)

	def on_trash(self) -> None:
		regulatory_licence.refuse_delete(self)
