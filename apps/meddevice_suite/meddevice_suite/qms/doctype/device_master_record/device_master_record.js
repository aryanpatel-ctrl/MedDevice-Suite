// The device master record (module 31): its author (QA or the Production Supervisor) signs it, QA,
// another person, approves it, returns it for changes or withdraws it, each with a signature; QA or
// the Production Supervisor starts the next revision. Every rule is checked on the server
// (qms/device_master_record.py).
const esign = frappe.provide("meddevice_suite.esign");
const DMR = "meddevice_suite.qms.device_master_record";

frappe.ui.form.on("Device Master Record", {
	setup(frm) {
		frm.set_query("item", () => ({
			filters: {
				mds_item_type: "Finished Good",
				mds_approval_status: "Approved",
				disabled: 0,
			},
		}));
		frm.set_query("controlled_document", "documents", () => ({
			filters: { status: "Effective" },
		}));
	},

	refresh(frm) {
		show_status(frm);
		if (frm.is_new() || frm.is_dirty()) return; // sign what is saved, never unsaved edits
		const user = frappe.session.user;
		const qa = frappe.user.has_role("Quality Manager");
		const may_author = qa || frappe.user.has_role("Manufacturing User");
		const { docstatus, status, author } = frm.doc;
		const offer = (label, action, meaning, options = {}) =>
			frm.add_custom_button(label, () =>
				esign.sign(frm, action, meaning, options).catch(() => {})
			);
		if (docstatus === 0 && status === "Draft" && user === author) {
			offer(__("Sign as Author"), "Author", "Authored").addClass("btn-primary");
		}
		if (docstatus === 0 && status === "Awaiting Approval" && qa) {
			if (user !== author) {
				offer(__("Approve"), "Approve", "Approved").addClass("btn-primary");
			}
			offer(__("Return for Changes"), "Return", "Rejected", { ask_reason: true });
		}
		if (
			qa &&
			["Draft", "Awaiting Approval", "Current", "Out of Date"].includes(status) &&
			docstatus < 2
		) {
			offer(__("Withdraw"), "Withdraw", "Rejected", { ask_reason: true });
		}
		const in_progress = frm.doc.__onload?.revision_in_progress;
		if (
			may_author &&
			docstatus === 1 &&
			["Current", "Out of Date"].includes(status) &&
			!in_progress
		) {
			frm.add_custom_button(__("Start New Revision"), () =>
				frappe
					.xcall(`${DMR}.revise`, { item: frm.doc.item })
					.then((name) => frappe.set_route("Form", "Device Master Record", name))
			);
		}
	},
});

function show_status(frm) {
	const { status } = frm.doc;
	if (status === "Out of Date") {
		const changed = (frm.doc.elements || [])
			.filter((row) => row.stale)
			.map(
				(row) =>
					`${__(row.reference_doctype)} ${row.reference_name}: ${row.change_note || ""}`
			)
			.concat((frm.doc.other_changes || "").split("\n").filter((line) => line.trim()))
			.map((line) => frappe.utils.escape_html(line));
		// The item's open work orders, for QA to decide on (the server lists them on load).
		const orders = (frm.doc.__onload?.open_work_orders || []).map((name) =>
			frappe.utils.escape_html(name)
		);
		frm.dashboard.set_headline_alert(
			__(
				"Out of date since {0}; not for use until QA approves a new revision. Changed: {1}. Open work orders: {2}",
				[
					frappe.datetime.str_to_user(frm.doc.out_of_date_since),
					changed.join("; "),
					orders.length ? orders.join(", ") : __("none"),
				]
			),
			"red"
		);
	} else if (status === "Superseded") {
		frm.dashboard.set_headline_alert(
			__("Superseded by {0}; not for use.", [
				frappe.utils.escape_html(frm.doc.superseded_by),
			]),
			"red"
		);
	} else if (status === "Withdrawn") {
		frm.dashboard.set_headline_alert(__("Withdrawn; not for use."), "red");
	} else if (status === "Current") {
		frm.dashboard.set_headline_alert(
			__("Current: the record this device is made by."),
			"green"
		);
	}
}
