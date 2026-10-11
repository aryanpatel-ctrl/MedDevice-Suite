// Equipment master (module 8), its calibration (module 38) and its history card (module 62) on the
// screen: QA records a piece of equipment and approves it on its form with a signature,
// Maintenance completes its calibration on the calibration's form with a signature, and its
// history card shows both (master_data/equipment.py, qms/calibration.py, equipment_history_card).
const USERS = "meddevice_suite.qa.ui_users";
const JOURNEY = "meddevice_suite.qa.ui_journey";
const FORM = ".page-container:visible";

let password, qa, maintenance, asset;

function as_user(user) {
	// Leaves the previous person's page first (as in standard_flow.js).
	cy.window({ log: false }).then((win) => win.location.replace("about:blank"));
	cy.location("href", { log: false }).should("eq", "about:blank");
	if (user === "Administrator") cy.login();
	else cy.login(user, password);
	cy.visit("/desk");
	cy.window().its("frappe.session.user").should("eq", user);
}

context("Equipment master", () => {
	before(() => {
		cy.login();
		cy.visit("/desk");
		cy.call(`${USERS}.prepare_job_users`).then(({ message }) => {
			password = message.password;
			qa = message.users["QA Manager"];
			maintenance = message.users["Maintenance"];
		});
		// The journey's masters: the approved instrument item, its category and location.
		cy.then(() => cy.call(`${JOURNEY}.prepare_journey`, { password }));
		cy.then(() => as_user(qa));
		cy.then(() => {
			const suffix = Date.now().toString(36).toUpperCase();
			cy.call("frappe.client.get_value", {
				doctype: "Asset",
				filters: { asset_name: "Digital Caliper (Demo)" },
				fieldname: ["item_code", "company", "location", "asset_category"],
			}).then(({ message: demo }) => {
				cy.insert_doc("Asset", {
					asset_name: `Caliper ${suffix}`,
					item_code: demo.item_code,
					company: demo.company,
					location: demo.location,
					purchase_date: new Date().toISOString().slice(0, 10),
					available_for_use_date: new Date().toISOString().slice(0, 10),
					asset_type: "Existing Asset",
					net_purchase_amount: 15000,
					asset_quantity: 1,
					calculate_depreciation: 0,
					mds_equipment_tag: `UI-CAL-${suffix}`,
					mds_equipment_class: "Measuring instrument",
					mds_manufacturer: "Demo Instruments",
					mds_model: "DC-150 (browser test)",
					mds_serial_number: `UI-${suffix}`,
					mds_affects_quality: 1,
					mds_needs_calibration: 1,
					mds_calibration_interval_days: 365,
				}).then((doc) => (asset = doc.name));
			});
		});
	});

	it("[URS-MST-010] QA approves the equipment on its form with a signature", () => {
		as_user(qa);
		cy.visit(`/desk/asset/${encodeURIComponent(asset)}`);
		cy.window().should((win) => {
			expect(win.cur_frm?.doctype).to.equal("Asset");
			expect(win.cur_frm.doc.__onload?.equipment_approval, "the approval shown").to.be.ok;
		});
		cy.get(`${FORM} .form-message`).should("contain", "Ready for QA's approval");
		cy.get(
			`${FORM} button[data-label="${encodeURIComponent("Approve Equipment")}"]:visible`
		).click();
		cy.window().should((win) => {
			expect(win.cur_dialog?.display, "the signing dialog has opened").to.equal(true);
			expect(win.cur_dialog.title).to.equal("Sign: Approve");
		});
		const reason = "Make, model, serial and calibration interval checked (browser test)";
		cy.get('.modal.show textarea[data-fieldname="reason"]').click().type(reason);
		cy.window()
			.its("cur_dialog")
			.should((dialog) => expect(dialog.get_value("reason")).to.equal(reason));
		cy.get('.modal.show input[data-fieldname="password"]')
			.click()
			.type(password, { log: false });
		cy.window()
			.its("cur_dialog")
			.should((dialog) => expect(dialog.get_value("password")).to.equal(password));
		cy.get(".modal:visible .btn-primary").contains("Sign").click();
		cy.window().should((win) =>
			expect(win.cur_frm.doc.mds_equipment_approval, "approved").to.equal("Approved")
		);
		cy.get(`${FORM} .form-message`).should("contain", "Equipment approved by QA");
	});

	it("[URS-EQP-001] Maintenance completes its calibration on the form with a signature", () => {
		as_user(maintenance);
		cy.call(`${JOURNEY}.draft_calibration`, { asset }).then(({ message: name }) => {
			cy.visit(`/desk/calibration-record/${encodeURIComponent(name)}`);
		});
		cy.window().should((win) => expect(win.cur_frm?.doctype).to.equal("Calibration Record"));
		cy.get(`${FORM} button[data-label="Complete"]:visible`).click();
		cy.window().should((win) => {
			expect(win.cur_dialog?.display, "the signing dialog has opened").to.equal(true);
			expect(win.cur_dialog.title).to.equal("Sign: Complete");
		});
		cy.get('.modal.show input[data-fieldname="password"]')
			.click()
			.type(password, { log: false });
		cy.window()
			.its("cur_dialog")
			.should((dialog) => expect(dialog.get_value("password")).to.equal(password));
		cy.get(".modal:visible .btn-primary").contains("Sign").click();
		cy.window().should((win) => {
			expect(win.cur_frm.doc.docstatus, "completed").to.equal(1);
			expect(win.cur_frm.doc.result).to.equal("Pass");
			expect(win.cur_frm.doc.next_due_date, "due again").to.be.ok;
		});
	});

	it("[URS-MST-011] the history card shows the approval", () => {
		as_user(qa);
		cy.visit(`/desk/query-report/Equipment History Card?asset=${encodeURIComponent(asset)}`);
		cy.get(".dt-scrollable", { timeout: 20000 }).should("contain", "Signed: Approved");
		cy.get(".dt-scrollable").should("contain", "Recorded");
		cy.get(".dt-scrollable").should("contain", "Calibration: Pass");
	});
});
