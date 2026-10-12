// Electronic signatures on the desk (module 49): the signing dialog every signed act
// uses, and the signatures shown on a signed record's form. The server decides what
// may be signed (compliance/signing.py); this only asks for the password and shows.

const MEANING_TEXT = {
	Authored: __("I wrote this and it is complete and correct."),
	Reviewed: __("I have reviewed this and found it fit for approval."),
	Approved: __("I approve this."),
	Rejected: __("I reject this."),
	Released: __("I release this for use."),
	Verified: __("I have verified this."),
	Trained: __("I was trained in this, and I understand it."),
	Competent: __("I have assessed this person and found them competent."),
	Withdrawn: __("I withdraw this from use."),
};

const esign = frappe.provide("meddevice_suite.esign");

// Sign `action` on the form's record; resolves once signed, rejects if the dialog is closed.
esign.sign = (frm, action, meaning, { ask_reason = false } = {}) =>
	new Promise((resolve, reject) => {
		let signed = false;
		const dialog = new frappe.ui.Dialog({
			title: __("Sign: {0}", [__(action)]),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "statement",
					options: `<p><strong>${frappe.utils.escape_html(
						__(meaning)
					)}</strong>: ${frappe.utils.escape_html(
						MEANING_TEXT[meaning] || ""
					)}</p><p class="text-muted small">${frappe.utils.escape_html(
						__(
							"{0} {1}, signed by {2}. Your signature is recorded with the date and time and is legally binding.",
							[__(frm.doctype), frm.doc.name, frappe.session.user_fullname]
						)
					)}</p>`,
				},
				...(ask_reason
					? [
							{
								fieldtype: "Small Text",
								fieldname: "reason",
								label: __("Reason"),
								reqd: 1,
							},
					  ]
					: []),
				{
					fieldtype: "Password",
					fieldname: "password",
					label: __("Your password"),
					reqd: 1,
				},
			],
			primary_action_label: __("Sign"),
			primary_action({ password, reason }) {
				dialog.disable_primary_action();
				frappe
					.xcall("meddevice_suite.compliance.signing.sign_action", {
						doctype: frm.doctype,
						name: frm.doc.name,
						action,
						password,
						reason: reason || null,
						// The server refuses a meaning other than the declared one (11.50(a)(3)).
						meaning,
					})
					.then(() => {
						signed = true;
						dialog.hide();
						frm.reload_doc();
						resolve();
					})
					.catch(() => {
						dialog.set_value("password", "");
						dialog.enable_primary_action();
					});
			},
			on_hide() {
				if (!signed) reject();
			},
		});
		dialog.show();
	});

// The signatures on a record, shown at the top of its form (URS-CMP-003).
$(document).on("form-refresh", (event, frm) => {
	const signatures = frm.doc.__onload?.signatures;
	if (!signatures?.length) return;
	const rows = signatures
		.map(
			(s) => `<tr>
				<td><strong>${frappe.utils.escape_html(__(s.meaning))}</strong></td>
				<td>${frappe.utils.escape_html(s.signer_name)}</td>
				<td>${frappe.utils.escape_html(frappe.datetime.str_to_user(s.signed_at))}</td>
				<td>${frappe.utils.escape_html(__(s.action))}${
				s.reason ? ` · ${frappe.utils.escape_html(s.reason)}` : ""
			}</td></tr>`
		)
		.join("");
	frm.dashboard.add_section(
		`<table class="table table-sm mds-signatures"><thead><tr>
			<th>${__("Meaning")}</th><th>${__("Signed by")}</th><th>${__("Date and time")}</th><th>${__(
			"Act"
		)}</th>
		</tr></thead><tbody>${rows}</tbody></table>`,
		__("Electronic signatures")
	);
});
