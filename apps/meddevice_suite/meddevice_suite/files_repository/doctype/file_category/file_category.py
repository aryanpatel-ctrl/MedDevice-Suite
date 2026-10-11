# Copyright (c) 2026, Demo Medical Devices Pvt Ltd and contributors
# For license information, please see license.txt

from frappe.utils.nestedset import NestedSet

from meddevice_suite.files_repository import files


class FileCategory(NestedSet):
	"""A category or sub-group of the important files repository (module 61)."""

	nsm_parent_field = "parent_file_category"

	def validate(self) -> None:
		files.check_category(self)

	def on_trash(self) -> None:
		files.protect_category(self)
		super().on_trash()
