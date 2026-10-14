// Item master (module 1) on the screen, as QA and Stores: a draft item is used in no record,
// QA approves it on its form with a signature, and from then on it is used; a finished good names
// the controlled document that defines it (plan D-34) (meddevice_suite/master_data/item_master.py).
const PASSWORD = "Im7!qRt2#vXw9pLz"; // test users on the test site only
const QA = "cy.item.qa@job-profiles.example.com";
const STORES = "cy.item.stores@job-profiles.example.com";
const FORM = ".page-container:visible";
const DOCUMENT = "its material or product specification"; // as the form lists it while missing

let item; // a new draft item each run: an approval is never undone
let finished_good; // a draft finished good still lacking its device data
let stores; // the approved store of the test company
let specification_document; // a Specification in effect, issued through document control

function as_user(user) {
	// Leaves the previous person's page first (as in standard_flow.js).
	cy.window({ log: false }).then((win) => win.location.replace("about:blank"));
	cy.location("href", { log: false }).should("eq", "about:blank");
	if (user === "Administrator") cy.login();
	else cy.login(user, PASSWORD);
	cy.visit("/desk");
	cy.window().its("frappe.session.user").should("eq", user);
}

function open_item(name) {
	cy.visit(`/desk/item/${encodeURIComponent(name)}`);
	cy.window().should((win) => {
		expect(win.cur_frm?.doctype).to.equal("Item");
		expect(win.cur_frm.doc.name).to.equal(name);
		expect(win.cur_frm.doc.__onload?.item_approval, "the approval shown").to.be.ok;
	});
}

function headline() {
	return cy.get(`${FORM} .form-message`); // Frappe's form message, where the banner is shown
}

function receipt_of(code) {
	return {
		doctype: "Stock Entry",
		stock_entry_type: "Material Receipt",
		items: [{ item_code: code, qty: 1, basic_rate: 1, t_warehouse: stores }],
	};
}

// A server call the signed-in user makes that must be refused; returns the refusal's text.
function refused_insert(doc) {
	return cy
		.window()
		.its("frappe.csrf_token")
		.then((csrf_token) =>
			cy.request({
				method: "POST",
				url: "/api/method/frappe.client.insert",
				body: { doc: JSON.stringify(doc) },
				headers: { Accept: "application/json", "X-Frappe-CSRF-Token": csrf_token },
				failOnStatusCode: false,
			})
		)
		.then((response) => {
			expect(response.status, "refused").to.be.within(400, 499);
			return JSON.stringify(response.body);
		});
}

function setup() {
	cy.login();
	cy.visit("/desk");
	for (const [email, profile] of [
		[QA, "QA Manager"],
		[STORES, "Stores"],
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
	cy.call("frappe.client.get_value", {
		doctype: "Warehouse",
		filters: { warehouse_name: "Stores" },
		fieldname: "name",
	}).then((r) => (stores = r.message.name));
	// Issued by the job users of the run, with their signatures (module 30).
	cy.call("meddevice_suite.qa.ui_users.prepare_job_users")
		.then(({ message }) =>
			cy.call("meddevice_suite.qa.ui_journey.prepare_effective_document", {
				password: message.password,
				document_type: "Specification",
			})
		)
		.then(({ message }) => (specification_document = message));
	const suffix = Date.now().toString(36).toUpperCase();
	// A sub-assembly: lot-tracked with its shelf life and storage, inspected later in the process.
	cy.insert_doc("Item", {
		item_code: `MDS-UI-ITEM-${suffix}`,
		item_name: "Catheter shaft, extruded (browser test)",
		item_group: "Raw Material",
		mds_item_type: "Sub-assembly (WIP)",
		stock_uom: "Nos",
		gst_hsn_code: "39269099",
		is_stock_item: 1,
		has_batch_no: 1,
		create_new_batch: 1,
		batch_number_series: `UIIT${suffix}-.####`,
		has_expiry_date: 1,
		shelf_life_in_days: 365,
		mds_storage_condition: "Below 30 °C, dry (browser test)",
	}).then((doc) => (item = doc.name));
	cy.insert_doc("Item", {
		item_code: `MDS-UI-FG-${suffix}`,
		item_name: "Foley catheter without its device data (browser test)",
		item_group: "Products",
		mds_item_type: "Finished Good",
		stock_uom: "Nos",
		gst_hsn_code: "90183910",
		is_stock_item: 1,
		has_batch_no: 1,
		create_new_batch: 1,
		batch_number_series: `UIFG${suffix}-.####`,
		has_expiry_date: 1,
		shelf_life_in_days: 1825,
		mds_storage_condition: "Below 30 °C (browser test)",
	}).then((doc) => (finished_good = doc.name));
}

context("Item master", () => {
	before(setup);

	it("[URS-MST-003] Stores cannot use an item QA has not approved", () => {
		as_user(STORES);
		refused_insert(receipt_of(item)).then((text) =>
			expect(text).to.contain("Not yet approved by QA")
		);
	});

	it("[URS-MST-005] the form of a finished good says what it still needs before QA can approve it", () => {
		as_user(QA);
		open_item(finished_good);
		headline()
			.should("contain", "Not yet approved by QA")
			.and("contain", "device class")
			.and("contain", "UDI device identifier")
			.and("contain", "final specification")
			.and("contain", DOCUMENT);
		cy.get(`${FORM} button[data-label="${encodeURIComponent("Approve Item")}"]`).should(
			"not.exist"
		);
	});

	it("[URS-MST-016] QA names the document that defines the finished good on its form", () => {
		as_user(QA);
		open_item(finished_good);
		headline().should("contain", DOCUMENT);
		cy.window().then((win) =>
			win.cur_frm.set_value("mds_specification_document", specification_document)
		);
		cy.window()
			.its("cur_frm.doc.mds_specification_document")
			.should("eq", specification_document);
		cy.window().then((win) => win.cur_frm.save());
		cy.window().should((win) => expect(win.cur_frm.is_dirty(), "saved").to.equal(false));
		open_item(finished_good); // loaded again, with what its approval now needs
		headline().should("contain", "Not yet approved by QA").and("not.contain", DOCUMENT);
	});

	it("[URS-MST-003] QA approves the item on its form with a signature", () => {
		as_user(QA);
		open_item(item);
		headline().should("contain", "Ready for QA's approval");
		cy.get(
			`${FORM} button[data-label="${encodeURIComponent("Approve Item")}"]:visible`
		).click();
		cy.window().should((win) => {
			expect(win.cur_dialog?.display, "the signing dialog has opened").to.equal(true);
			expect(win.cur_dialog.title).to.equal("Sign: Approve");
		});
		cy.get('.modal.show textarea[data-fieldname="reason"]')
			.click()
			.type("Item data and storage checked (browser test)");
		cy.window()
			.its("cur_dialog")
			.should((dialog) =>
				expect(dialog.get_value("reason")).to.equal(
					"Item data and storage checked (browser test)"
				)
			);
		cy.get('.modal.show input[data-fieldname="password"]')
			.click()
			.type(PASSWORD, { log: false });
		cy.window()
			.its("cur_dialog")
			.should((dialog) => expect(dialog.get_value("password")).to.equal(PASSWORD));
		cy.get(".modal:visible .btn-primary").contains("Sign").click();
		cy.window().should((win) => {
			expect(win.cur_frm.doc.mds_approval_status, "approved").to.equal("Approved");
			expect(win.cur_frm.doc.mds_approved_by).to.equal(QA);
		});
		headline().should("contain", "Approved by QA");
		cy.get(`${FORM} button[data-label="${encodeURIComponent("Approve Item")}"]`).should(
			"not.exist"
		);
	});

	it("[URS-MST-003] once approved, Stores uses the item", () => {
		as_user(STORES);
		cy.call("frappe.client.insert", { doc: receipt_of(item) }).then((r) =>
			expect(r.message.items[0].item_code).to.equal(item)
		);
	});
});
