// Lot control on the desk (modules 16, 17, 18): QC verifies supplier certificates from the goods
// receipt, QA opens a lot's decision from the lot, and once a lot has stock its dates are shown
// fixed and QA corrects them with a signature. The server checks every rule again
// (lot_control/receipt.py, lot_control/release.py, lot_control/lot_dates.py).
const RECEIPT = "meddevice_suite.lot_control.receipt";
const esign = frappe.provide("meddevice_suite.esign");
const LOT_DATES = ["manufacturing_date", "expiry_date"];

frappe.ui.form.on("Purchase Receipt", {
	refresh(frm) {
		if (frm.doc.docstatus !== 1 || frm.doc.is_return) return;
		const qc = ["QC Inspector", "Quality Manager"].some((role) => frappe.user.has_role(role));
		const unverified = (frm.doc.items || []).filter(
			(row) => row.mds_certificate && !row.mds_certificate_verified
		);
		if (!qc || !unverified.length) return;
		frm.add_custom_button(__("Verify Certificates"), () =>
			verify_certificates(frm, unverified)
		)
			.removeClass("btn-default")
			.addClass("btn-primary");
	},
});

function verify_certificates(frm, rows) {
	const dialog = new frappe.ui.Dialog({
		title: __("Verify supplier certificates"),
		fields: [
			{
				fieldtype: "HTML",
				fieldname: "note",
				options: `<p class="text-muted small">${frappe.utils.escape_html(
					__(
						"Tick each line whose certificate you have checked against the purchase specification."
					)
				)}</p>`,
			},
			{
				fieldtype: "MultiCheck",
				fieldname: "lines",
				label: __("Lines"),
				columns: 1,
				select_all: true,
				options: rows.map((row) => ({
					label: `${row.idx}. ${row.item_code} · ${__("lot {0}", [
						row.batch_no || __("in bundle"),
					])} · ${__("supplier lot {0}", [row.mds_supplier_lot_no || "-"])}`,
					value: row.name,
					checked: true,
				})),
			},
		],
		primary_action_label: __("Verify"),
		async primary_action({ lines }) {
			dialog.disable_primary_action();
			try {
				for (const row of lines || []) {
					await frappe.xcall(`${RECEIPT}.verify_certificate`, {
						receipt: frm.doc.name,
						row,
					});
				}
				dialog.hide();
				frm.reload_doc();
			} catch (error) {
				dialog.enable_primary_action();
			}
		},
	});
	dialog.show();
}

frappe.ui.form.on("Batch", {
	refresh(frm) {
		const fixed = !frm.is_new() && Boolean(frm.doc.__onload?.dates_fixed);
		// The form is reused for the next lot opened, so each lot sets the fields for itself.
		for (const field of LOT_DATES) frm.set_df_property(field, "read_only", fixed ? 1 : 0);
		if (frm.is_new() || !frappe.user.has_role("Quality Manager")) return;
		if (["Quarantine", "Hold", "Approved"].includes(frm.doc.mds_lot_status)) {
			frm.add_custom_button(__("Decide Lot"), () =>
				frappe.new_doc("Lot Release", { batch: frm.doc.name })
			);
		}
		if (fixed && !frm.is_dirty()) {
			frm.add_custom_button(__("Correct Dates"), () => correct_dates(frm));
		}
	},
});

// QA's signed correction of a lot's dates: the new dates, the reason and the password in one dialog.
function correct_dates(frm) {
	esign
		.sign(frm, "Correct Dates", "Approved", {
			ask_reason: true,
			fields: [
				{
					fieldtype: "Date",
					fieldname: "manufacturing_date",
					label: __("Manufacturing Date"),
					default: frm.doc.manufacturing_date,
				},
				{
					fieldtype: "Date",
					fieldname: "expiry_date",
					label: __("Expiry Date"),
					default: frm.doc.expiry_date,
				},
			],
		})
		.catch(() => {});
}
