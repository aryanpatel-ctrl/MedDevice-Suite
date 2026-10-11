// A risk assessment revision (module 39) moves on only through signed acts: its author, a reviewer
// and QA's approver are three people. The form offers each act to the person allowed to take it;
// the server checks every rule again and works out every risk (qms/risk_management.py).
const esign = frappe.provide("meddevice_suite.esign");
// ISO 14971 7.1: the records each kind of control is, and the document type it must be.
const CONTROL_DOCUMENT_TYPES = {
	"Inherently safe design and manufacture": ["Specification"],
	"Information for safety": ["Packaging and Labelling"],
};

frappe.ui.form.on("Risk Assessment", {
	setup(frm) {
		frm.set_query("risk_review", () => ({
			filters: { risk_management_file: frm.doc.risk_management_file, status: "Open" },
		}));
		// Only approved change requests that list this file (module 36, URS-QMS-024).
		frm.set_query("change_request", () => ({
			query: "meddevice_suite.qms.change_control.approved_changes_for",
			filters: {
				reference_doctype: "Risk Management File",
				reference_name: frm.doc.risk_management_file,
			},
		}));
		frm.set_query("risk_management_plan", () => ({ filters: { status: "Effective" } }));
		frm.set_query("item", "items", () => ({ filters: { mds_item_type: "Finished Good" } }));
		frm.set_query("control_name", "controls", (doc, cdt, cdn) => {
			const row = locals[cdt][cdn];
			if (row.control_doctype !== "Controlled Document") return {};
			const types = CONTROL_DOCUMENT_TYPES[row.option];
			return {
				filters: {
					status: "Effective",
					...(types ? { document_type: ["in", types] } : {}),
				},
			};
		});
	},

	refresh(frm) {
		if (frm.is_new()) return;
		show_status(frm);
		if (frm.is_dirty()) return; // sign what is saved, never unsaved edits

		const user = frappe.session.user;
		const qa = frappe.user.has_role("Quality Manager");
		const { docstatus, status, author, reviewer } = frm.doc;
		if (docstatus !== 0) return;
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
		if (["Draft", "In Review", "Awaiting Approval"].includes(status) && qa) {
			offer(__("Withdraw"), "Withdraw", "Rejected", { ask_reason: true });
		}
	},
});

function show_status(frm) {
	const { status, superseded_by, approved_on } = frm.doc;
	const notices = {
		Approved: [
			__("Approved on {0}. This is the risk assessment in force.", [
				frappe.datetime.str_to_user(approved_on),
			]),
			"green",
		],
		Superseded: [__("Superseded by {0}. Do not use this revision.", [superseded_by]), "red"],
	};
	if (notices[status]) frm.dashboard.set_headline_alert(...notices[status]);
}
