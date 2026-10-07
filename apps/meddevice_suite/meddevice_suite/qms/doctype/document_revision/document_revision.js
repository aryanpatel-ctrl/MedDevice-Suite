// A document revision moves on only through signed acts (module 30). The form offers each
// act to the person allowed to take it and says plainly whether the revision may be used;
// the server checks every rule again (qms/document_control.py).
const esign = frappe.provide("meddevice_suite.esign");

frappe.ui.form.on("Document Revision", {
	refresh(frm) {
		if (frm.is_new()) return;
		show_status(frm);
		if (frm.is_dirty()) return; // sign what is saved, never unsaved edits

		const user = frappe.session.user;
		const qa = frappe.user.has_role("Quality Manager");
		const { status, author, reviewer } = frm.doc;
		const offer = (label, action, meaning, options = {}) => {
			const button = frm.add_custom_button(label, () =>
				esign.sign(frm, action, meaning, options).catch(() => {})
			);
			if (options.primary) button.removeClass("btn-default").addClass("btn-primary");
		};

		if (status === "Draft" && user === author) {
			offer(__("Sign as Author"), "Author", "Authored", { primary: true });
		}
		if (status === "In Review" && user === reviewer) {
			offer(__("Sign as Reviewer"), "Review", "Reviewed", { primary: true });
		}
		if (["In Review", "Awaiting Approval"].includes(status) && (user === reviewer || qa)) {
			offer(__("Return for Changes"), "Return", "Rejected", { ask_reason: true });
		}
		if (status === "Awaiting Approval" && qa && user !== author && user !== reviewer) {
			offer(__("Approve"), "Approve", "Approved", { primary: true });
		}
	},
});

function show_status(frm) {
	const { status, superseded_by, effective_date } = frm.doc;
	const date = (value) => frappe.datetime.str_to_user(value);
	const notices = {
		Effective: [
			__("Effective since {0}. This is the revision in use.", [date(effective_date)]),
			"green",
		],
		Approved: [__("Approved; takes effect on {0}.", [date(effective_date)]), "blue"],
		Superseded: [__("Superseded by {0}. Do not use this revision.", [superseded_by]), "red"],
		Obsolete: [__("The document has been withdrawn. Do not use this revision."), "red"],
	};
	if (notices[status]) frm.dashboard.set_headline_alert(...notices[status]);
}
