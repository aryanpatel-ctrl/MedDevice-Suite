// Lots in quarantine or on hold and what each waits for (module 16).
frappe.query_reports["Lots Awaiting Release"] = {
	filters: [{ fieldname: "item_code", label: __("Item"), fieldtype: "Link", options: "Item" }],
};
