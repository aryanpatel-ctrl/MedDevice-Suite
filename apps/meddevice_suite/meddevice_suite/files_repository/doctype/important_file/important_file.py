# Copyright (c) 2026, Demo Medical Devices Pvt Ltd and contributors
# For license information, please see license.txt

from frappe.model.document import Document

from meddevice_suite.files_repository import files


class ImportantFile(Document):
	"""A file of the important files repository with its versions (module 61); the rules are in
	`files_repository.files`."""

	def validate(self) -> None:
		files.check_file(self)

	def on_trash(self) -> None:
		files.protect_file(self)
