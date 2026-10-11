// Training status (module 37): each employee's training on what their job needs.
frappe.query_reports["Training Status"] = {
	filters: [
		{ fieldname: "employee", label: __("Employee"), fieldtype: "Link", options: "Employee" },
	],
};
