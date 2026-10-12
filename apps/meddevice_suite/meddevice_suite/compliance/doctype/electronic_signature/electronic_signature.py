# Copyright (c) 2026, Demo Medical Devices Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class ElectronicSignature(Document):
	"""A 21 CFR Part 11 signature. Created only by `meddevice_suite.compliance.esign.sign`.

	Once recorded it can never change: any save after the first, and any delete,
	is refused for every user, whatever their permissions.

	Nor can one be inserted any other way (21 CFR 11.70). No role may create signatures, but a
	system manager can grant that on a site, and code can skip permissions: either could record
	a signature in another person's name with a fingerprint that verifies. The signing service
	marks the one it inserts in the document's flags, which a request cannot set.
	"""

	def before_insert(self) -> None:
		# Runs for every insert, even one that skips permissions and validation.
		if not self.flags.recorded_by_signing:
			frappe.throw(
				_("An electronic signature is recorded only by signing, with the signer's password."),
				frappe.PermissionError,
			)

	def validate(self) -> None:
		if not self.is_new():
			frappe.throw(_("An electronic signature cannot be changed."), frappe.PermissionError)
		# A person signs under their own account only; Frappe sets the owner from the session.
		if self.owner != self.signer:
			frappe.throw(
				_("A signature is recorded only under the signer's own account."), frappe.PermissionError
			)

	def on_trash(self) -> None:
		frappe.throw(_("An electronic signature cannot be deleted."), frappe.PermissionError)


def on_doctype_update() -> None:
	# A record's signatures are read at every signing and verification, and a signer's
	# when their work is reviewed; each stays an index lookup at millions of signatures.
	frappe.db.add_index("Electronic Signature", ["reference_doctype", "reference_name", "signed_at"])
	frappe.db.add_index("Electronic Signature", ["signer", "signed_at"])
	# The newest signature on a record is the one no other signature follows.
	frappe.db.add_index("Electronic Signature", ["previous_signature"])
