// A customer's licence (module 5): Sales records it; QA verifies or rejects it, and withdraws a
// verified one, as signed acts. The server checks every rule again (master_data/customer_licence.py).
const esign = frappe.provide("meddevice_suite.esign");

frappe.ui.form.on("Customer Licence", {
	refresh(frm) {
		show_status(frm);
		if (frm.is_new() || frm.is_dirty() || !frappe.user.has_role("Quality Manager")) return;
		const sign = (label, action, meaning, primary) => {
			const button = frm.add_custom_button(label, () =>
				// Closing the dialog signs nothing; there is nothing more to do then.
				esign.sign(frm, action, meaning, { ask_reason: true }).catch(() => {})
			);
			if (primary) button.removeClass("btn-default").addClass("btn-primary");
		};
		if (frm.doc.docstatus === 0) {
			// Submit is refused for everyone: the signed acts are offered instead.
			frm.page.clear_primary_action();
			sign(__("Verify"), "Verify", "Verified", true);
			sign(__("Reject"), "Reject", "Rejected", false);
		} else if (frm.doc.status === "Verified") {
			sign(__("Withdraw"), "Withdraw", "Rejected", false);
		}
	},
});

function show_status(frm) {
	if (frm.is_new()) return;
	if (frm.doc.docstatus === 0) {
		frm.dashboard.set_headline_alert(
			__("Awaiting QA's verification: no sales order or delivery is made on it yet."),
			"orange"
		);
		return;
	}
	const colours = { Verified: "green", Rejected: "red", Withdrawn: "red" };
	frm.dashboard.set_headline_alert(
		`${__(frm.doc.status)}: ${frappe.utils.escape_html(frm.doc.reason || "")}`,
		colours[frm.doc.status] || "blue"
	);
}
