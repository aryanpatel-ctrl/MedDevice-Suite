// The audit trail's anchors (URS-CMP-040) and sales orders in the audit trail (URS-CMP-041),
// on the screens of the jobs that use them. The job users and the journey's masters (a product
// QA approved, a customer whose licence QA verified) come from the test-site helpers the
// standard flow uses (meddevice_suite/qa/ui_users.py, ui_journey.py).
const USERS = "meddevice_suite.qa.ui_users";
const JOURNEY = "meddevice_suite.qa.ui_journey";
const FORM = ".page-container:visible";

let password;
let users;
let data;

function as_job(job) {
	// Leaves the previous person's page first (as in standard_flow.js).
	cy.window({ log: false }).then((win) => win.location.replace("about:blank"));
	cy.location("href", { log: false }).should("eq", "about:blank");
	if (job === "Administrator") cy.login();
	else cy.login(users[job], password);
	cy.visit("/desk");
	cy.window()
		.its("frappe.session.user")
		.should("eq", job === "Administrator" ? "Administrator" : users[job]);
}

function open_dialog() {
	return cy.get(".modal:visible").last();
}

function give_reason(title, reason) {
	open_dialog().should("contain", title);
	cy.window().its("cur_dialog.display").should("eq", true);
	open_dialog().find('[data-fieldname="reason"] textarea').type(reason);
	cy.window()
		.its("cur_dialog")
		.should((dialog) => expect(dialog.get_value("reason")).to.equal(reason));
	open_dialog().find(".btn-primary").contains("Continue").click();
}

// A POST as the signed-in person that may be refused, returning the HTTP status.
function status_of(method) {
	return cy
		.window()
		.its("frappe.csrf_token")
		.then((csrf_token) =>
			cy.request({
				method: "POST",
				url: `/api/method/${method}`,
				headers: { Accept: "application/json", "X-Frappe-CSRF-Token": csrf_token },
				failOnStatusCode: false,
			})
		)
		.its("status");
}

function anchor_button(label) {
	return cy.get(`${FORM} button[data-label="${encodeURIComponent(label)}"]`);
}

context("Audit anchors and sales orders, on the screens of each job", () => {
	before(() => {
		cy.login();
		cy.visit("/desk");
		cy.call(`${USERS}.prepare_job_users`).then(({ message }) => {
			password = message.password;
			users = message.users;
		});
		cy.then(() => cy.call(`${JOURNEY}.prepare_journey`, { password })).then(({ message }) => {
			data = message;
		});
	});

	it("[URS-CMP-041] Update Items on a submitted sales order asks Sales for a reason, which the audit trail records", () => {
		const reason = "Customer ordered a second unit (browser test)";
		as_job("Sales");
		const delivery_date = new Date(Date.now() + 14 * 864e5).toISOString().slice(0, 10);
		cy.insert_doc("Sales Order", {
			customer: data.customer,
			company: data.company,
			delivery_date,
			docstatus: 1,
			items: [
				{
					item_code: data.product,
					qty: 1,
					rate: data.rate,
					delivery_date,
					warehouse: data.stores.finished_goods,
				},
			],
		}).then((order) => {
			cy.visit(`/desk/sales-order/${encodeURIComponent(order.name)}`);
			cy.window().its("cur_frm.doc.name").should("eq", order.name);
			anchor_button("Update Items").click();
			give_reason(`Reason for changing ${order.name}`, reason);

			// ERPNext's own dialog follows the reason.
			cy.window()
				.its("cur_dialog")
				.should((dialog) => expect(dialog.title).to.equal("Update Items"));
			cy.window().then((win) => {
				const table = win.cur_dialog.fields_dict.trans_items;
				table.df.data[0].qty = 2;
				table.grid.refresh();
			});
			open_dialog().find(".btn-primary").contains("Update").click();
			cy.window().its("cur_frm.doc.items.0.qty").should("eq", 2);

			as_job("Administrator");
			cy.call("frappe.client.get_list", {
				doctype: "Audit Event",
				filters: {
					reference_doctype: "Sales Order",
					reference_name: order.name,
					action: "Updated after submission",
				},
				fields: ["reason", "user"],
			})
				.its("message")
				.should("deep.equal", [{ reason, user: users["Sales"] }]);
		});
	});

	it("[URS-CMP-040] QA anchors the audit trail on request and verifies it from the Audit Anchor list", () => {
		as_job("QA Manager");
		cy.visit("/desk/audit-anchor");
		anchor_button("Anchor Now").should("be.visible").click();
		cy.get(".desk-alert:visible").should("contain", "Audit Event: anchor ANC-");
		cy.call("frappe.client.get_list", {
			doctype: "Audit Anchor",
			filters: { ledger: "Audit Event" },
			fields: ["trigger"],
			order_by: "creation desc",
			limit_page_length: 1,
		})
			.its("message.0.trigger")
			.should("be.oneOf", ["On request", "Scheduled"]);

		anchor_button("Verify Audit Trail").should("be.visible").click();
		open_dialog().should("contain", "Integrity verified");
		open_dialog().should(
			"contain",
			"No anchored entry of the audit trail or the signatures is missing."
		);
	});

	it("[URS-CMP-040] the system administrator has the anchor buttons too", () => {
		as_job("System Administrator");
		cy.visit("/desk/audit-anchor");
		anchor_button("Anchor Now").should("be.visible");
		anchor_button("Verify Audit Trail").should("be.visible");
	});

	it("[URS-CMP-040] a job outside QA and administration has no anchor buttons and cannot anchor", () => {
		as_job("Sales");
		cy.visit("/desk/audit-anchor");
		cy.get(FORM).should("exist");
		cy.get(`button[data-label="${encodeURIComponent("Anchor Now")}"]`).should("not.exist");
		cy.get(`button[data-label="${encodeURIComponent("Verify Audit Trail")}"]`).should(
			"not.exist"
		);
		status_of("meddevice_suite.compliance.audit_anchor.anchor_now").should("eq", 403);
		status_of("meddevice_suite.compliance.audit_anchor.verify_audit_trail").should("eq", 403);
	});
});
