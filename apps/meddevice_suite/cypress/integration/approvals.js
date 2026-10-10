// Purchase approval (D1 "Purchase Approval?") through the workflow buttons, as the
// purchasing and management job profiles (meddevice_suite/setup/workflows.py).
const PASSWORD = "Ap9!rTq4#wZ7mXv2"; // test users on the test site only
const PURCHASE = "cy.purchase@job-profiles.example.com";
const MANAGEMENT = "cy.management@job-profiles.example.com";
const QA = "cy.qa@job-profiles.example.com";
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
			mds_item_type: "Consumable",
		},
		true
	);
	cy.insert_doc("Supplier", { supplier_name: SUPPLIER, supplier_group: "Raw Material" }, true);
	for (const [email, profile] of [
		[PURCHASE, "Purchase"],
		[MANAGEMENT, "Management"],
		[QA, "QA Manager"],
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
	item_approved();
	supplier_approved();
}

// QA approves the item with a signature: only an approved item is ordered (module 1, URS-MST-003).
function item_approved() {
	as_user(QA);
	cy.call("frappe.client.get_value", {
		doctype: "Item",
		filters: { name: ITEM },
		fieldname: "mds_approval_status",
	}).then((r) => {
		if (r.message.mds_approval_status === "Approved") return;
		// An item made by an earlier run, before the item master, is given its type first.
		as_user("Administrator");
		cy.call("frappe.client.set_value", {
			doctype: "Item",
			name: ITEM,
			fieldname: "mds_item_type",
			value: "Consumable",
		});
		as_user(QA);
		cy.call("meddevice_suite.compliance.signing.sign_action", {
			doctype: "Item",
			name: ITEM,
			action: "Approve",
			password: PASSWORD,
			reason: "Approved for the browser test",
		});
	});
}

// QA approves the supplier for the item with a signature: purchase orders are placed only with
// an approved supplier (module 4, URS-PUR-001).
function supplier_approved() {
	as_user(QA);
	cy.insert_doc("File", {
		file_name: "ISO-13485-ui-workflow-supplier.txt",
		content: "ISO 13485 certificate (browser test)",
		is_private: 1,
	}).then((file) =>
		cy
			.insert_doc("Supplier Approval", {
				supplier: SUPPLIER,
				certificate_number: "QMS-UI-WORKFLOW",
				certificate_expiry: in_days(365),
				certificate: file.file_url,
				items: [{ item_code: ITEM }],
			})
			.then((approval) =>
				cy.call("meddevice_suite.compliance.signing.sign_action", {
					doctype: "Supplier Approval",
					name: approval.name,
					action: "Approve",
					password: PASSWORD,
					reason: "Approved for the browser test",
				})
			)
	);
}

// Leaves the previous person's page first: a page left open while the sessions change can end
// the new session on a slow machine (as in standard_flow.js).
function as_user(user) {
	cy.window({ log: false }).then((win) => win.location.replace("about:blank"));
	cy.location("href", { log: false }).should("eq", "about:blank");
	if (user === "Administrator") cy.login();
	else cy.login(user, PASSWORD);
	cy.visit("/desk");
	cy.window().its("frappe.session.user").should("eq", user);
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
			// Typed once the dialog has finished opening: Frappe then moves the focus to its first field.
			cy.window().its("cur_dialog.display").should("eq", true);
			cy.get(".modal:visible")
				.last()
				.find('[data-fieldname="reason"] textarea')
				.type("Supplier cannot deliver");
			// Frappe takes a typed value a moment after the keystrokes; go on once the dialog has it.
			cy.window()
				.its("cur_dialog")
				.should((dialog) =>
					expect(dialog.get_value("reason")).to.equal("Supplier cannot deliver")
				);
			cy.get(".modal:visible").last().find(".btn-primary").contains("Continue").click();

			cy.get(".page-container:visible .indicator-pill").should("contain", "Cancelled");
			// Management may not read the audit trail; a reviewer checks it.
			as_user("Administrator");
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
