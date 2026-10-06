// Purchase approval (D1 "Purchase Approval?") through the workflow buttons, as the
// purchasing and management job profiles (meddevice_suite/setup/workflows.py).
const PASSWORD = "Ap9!rTq4#wZ7mXv2"; // test users on the test site only
const PURCHASE = "cy.purchase@job-profiles.example.com";
const MANAGEMENT = "cy.management@job-profiles.example.com";
const ITEM = "MDS-UI-WORKFLOW-ITEM";
const SUPPLIER = "MDS UI Workflow Supplier";

function in_days(days) {
	const date = new Date();
	date.setDate(date.getDate() + days);
	return date.toISOString().slice(0, 10);
}

function setup() {
	cy.login();
	cy.visit("/desk");
	cy.insert_doc(
		"Item",
		{
			item_code: ITEM,
			item_name: "UI workflow test item",
			item_group: "Products",
			stock_uom: "Nos",
			is_stock_item: 0,
			gst_hsn_code: "90183910",
		},
		true
	);
	cy.insert_doc("Supplier", { supplier_name: SUPPLIER, supplier_group: "Raw Material" }, true);
	for (const [email, profile] of [
		[PURCHASE, "Purchase"],
		[MANAGEMENT, "Management"],
	]) {
		cy.insert_doc(
			"User",
			{
				email,
				first_name: profile,
				send_welcome_email: 0,
				new_password: PASSWORD,
				role_profiles: [{ role_profile: profile }],
			},
			true
		);
	}
}

function as_user(user) {
	cy.login(user, PASSWORD);
	cy.visit("/desk");
}

function open_order(name) {
	cy.visit(`/desk/purchase-order/${name}`);
	cy.get(".page-container:visible .indicator-pill").should("be.visible");
}

function take_action(action) {
	cy.get(".page-container:visible .actions-btn-group > button").click();
	cy.get(".page-container:visible .actions-btn-group .dropdown-menu a.dropdown-item")
		.contains(new RegExp(`^\\s*${action}\\s*$`))
		.click();
}

function raised_order() {
	as_user(PURCHASE);
	return cy.insert_doc("Purchase Order", {
		supplier: SUPPLIER,
		schedule_date: in_days(7),
		items: [{ item_code: ITEM, qty: 100, rate: 4, schedule_date: in_days(7) }],
	});
}

context("Purchase approval", () => {
	before(setup);

	it("[URS-CMP-020] purchasing sends an order for approval and management approves it", () => {
		raised_order().then((order) => {
			open_order(order.name);
			take_action("Send for Approval");
			cy.get(".page-container:visible .indicator-pill").should(
				"contain",
				"Awaiting Approval"
			);

			as_user(MANAGEMENT);
			open_order(order.name);
			take_action("Approve");
			cy.get(".page-container:visible .indicator-pill").should("contain", "Approved");
		});
	});

	it("[URS-CMP-021] the person who raised an order is not offered the approval", () => {
		raised_order().then((order) => {
			open_order(order.name);
			take_action("Send for Approval");
			cy.get(".page-container:visible .indicator-pill").should(
				"contain",
				"Awaiting Approval"
			);

			cy.get(".page-container:visible .actions-btn-group").should("not.be.visible");
		});
	});

	it("[URS-CMP-011] cancelling an approved order through the workflow asks for a reason", () => {
		raised_order().then((order) => {
			cy.call("frappe.model.workflow.apply_workflow", {
				doc: order,
				action: "Send for Approval",
			});

			as_user(MANAGEMENT);
			cy.call("frappe.model.workflow.apply_workflow", {
				doc: { doctype: "Purchase Order", name: order.name },
				action: "Approve",
			});
			open_order(order.name);
			take_action("Cancel");
			cy.get(".modal:visible")
				.last()
				.should("contain", `Reason for cancelling ${order.name}`);
			cy.get(".modal:visible")
				.last()
				.find('[data-fieldname="reason"] textarea')
				.type("Supplier cannot deliver");
			cy.get(".modal:visible").last().find(".btn-primary").contains("Continue").click();

			cy.get(".page-container:visible .indicator-pill").should("contain", "Cancelled");
			// Management may not read the audit trail; a reviewer checks it.
			cy.login();
			cy.visit("/desk");
			cy.call("frappe.client.get_list", {
				doctype: "Audit Event",
				filters: {
					reference_doctype: "Purchase Order",
					reference_name: order.name,
					action: "Cancelled",
				},
				fields: ["reason"],
			}).then((r) =>
				expect(r.message.map((event) => event.reason)).to.deep.equal([
					"Supplier cannot deliver",
				])
			);
		});
	});
});
