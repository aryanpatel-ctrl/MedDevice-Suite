from frappe.model.document import Document

from meddevice_suite.compliance import quality_settings


class QualitySettings(Document):
	"""The adjustable values of the validated system; the rules are in compliance/quality_settings.py."""

	def validate(self) -> None:
		quality_settings.validate(self)

	def on_update(self) -> None:
		quality_settings.on_update(self)
