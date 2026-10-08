// Stock by lot and status (module 17): filters by status, item and store.
frappe.query_reports["Stock by Lot Status"] = {
	filters: [
		{
			fieldname: "lot_status",
			label: __("Lot Status"),
			fieldtype: "Select",
			options: ["", "Quarantine", "Approved", "Rejected", "Hold"],
		},
		{ fieldname: "item_code", label: __("Item"), fieldtype: "Link", options: "Item" },
		{ fieldname: "warehouse", label: __("Store"), fieldtype: "Link", options: "Warehouse" },
	],
};
