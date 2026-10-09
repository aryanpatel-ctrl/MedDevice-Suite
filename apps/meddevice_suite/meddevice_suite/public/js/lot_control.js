// Lot control on the desk (modules 16, 17): QC verifies supplier certificates from the goods
// receipt, and QA opens a lot's decision from the lot. The server checks every rule again
// (lot_control/receipt.py, lot_control/release.py).
const RECEIPT = "meddevice_suite.lot_control.receipt";

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
		if (frm.is_new() || !frappe.user.has_role("Quality Manager")) return;
		if (["Quarantine", "Hold", "Approved"].includes(frm.doc.mds_lot_status)) {
			frm.add_custom_button(__("Decide Lot"), () =>
				frappe.new_doc("Lot Release", { batch: frm.doc.name })
			);
		}
	},
});
