// QA's decision on a supplier (module 4): the form offers Approve, Approve Conditionally and
// Not Approve as signed acts; the server checks every rule again
// (master_data/supplier_approval.py).
const esign = frappe.provide("meddevice_suite.esign");

frappe.ui.form.on("Supplier Approval", {
	refresh(frm) {
		if (frm.doc.docstatus === 1) {
			show_decision(frm);
			return;
		}
		if (frm.is_new() || frm.is_dirty() || !frappe.user.has_role("Quality Manager")) return;
		const sign = (label, action, meaning, primary) => {
			const button = frm.add_custom_button(label, () =>
				esign.sign(frm, action, meaning, { ask_reason: true }).catch(() => {})
			);
			if (primary) button.removeClass("btn-default").addClass("btn-primary");
		};
		sign(__("Approve"), "Approve", "Approved", true);
		sign(__("Approve Conditionally"), "Approve Conditionally", "Approved", false);
		sign(__("Not Approve"), "Not Approve", "Rejected", false);
	},
});

function show_decision(frm) {
	const colours = { Approved: "green", Conditional: "orange", "Not Approved": "red" };
	let text = __(frm.doc.decision);
	if (frm.doc.decision === "Conditional") {
		text = __("Conditional until {0}", [frappe.datetime.str_to_user(frm.doc.valid_until)]);
	}
	frm.dashboard.set_headline_alert(
		`${text}: ${frappe.utils.escape_html(frm.doc.reason || "")}`,
		colours[frm.doc.decision] || "blue"
	);
}
